import asyncio
import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from server.auth import Auth, AuthError, COOKIE, DAY
from server.auth_logging import CredentialFilter, install_validation_redaction
from server.routes.auth import auth_router
from server.store import Store

PASSWORD = "fixture password only"


@pytest.fixture
def fixture(tmp_path):
    clock = [1700000000]
    store = Store(tmp_path)
    auth = Auth(store, lambda: clock[0])
    owner = auth.create_owner("mark", PASSWORD, "Mark")
    return auth, owner, clock


def token(auth, name="mark", password=PASSWORD, kind="device", ip="fixture"):
    return auth.login(
        name, password, kind, "fixture phone", "ios" if kind == "device" else "web", ip
    )["token"]


def test_owner_bootstrap_hash_and_single_owner(fixture):
    auth, owner, _ = fixture
    assert auth.ready()
    assert auth.user(owner)["password_hash"].startswith("$argon2id$")
    with pytest.raises(AuthError, match="owner_already_exists"):
        auth.create_owner("other", PASSWORD)
    with auth.store.connect() as conn:
        assert PASSWORD not in "\n".join(conn.iterdump())


def test_token_hash_only_revocation_and_session_scoping(fixture):
    auth, owner, _ = fixture
    first = token(auth)
    second = token(auth)
    person, session = auth.authenticate(first)
    assert person["id"] == owner
    assert "password_hash" not in person
    listed = auth.sessions(owner)
    assert all(len(row["id"]) == 36 for row in listed)
    assert session["id"] not in json.dumps(listed)
    with auth.store.connect() as conn:
        assert first not in "\n".join(conn.iterdump())
        assert second not in "\n".join(conn.iterdump())
    auth.revoke(owner, session["public_id"])
    with pytest.raises(AuthError):
        auth.authenticate(first)
    auth.authenticate(second)
    with pytest.raises(AuthError, match="session_not_found"):
        auth.revoke("another-user", listed[0]["id"])


def test_web_and_device_expiry_hourly_writes(fixture):
    auth, _, clock = fixture
    web = token(auth, kind="web")
    device = token(auth)
    _, s1 = auth.authenticate(device)
    clock[0] += 60
    _, s2 = auth.authenticate(device)
    assert s1["last_seen_at"] == s2["last_seen_at"]
    clock[0] += 3600
    auth.authenticate(device)
    _, s3 = auth.authenticate(device)
    assert s3["last_seen_at"] == clock[0]
    clock[0] += 15 * DAY
    with pytest.raises(AuthError):
        auth.authenticate(web)
    auth.authenticate(device)
    clock[0] += 181 * DAY
    with pytest.raises(AuthError):
        auth.authenticate(device)


def test_session_renewal_never_waits_for_another_writer(fixture):
    """#26: the hourly renewal is a write on the reserved sign-in threads; it
    waited up to 10 s for a busy database (then failed the sign-in check).
    It is skipped instead, and the next request renews."""
    import sqlite3
    import time

    auth, owner, clock = fixture
    device = token(auth)
    clock[0] += 3601
    writer = sqlite3.connect(auth.store.database, timeout=0)
    writer.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        person, session = auth.authenticate(device)
        assert time.monotonic() - started < 1
        assert person["id"] == owner
    finally:
        writer.rollback()
        writer.close()
    _, session = auth.authenticate(device)
    _, renewed = auth.authenticate(device)
    assert renewed["last_seen_at"] == clock[0]


def test_web_absolute_limit_despite_activity(fixture):
    auth, _, clock = fixture
    web = token(auth, kind="web")
    for _ in range(5):
        clock[0] += 10 * DAY
        auth.authenticate(web)
    clock[0] += 10 * DAY
    with pytest.raises(AuthError):
        auth.authenticate(web)


