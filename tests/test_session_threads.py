"""Sign-in checks never queue behind slow work on the shared thread pool
(Codex round 1 on d093295; 28 Oct live: 1.8 s in auth-ready, pool 10/10)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

from fastapi.testclient import TestClient
from starlette.requests import Request

from server.auth import AuthError

from test_security import PASSWORD, protected


def request(token):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/me",
            "raw_path": b"/api/v1/me",
            "query_string": b"",
            "headers": [(b"authorization", b"Bearer " + token.encode())],
            "scheme": "https",
            "server": ("private.ts.net", 443),
            "root_path": "",
        }
    )


def test_sign_in_answers_while_slow_work_fills_the_shared_pool(tmp_path, monkeypatch):
    app, owner = protected(tmp_path, monkeypatch)
    token = app.state.auth.login("mark", PASSWORD, "device", "phone", "ios", "f")[
        "token"
    ]
    security = app.state.security
    release = threading.Event()

    async def run():
        loop = asyncio.get_running_loop()
        shared = ThreadPoolExecutor(max_workers=2)
        loop.set_default_executor(shared)
        # Slow NAS resolutions (say) occupy every shared worker.
        busy = [asyncio.to_thread(release.wait, 10) for _ in range(2)]
        tasks = [asyncio.ensure_future(job) for job in busy]
        await asyncio.sleep(0.05)
        try:
            return await asyncio.wait_for(security.current_user(request(token)), 1)
        finally:
            release.set()
            await asyncio.gather(*tasks)
            shared.shutdown()

    person = asyncio.run(run())
    assert person["id"] == owner


def test_account_pages_answer_while_anonymous_sign_ins_fill_the_password_gate(
    tmp_path, monkeypatch
):
    """Wrong-password attempts (below the lockout) must not queue signed-in
    account requests behind their password hashing (audit S3)."""
    app, owner = protected(tmp_path, monkeypatch)
    auth = app.state.auth
    token = auth.login("mark", PASSWORD, "device", "phone", "ios", "f")["token"]
    hashing, release = threading.Semaphore(0), threading.Event()

    def slow_login(*args):
        hashing.release()
        release.wait(10)
        raise AuthError("invalid_credentials")

    monkeypatch.setattr(auth, "login", slow_login)
    with TestClient(app, base_url="https://private.ts.net") as client:
        attempts = [
            threading.Thread(
                target=client.post,
                args=("/api/v1/auth/login",),
                kwargs={
                    "json": {"username": "mark", "password": "wrong password"},
                    "headers": {"X-Pig-Request": "1"},
                },
            )
            for _ in range(3)
        ]
        for attempt in attempts:
            attempt.start()
        try:
            # Two attempts are hashing; the third waits for the gate.
            assert hashing.acquire(timeout=5) and hashing.acquire(timeout=5)
            answer = {}
            me = threading.Thread(
                target=lambda: answer.update(
                    response=client.get(
                        "/api/v1/me", headers={"Authorization": "Bearer " + token}
                    )
                )
            )
            me.start()
            me.join(3)
            assert "response" in answer, "/api/v1/me waited behind password hashing"
            assert answer["response"].json()["id"] == owner
        finally:
            release.set()
            for attempt in attempts:
                attempt.join(10)
