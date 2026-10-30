"""Sign-in checks never queue behind slow work on the shared thread pool
(Codex round 1 on d093295; 28 Oct live: 1.8 s in auth-ready, pool 10/10)."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

from starlette.requests import Request

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