def test_setup_one_use_expiry_reset_and_disabled_user(fixture):
    auth, owner, clock = fixture
    setup = auth.setup_code(owner, "member", "Household member")
    with auth.store.connect() as conn:
        assert setup["code"] not in "\n".join(conn.iterdump())
    member = auth.activate(setup["code"], PASSWORD, "fixture2")
    device = token(auth, "member")
    with pytest.raises(AuthError, match="invalid_setup_code"):
        auth.activate(setup["code"], PASSWORD, "fixture2")
    reset = auth.setup_code(owner, identity=member["id"])
    with pytest.raises(AuthError):
        auth.authenticate(device)
    clock[0] += DAY
    with pytest.raises(AuthError, match="invalid_setup_code"):
        auth.activate(reset["code"], PASSWORD, "fixture2")
    reset = auth.setup_code(owner, identity=member["id"])
    auth.activate(reset["code"], PASSWORD, "fixture2")
    device = token(auth, "member")
    with auth.store.connect() as conn:
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (member["id"],))
    with pytest.raises(AuthError):
        auth.authenticate(device)


def test_password_change_keeps_current_and_revokes_others(fixture):
    auth, _, _ = fixture
    first, second = token(auth), token(auth)
    person, session = auth.authenticate(first)
    auth.change_password(person, session, PASSWORD, "new fixture password")
    auth.authenticate(first)
    with pytest.raises(AuthError):
        auth.authenticate(second)
    token(auth, password="new fixture password")
    auth.reset_password("mark", PASSWORD)
    with pytest.raises(AuthError):
        auth.authenticate(first)


def test_login_limits_unknown_user_same_errors_and_exponential_lock(fixture):
    auth, _, clock = fixture
    # The owner's usual address, which the account-wide slow-down skips (D-49).
    assert token(auth, ip="another-ip")
    for cycle, seconds in enumerate([60, 120, 240]):
        for _ in range(5):
            with pytest.raises(AuthError, match="invalid_credentials"):
                token(auth, password="wrong fixture", ip="bad-ip")
        with auth.store.connect() as conn:
            assert (
                conn.execute(
                    "SELECT locked_until FROM login_attempts WHERE key=?",
                    (auth.keys("mark", "bad-ip")[0],),
                ).fetchone()[0]
                == clock[0] + seconds
            )
        # The failing address stays locked even with the right password, but
        # nobody else can lock the owner out by guessing at their name.
        with pytest.raises(AuthError, match="invalid_credentials"):
            token(auth, ip="bad-ip")
        assert token(auth, ip="another-ip")
        clock[0] += seconds + 1
    for _ in range(5):
        with pytest.raises(AuthError, match="invalid_credentials"):
            token(auth, name="missing-user", ip="unknown-ip")
    with pytest.raises(AuthError, match="invalid_credentials"):
        token(auth, name="another-missing", ip="unknown-ip")


def account_delay(auth, clock, name="mark"):
    with auth.store.connect() as conn:
        found = conn.execute(
            "SELECT locked_until FROM login_attempts WHERE key=?",
            (auth.account_key(name),),
        ).fetchone()
    return max(0, found[0] - clock[0]) if found else 0


def test_guessing_one_account_from_many_addresses_slows_down_and_alerts(fixture):
    """D-49: wrong passwords for one account, from any address, slow its
    sign-ins by a growing delay (no hard lockout) and alert the owner once a
    day; the owner's recent addresses skip the delay."""
    auth, owner, clock = fixture
    assert token(auth, ip="home")
    addresses = iter(f"guesser-{i}" for i in range(1000))

    def guess():
        with pytest.raises(AuthError, match="invalid_credentials"):
            token(auth, password="wrong fixture", ip=next(addresses))

    for _ in range(4):
        guess()
    assert account_delay(auth, clock) == 0
    delays = []
    for _ in range(9):
        guess()
        delays.append(account_delay(auth, clock))
        # Too soon: refused unchecked, without stretching the delay.
        before = account_delay(auth, clock)
        with pytest.raises(AuthError, match="invalid_credentials"):
            token(auth, ip="cafe")
        assert account_delay(auth, clock) == before
        assert token(auth, ip="home")  # The owner's own address never waits.
        clock[0] += delays[-1]
    assert delays == [1, 2, 4, 8, 16, 32, 60, 60, 60]
    assert token(auth, ip="cafe")  # Once the delay passes, nobody is locked out.
    with auth.store.connect() as conn:
        alerts = conn.execute(
            "SELECT user_id FROM auth_events WHERE event='sign_in_slowed'"
        ).fetchall()
    assert [tuple(a) for a in alerts] == [(owner,)]
    clock[0] += DAY
    guess()
    assert account_delay(auth, clock) == 0  # A quiet day starts afresh.


def test_guessing_for_days_renews_the_owner_warning_daily(fixture):
    """Guessing that never pauses for a day keeps one account row growing; the
    owner is still warned every day, and the warning outlives a feed flooded
    with failed sign-ins (Codex round 2 on 60f84e1)."""
    auth, owner, clock = fixture
    start = clock[0]
    for i in range(36):  # One guess every two hours for three days.
        with pytest.raises(AuthError, match="invalid_credentials"):
            token(auth, password="wrong fixture", ip=f"guesser-{i}")
        clock[0] += 2 * 3600
    with auth.store.connect() as conn:
        alerts = [
            row[0]
            for row in conn.execute(
                "SELECT at FROM auth_events WHERE event='sign_in_slowed' ORDER BY id"
            )
        ]
    assert [(at - start) // 3600 for at in alerts] == [18, 42, 66]
    for i in range(250):  # Refused attempts each add a feed event.
        auth.failure("mark", f"flood-{i}")
    assert all(e["event"] != "sign_in_slowed" for e in auth.activity())
    bearer = {"Authorization": "Bearer " + token(auth, ip="home")}
    with TestClient(app_for(auth), base_url="https://private.ts.net") as client:
        listed = client.get("/api/v1/people/activity", headers=bearer).json()
    assert listed["warnings"] == [
        {
            "at": alerts[-1],
            "user_id": owner,
            "event": "sign_in_slowed",
            "display_name": "Mark",
        }
    ]
    clock[0] = alerts[-1] + 7 * DAY + 1
    assert auth.warnings() == []


def test_guessing_an_unknown_name_slows_it_without_an_alert(fixture):
    auth, _, clock = fixture
    for i in range(12):
        with pytest.raises(AuthError, match="invalid_credentials"):
            token(auth, name="nobody", password="wrong fixture", ip=f"a{i}")
        clock[0] += 60
    assert account_delay(auth, clock, "nobody") == 0
    with auth.store.connect() as conn:
        assert not conn.execute(
            "SELECT 1 FROM auth_events WHERE event='sign_in_slowed'"
        ).fetchone()


def test_ipv6_addresses_count_per_64_and_ipv4_per_address(fixture):
    auth, _, clock = fixture
    assert token(auth, ip="2001:db8:5:6::1")  # The owner's home network.
    # One host rotating through its /64 is one address to the limits.
    for i in range(5):
        with pytest.raises(AuthError, match="invalid_credentials"):
            token(auth, password="wrong fixture", ip=f"2001:db8:1:2::{i + 10:x}")
    # Past the account's own short delay (D-49), inside the address lock.
    clock[0] += account_delay(auth, clock) + 1
    with pytest.raises(AuthError, match="invalid_credentials"):
        token(auth, ip="2001:db8:1:2:ffff:ffff:ffff:ffff")
    assert token(auth, ip="2001:db8:1:3::10")  # The next /64 is not locked.
    # IPv4 stays per address; mapped IPv4 is the same address.
    for _ in range(5):
        with pytest.raises(AuthError, match="invalid_credentials"):
            token(auth, password="wrong fixture", ip="192.0.2.1")
    clock[0] += account_delay(auth, clock) + 1
    with pytest.raises(AuthError, match="invalid_credentials"):
        token(auth, ip="::ffff:192.0.2.1")
    assert token(auth, ip="192.0.2.2")
    # While the account is slowed, a new privacy address in the owner's
    # known /64 still skips the delay.
    with pytest.raises(AuthError, match="invalid_credentials"):
        token(auth, password="wrong fixture", ip="198.51.100.1")
    assert account_delay(auth, clock) > 0
    assert token(auth, ip="2001:db8:5:6:a:b:c:d")


@pytest.mark.parametrize(
    "password",
    [
        "short",
        "password1234",
        "123456789012",
        "x" * 257,
        # From the common-password lists, in any case.
        "q1w2e3r4t5y6",
        "1QAZ2WSX3EDC",
        "qwertyuiop[]",
        "justinbieber",
        # A short run repeated.
        "abcabcabcabc",
        "12121212121212",
        "ZZZZZZZZZZZZZ",
    ],
)
def test_weak_or_unbounded_password_rejected(fixture, password):
    auth, _, _ = fixture
    with pytest.raises(AuthError, match="password_not_accepted"):
        auth.create_owner("another", password)


def test_ordinary_passphrases_are_accepted():
    from server.auth import COMMON_PASSWORDS, validate_password

    assert len(COMMON_PASSWORDS) > 1000
    assert all(12 <= len(p) and p == p.casefold() for p in COMMON_PASSWORDS)
    for password in [PASSWORD, "abcd1abcd2abcd3", "correct horse battery"]:
        validate_password(password, "mark")


def app_for(auth, origin=None):
    app = FastAPI()
    install_validation_redaction(app)
    app.include_router(auth_router(auth, origin=origin))
    return app


def test_invites_carry_the_code_in_the_link_fragment_and_activation_returns_the_user(
    fixture,
):
    auth, _, _ = fixture
    owner = token(auth)
    bearer = {"Authorization": "Bearer " + owner}
    origin = "https://music.example.test"
    with TestClient(app_for(auth, origin), base_url=origin) as client:
        added = client.post(
            "/api/v1/people",
            headers=bearer,
            json={"username": "sam", "display_name": "Sam"},
        ).json()
        assert added["invite_url"] == f"{origin}/#/join?code={added['code']}"
        reset = client.post(
            f"/api/v1/people/{added['user_id']}/setup-code", headers=bearer
        ).json()
        assert reset["invite_url"] == f"{origin}/#/join?code={reset['code']}"
        assert reset["code"] != added["code"]
        activated = client.post(
            "/api/v1/setup",
            headers={"X-Pig-Request": "1"},
            json={"code": reset["code"], "password": PASSWORD},
        )
        assert activated.json() == {
            "user": {
                "id": added["user_id"],
                "username": "sam",
                "display_name": "Sam",
                "role": "member",
            }
        }
    with TestClient(app_for(auth), base_url=origin) as client:
        unconfigured = client.post(
            "/api/v1/people",
            headers=bearer,
            json={"username": "alex", "display_name": "Alex"},
        ).json()
        assert unconfigured["invite_url"] is None and unconfigured["code"]


def test_people_pages_read_the_database_off_the_event_loop(fixture, monkeypatch):
    auth, owner, _ = fixture
    bearer = {"Authorization": "Bearer " + token(auth)}
    connect, on_loop = auth.store.connect, []

    def watched():
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            pass
        return connect()

    monkeypatch.setattr(auth.store, "connect", watched)
    with TestClient(app_for(auth), base_url="https://private.ts.net") as client:
        people = client.get("/api/v1/people", headers=bearer).json()["people"]
        events = client.get("/api/v1/people/activity", headers=bearer).json()
    assert [p["id"] for p in people] == [owner]
    assert events["events"][0]["event"] == "login"
    assert not on_loop, "People pages queried SQLite on the event loop"


def test_https_cookie_flags_csrf_and_device_bearer(fixture):
    auth, owner, _ = fixture
    with TestClient(
        app_for(auth), base_url="https://private.ts.net", headers={"X-Pig-Request": "1"}
    ) as client:
        response = client.post(
            "/api/v1/auth/login", json={"username": "mark", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        cookie = response.headers["set-cookie"]
        assert (
            COOKIE in cookie
            and "HttpOnly" in cookie
            and "Secure" in cookie
            and "SameSite=strict" in cookie
            and "Path=/" in cookie
        )
        assert "token" not in response.json()
        assert client.get("/api/v1/me").json()["id"] == owner
        result = client.post(
            "/api/v1/auth/device",
            json={
                "username": "mark",
                "password": PASSWORD,
                "device_name": "test phone",
                "platform": "ios",
            },
        )
        device = result.json()["token"]
        assert (
            client.get(
                "/api/v1/me", headers={"Authorization": "Bearer " + device}
            ).status_code
            == 200
        )
        client.headers.pop("X-Pig-Request")
        assert client.post("/api/v1/auth/logout").status_code == 403
        assert (
            client.post(
                "/api/v1/auth/logout", headers={"Authorization": "Bearer " + device}
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/v1/me", headers={"Authorization": "Bearer " + device}
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/auth/login",
                headers={"X-Pig-Request": "1", "Origin": "https://evil.example"},
                json={"username": "mark", "password": PASSWORD},
            ).status_code
            == 403
        )
    with TestClient(app_for(auth)) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "mark", "password": PASSWORD}
            ).status_code
            == 400
        )


def test_members_cannot_issue_accounts_and_errors_do_not_echo_secrets(fixture, caplog):
    auth, owner, _ = fixture
    setup = auth.setup_code(owner, "member", "Member")
    auth.activate(setup["code"], PASSWORD, "fixture2")
    device = token(auth, "member")
    with TestClient(app_for(auth), base_url="https://private.ts.net") as client:
        assert (
            client.post(
                "/api/v1/people",
                headers={"Authorization": "Bearer " + device},
                json={"username": "extra", "display_name": "Extra"},
            ).status_code
            == 403
        )
        response = client.post(
            "/api/v1/setup",
            headers={"X-Pig-Request": "1"},
            json={
                "code": "sensitive invalid code",
                "password": "sensitive invalid password" * 20,
            },
        )
        assert response.status_code == 422
        assert "sensitive invalid" not in response.text
    assert (
        PASSWORD not in caplog.text
        and device not in caplog.text
        and setup["code"] not in caplog.text
    )


def test_credential_filter_redacts_diagnostic_fields():
    record = logging.LogRecord(
        "fixture",
        logging.INFO,
        "",
        1,
        {
            "password": "secret-value",
            "nested": {
                "Authorization": "Bearer secret-value",
                "Cookie": "secret-value",
                "token": "secret-value",
                "code": "secret-value",
            },
        },
        (),
        None,
    )
    CredentialFilter().filter(record)
    assert "secret-value" not in record.getMessage()
    text = logging.LogRecord(
        "fixture",
        logging.INFO,
        "",
        1,
        "POST /api?token=secret-value&sig=secret-signature Cookie=secret-cookie",
        (),
        None,
    )
    CredentialFilter().filter(text)
    assert (
        "secret-value" not in text.getMessage()
        and "secret-signature" not in text.getMessage()
        and "secret-cookie" not in text.getMessage()
    )


def test_devices_are_named_marked_current_and_others_can_be_signed_out(fixture):
    auth, owner, _ = fixture
    phone = token(auth)
    watch = token(auth)
    with TestClient(
        app_for(auth), base_url="https://private.ts.net", headers={"X-Pig-Request": "1"}
    ) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "mark", "password": PASSWORD},
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
            },
        )
        assert response.status_code == 200
        sessions = client.get("/api/v1/me/sessions").json()["sessions"]
        names = {s["device_name"]: s for s in sessions}
        assert names["Chrome on Mac"]["current"] is True
        assert names["Chrome on Mac"]["platform"] == "web"
        assert sum(s["current"] for s in sessions) == 1
        assert all(s["last_seen_at"] for s in sessions)
        # The full agent string is not kept.
        assert "AppleWebKit" not in str(sessions)
        assert client.delete("/api/v1/me/sessions").json() == {"revoked": 2}
        assert client.get("/api/v1/me").status_code == 200
        for other in (phone, watch):
            denied = client.get(
                "/api/v1/me", headers={"Authorization": "Bearer " + other}
            )
            # A bearer token is checked before the cookie.
            assert denied.status_code == 401
        assert client.delete("/api/v1/me/sessions").json() == {"revoked": 0}
