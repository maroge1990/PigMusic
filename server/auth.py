"""Argon2id accounts and hashed, immediately revocable opaque sessions.

This module never logs credentials. Public session IDs differ from stored token
hashes. Call password work off the event loop using a bounded worker gate.
"""

import hashlib
import hmac
import re
import secrets
import time
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

DAY = 86400
# Wrong passwords for one account from any address (D-49): after this many in
# a day its sign-ins wait a growing delay, and at the alert count the owner is
# told. Never a hard lockout, and the account's recent addresses don't wait.
ACCOUNT_FREE_FAILURES = 5
ACCOUNT_MAX_DELAY = 60
ACCOUNT_ALERT_FAILURES = 10
COOKIE = "__Host-pig_session"
COMMON_PASSWORDS = frozenset(
    {
        "password1234",
        "123456789012",
        "qwerty123456",
        "passwordpassword",
        "letmeinletmein",
        "iloveyou12345",
        "abcdefghijkl",
        "111111111111",
        "1234567890123",
        "12345678901234",
    }
)
HASHER = PasswordHasher()  # Argon2id library defaults (64MiB, three iterations).
DUMMY_HASH = HASHER.hash(secrets.token_urlsafe(32))


def browser_name(user_agent: str) -> str:
    """A person-readable name for a web session, e.g. "Safari on iPhone".

    Only the browser family and device kind are kept; the full agent string
    is never stored.
    """
    agent = user_agent or ""
    browser = next(
        (
            name
            for token, name in (
                ("Edg", "Edge"),
                ("OPR/", "Opera"),
                ("FxiOS", "Firefox"),
                ("Firefox/", "Firefox"),
                ("CriOS", "Chrome"),
                ("Chrome/", "Chrome"),
                ("Safari/", "Safari"),
            )
            if token in agent
        ),
        "",
    )
    system = next(
        (
            name
            for token, name in (
                ("iPhone", "iPhone"),
                ("iPad", "iPad"),
                ("Android", "Android"),
                ("Windows", "Windows"),
                ("CrOS", "ChromeOS"),
                ("Macintosh", "Mac"),
                ("Linux", "Linux"),
            )
            if token in agent
        ),
        "",
    )
    if browser and system:
        return f"{browser} on {system}"
    return browser or (f"Browser on {system}" if system else "Browser")


class AuthError(ValueError):
    def __init__(self, code, status=401):
        super().__init__(code)
        self.code, self.status = code, status


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def username(value):
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", value):
        raise AuthError("invalid_username", 422)
    return value


def validate_password(value, name):
    if (
        not isinstance(value, str)
        or not 12 <= len(value) <= 256
        or value.casefold() in COMMON_PASSWORDS
        or value.casefold() == name.casefold()
    ):
        raise AuthError("password_not_accepted", 422)


def public_user(row):
    return {key: row[key] for key in ("id", "username", "display_name", "role")}


def records(cursor):
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


class Auth:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def now(self):
        return int(self.clock())

    def ready(self):
        with self.store.connect() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM users WHERE role='owner' AND disabled=0 AND password_hash IS NOT NULL"
                ).fetchone()
                is not None
            )

    def has_owner(self):
        # Disabling an owner must never reopen first-run registration.
        with self.store.connect() as conn:
            return (
                conn.execute("SELECT 1 FROM users WHERE role='owner'").fetchone()
                is not None
            )

    def claim_owner(
        self, code, expected, name, password, display_name, ip, device_name="Browser"
    ):
        if self.has_owner():
            raise AuthError("owner_already_exists", 409)
        if self.locked("first-owner", ip):
            raise AuthError("rate_limited", 429)
        if not hmac.compare_digest(digest(code), digest(expected)):
            self.failure("first-owner", ip, "owner_setup_failed")
            raise AuthError("invalid_installation_code", 403)
        if not display_name.strip():
            raise AuthError("invalid_display_name", 422)
        self.create_owner(name, password, display_name)
        return self.login(name, password, "web", device_name, "web", ip)

    def create_owner(self, name, password, display_name=None):
        name = username(name)
        validate_password(password, name)
        hashed = HASHER.hash(password)
        now = self.now()
        identity = str(uuid.uuid4())
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM users WHERE role='owner'").fetchone():
                raise AuthError("owner_already_exists", 409)
            conn.execute(
                "INSERT INTO users VALUES (?,?,?,?,?,?,0,?)",
                (identity, name, display_name or name, "owner", hashed, now, now),
            )
        from .user_library import initialise_owner

        initialise_owner(self.store)
        return identity

    def user(self, identity):
        with self.store.connect() as conn:
            found = records(conn.execute("SELECT * FROM users WHERE id=?", (identity,)))
        if not found or found[0]["disabled"]:
            raise AuthError("authentication_required")
        return found[0]

    def event(self, conn, event, ip, identity=None):
        conn.execute(
            "INSERT INTO auth_events(at,user_id,event,ip,detail) VALUES (?,?,?,?,?)",
            (self.now(), identity, event, ip[:128], ""),
        )

    def keys(self, name, ip):
        # A name locks only for the address that failed: on the internet, anyone
        # knowing a username could otherwise keep that person from signing in.
        return (self.name_key(name) + ":" + digest(ip), "ip:" + digest(ip))

    def name_key(self, name):
        return "name:" + digest(name.strip().casefold())

    def account_key(self, name):
        return "account:" + digest(name.strip().casefold())

    def known_key(self, name, ip):
        """An address that signed in to this account in the last 30 days."""
        return "known:" + digest(name.strip().casefold()) + ":" + digest(ip)

    def slowed(self, name, ip):
        """Whether this sign-in must wait out the account's delay (D-49)."""
        with self.store.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM login_attempts WHERE key=?", (self.known_key(name, ip),)
            ).fetchone():
                return False
            return bool(
                conn.execute(
                    "SELECT 1 FROM login_attempts WHERE key=? AND locked_until>?",
                    (self.account_key(name), self.now()),
                ).fetchone()
            )

    def account_failure(self, conn, name, now):
        """Count a checked wrong password against the account and grow its
        delay; alert the owner once a day while an account is being guessed.
        An account row's `lockouts` column holds the time of its last alert."""
        key = self.account_key(name)
        found = conn.execute(
            "SELECT failures,window_started,lockouts,locked_until,updated_at FROM login_attempts WHERE key=?",
            (key,),
        ).fetchone()
        failures, started, alerted, until, updated = found or (0, now, 0, 0, now)
        if now - updated >= DAY:
            failures, started = 0, now
        failures += 1
        if failures >= ACCOUNT_FREE_FAILURES:
            delay = 2 ** min(failures - ACCOUNT_FREE_FAILURES, 16)
            until = now + min(delay, ACCOUNT_MAX_DELAY)
        # Guessing that never pauses for a day keeps one row growing, so the
        # alert is renewed daily rather than once per row (Codex round 2).
        if failures >= ACCOUNT_ALERT_FAILURES and now - alerted >= DAY:
            person = conn.execute(
                "SELECT id FROM users WHERE username=? COLLATE NOCASE", (name.strip(),)
            ).fetchone()
            if person:
                self.event(conn, "sign_in_slowed", "", person[0])
            alerted = now
        conn.execute(
            "INSERT OR REPLACE INTO login_attempts VALUES (?,?,?,?,?,?)",
            (key, failures, started, alerted, until, now),
        )

    def locked(self, name, ip):
        with self.store.connect() as conn:
            return any(
                conn.execute(
                    "SELECT 1 FROM login_attempts WHERE key=? AND locked_until>?",
                    (key, self.now()),
                ).fetchone()
                for key in self.keys(name, ip)
            )

    def failure(self, name, ip, event="login_failed", account=False):
        now = self.now()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if account:
                self.account_failure(conn, name, now)
            for key in self.keys(name, ip):
                found = conn.execute(
                    "SELECT failures,window_started,lockouts,locked_until,updated_at FROM login_attempts WHERE key=?",
                    (key,),
                ).fetchone()
                failures, started, strikes, until, updated = found or (
                    0,
                    now,
                    0,
                    0,
                    now,
                )
                if now - started >= 900:
                    failures, started = 0, now
                if now - updated >= DAY:
                    strikes = 0
                if until <= now:
                    failures += 1
                    if failures >= 5:
                        until = now + min(60 * 2 ** min(strikes, 6), 3600)
                        strikes += 1
                        failures = 0
                conn.execute(
                    "INSERT OR REPLACE INTO login_attempts VALUES (?,?,?,?,?,?)",
                    (key, failures, started, strikes, until, now),
                )
            self.event(conn, event, ip)
            conn.execute(
                "DELETE FROM login_attempts WHERE updated_at<?", (now - 30 * DAY,)
            )
            conn.execute("DELETE FROM auth_events WHERE at<?", (now - 90 * DAY,))

    def login(self, name, password, kind, device_name, platform, ip):
        if not self.ready():
            raise AuthError("setup_required", 503)
        if (
            kind not in {"web", "device"}
            or not 1 <= len(device_name) <= 128
            or platform not in {"web", "ios", "watchos"}
        ):
            raise AuthError("invalid_device", 422)
        if not isinstance(password, str) or len(password) > 256 or len(name) > 64:
            self.failure(name[:64], ip, account=True)
            raise AuthError("invalid_credentials")
        if self.locked(name, ip) or self.slowed(name, ip):
            # Refused unchecked: counts for this address, not the account, so
            # waiting attempts never stretch the account's delay.
            self.failure(name, ip)
            raise AuthError("invalid_credentials")
        with self.store.connect() as conn:
            found = records(
                conn.execute(
                    "SELECT * FROM users WHERE username=? COLLATE NOCASE",
                    (name.strip(),),
                )
            )
        person = found[0] if found else None
        hashed = (
            person["password_hash"]
            if person and person["password_hash"]
            else DUMMY_HASH
        )
        try:
            HASHER.verify(hashed, password)
            verified = bool(
                person and person["password_hash"] and not person["disabled"]
            )
        except (VerificationError, InvalidHashError):
            verified = False
        if not verified:
            self.failure(name, ip, account=True)
            raise AuthError("invalid_credentials")
        now = self.now()
        token = secrets.token_urlsafe(32)
        expires = now + (14 if kind == "web" else 180) * DAY
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # Re-read after expensive verification: password reset/disable wins races.
            current = conn.execute(
                "SELECT password_hash,disabled FROM users WHERE id=?", (person["id"],)
            ).fetchone()
            if not current or current[1] or current[0] != hashed:
                raise AuthError("invalid_credentials")
            if HASHER.check_needs_rehash(hashed):
                conn.execute(
                    "UPDATE users SET password_hash=? WHERE id=?",
                    (HASHER.hash(password), person["id"]),
                )
            conn.execute(
                "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                (
                    digest(token),
                    str(uuid.uuid4()),
                    person["id"],
                    kind,
                    device_name,
                    platform,
                    now,
                    now,
                    expires,
                ),
            )
            conn.execute(
                "DELETE FROM login_attempts WHERE key=?", (self.keys(name, ip)[0],)
            )
            conn.execute(
                "INSERT OR REPLACE INTO login_attempts VALUES (?,0,?,0,0,?)",
                (self.known_key(name, ip), now, now),
            )
            self.event(conn, "login", ip, person["id"])
        return {"token": token, "user": public_user(person), "expires_at": expires}

    def authenticate(self, token, kind=None):
        if not token or len(token) > 256:
            raise AuthError("authentication_required")
        hashed, now = digest(token), self.now()
        with self.store.connect() as conn:
            found = records(
                conn.execute("SELECT * FROM sessions WHERE id=?", (hashed,))
            )
            if not found:
                raise AuthError("authentication_required")
            session = found[0]
            if (
                not hmac.compare_digest(session["id"], hashed)
                or session["revoked_at"] is not None
                or session["expires_at"] <= now
                or (kind and session["kind"] != kind)
                or (
                    session["kind"] == "web" and session["created_at"] + 60 * DAY <= now
                )
            ):
                raise AuthError("authentication_required")
            person = records(
                conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],))
            )
            if not person or person[0]["disabled"] or not person[0]["password_hash"]:
                raise AuthError("authentication_required")
            if now - session["last_seen_at"] >= 3600:
                expires = now + (14 if session["kind"] == "web" else 180) * DAY
                if session["kind"] == "web":
                    expires = min(expires, session["created_at"] + 60 * DAY)
                conn.execute(
                    "UPDATE sessions SET last_seen_at=?,expires_at=? WHERE id=? AND revoked_at IS NULL",
                    (now, expires, hashed),
                )
        return public_user(person[0]), session

    def sessions(self, identity):
        with self.store.connect() as conn:
            return records(
                conn.execute(
                    "SELECT public_id AS id,kind,device_name,platform,created_at,last_seen_at,expires_at,revoked_at FROM sessions WHERE user_id=? ORDER BY created_at DESC",
                    (identity,),
                )
            )

    def people(self):
        with self.store.connect() as conn:
            return records(
                conn.execute(
                    "SELECT id,username,display_name,role,disabled FROM users ORDER BY created_at"
                )
            )

    def activity(self):
        with self.store.connect() as conn:
            return records(
                conn.execute(
                    "SELECT a.at,a.user_id,a.event,u.display_name FROM auth_events a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 200"
                )
            )

    def warnings(self):
        """People whose accounts were being guessed in the last week, read from
        their account rows: failed sign-ins can push the alert events out of
        the latest-200 activity feed."""
        since = self.now() - 7 * DAY
        with self.store.connect() as conn:
            people = conn.execute("SELECT id,username,display_name FROM users")
            found = []
            for identity, name, display_name in people.fetchall():
                row = conn.execute(
                    "SELECT lockouts FROM login_attempts WHERE key=?",
                    (self.account_key(name),),
                ).fetchone()
                if row and row[0] >= since:
                    found.append(
                        {
                            "at": row[0],
                            "user_id": identity,
                            "event": "sign_in_slowed",
                            "display_name": display_name,
                        }
                    )
            return found

    def revoke(self, identity, public_id):
        with self.store.connect() as conn:
            result = conn.execute(
                "UPDATE sessions SET revoked_at=? WHERE user_id=? AND public_id=?",
                (self.now(), identity, public_id),
            )
            if not result.rowcount:
                raise AuthError("session_not_found", 404)
            self.event(conn, "session_revoked", "", identity)

    def revoke_others(self, identity, keep_public_id):
        """Sign out every other session of this person; returns how many."""
        with self.store.connect() as conn:
            result = conn.execute(
                "UPDATE sessions SET revoked_at=? WHERE user_id=? AND public_id<>? AND revoked_at IS NULL",
                (self.now(), identity, keep_public_id),
            )
            if result.rowcount:
                self.event(conn, "sessions_revoked", str(result.rowcount), identity)
            return result.rowcount

    def disable(self, owner_id, identity, disabled):
        if self.user(owner_id)["role"] != "owner":
            raise AuthError("owner_required", 403)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            target = conn.execute(
                "SELECT role FROM users WHERE id=?", (identity,)
            ).fetchone()
            if not target:
                raise AuthError("user_not_found", 404)
            if target[0] == "owner":
                raise AuthError("owner_cannot_be_disabled", 409)
            conn.execute(
                "UPDATE users SET disabled=? WHERE id=?", (int(disabled), identity)
            )
            if disabled:
                conn.execute(
                    "UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
                    (self.now(), identity),
                )
                conn.execute(
                    "UPDATE setup_codes SET used_at=? WHERE user_id=? AND used_at IS NULL",
                    (self.now(), identity),
                )
            self.event(
                conn,
                "account_disabled" if disabled else "account_enabled",
                "",
                identity,
            )
        return {"disabled": disabled}

    def reset_password(self, name, password):
        name = username(name)
        validate_password(password, name)
        hashed, now = HASHER.hash(password), self.now()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            person = conn.execute(
                "SELECT id FROM users WHERE username=? COLLATE NOCASE", (name,)
            ).fetchone()
            if not person:
                raise AuthError("user_not_found", 404)
            conn.execute(
                "UPDATE users SET password_hash=?,password_changed_at=? WHERE id=?",
                (hashed, now, person[0]),
            )
            conn.execute(
                "UPDATE sessions SET revoked_at=? WHERE user_id=?", (now, person[0])
            )
            conn.execute(
                "UPDATE setup_codes SET used_at=? WHERE user_id=? AND used_at IS NULL",
                (now, person[0]),
            )
            conn.execute(
                "DELETE FROM login_attempts WHERE key LIKE ? OR key=?",
                (self.name_key(name) + ":%", self.account_key(name)),
            )
            self.event(conn, "password_reset", "", person[0])

    def change_password(self, person, session, current_password, password):
        row = self.user(person["id"])
        try:
            HASHER.verify(row["password_hash"], current_password)
        except (VerificationError, InvalidHashError):
            raise AuthError("invalid_credentials")
        validate_password(password, row["username"])
        hashed = HASHER.hash(password)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT password_hash FROM users WHERE id=?", (person["id"],)
            ).fetchone()
            if not current or current[0] != row["password_hash"]:
                raise AuthError("invalid_credentials")
            conn.execute(
                "UPDATE users SET password_hash=?,password_changed_at=? WHERE id=?",
                (hashed, self.now(), person["id"]),
            )
            conn.execute(
                "UPDATE sessions SET revoked_at=? WHERE user_id=? AND id!=?",
                (self.now(), person["id"], session["id"]),
            )
            conn.execute(
                "UPDATE setup_codes SET used_at=? WHERE user_id=? AND used_at IS NULL",
                (self.now(), person["id"]),
            )
            self.event(conn, "password_changed", "", person["id"])

    def setup_code(self, owner_id, name=None, display_name=None, identity=None):
        if self.user(owner_id)["role"] != "owner":
            raise AuthError("owner_required", 403)
        now, code = self.now(), "".join(
            secrets.choice("23456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(16)
        )
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if identity:
                if identity == owner_id:
                    raise AuthError("use_password_change", 422)
                target = conn.execute(
                    "SELECT id FROM users WHERE id=? AND role='member'", (identity,)
                ).fetchone()
                if not target:
                    raise AuthError("user_not_found", 404)
                conn.execute(
                    "UPDATE users SET password_hash=NULL,password_changed_at=? WHERE id=?",
                    (now, identity),
                )
            else:
                name = username(name or "")
                identity = str(uuid.uuid4())
                if conn.execute(
                    "SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (name,)
                ).fetchone():
                    raise AuthError("username_unavailable", 409)
                conn.execute(
                    "INSERT INTO users VALUES (?,?,?,?,NULL,NULL,0,?)",
                    (identity, name, (display_name or name)[:128], "member", now),
                )
            conn.execute(
                "UPDATE sessions SET revoked_at=? WHERE user_id=?", (now, identity)
            )
            conn.execute(
                "UPDATE setup_codes SET used_at=? WHERE user_id=? AND used_at IS NULL",
                (now, identity),
            )
            conn.execute(
                "INSERT INTO setup_codes VALUES (?,?,?,?,NULL)",
                (str(uuid.uuid4()), identity, digest(code), now + DAY),
            )
            self.event(conn, "setup_issued", "", identity)
        return {"user_id": identity, "code": code, "expires_at": now + DAY}

    def activate(self, code, password, ip):
        normalized = code.strip().upper()
        if len(normalized) > 64 or self.locked("setup:" + digest(normalized), ip):
            raise AuthError("invalid_setup_code")
        with self.store.connect() as conn:
            found = records(
                conn.execute(
                    "SELECT u.*,s.id AS setup_id,s.code_hash,s.expires_at,s.used_at FROM setup_codes s JOIN users u ON u.id=s.user_id WHERE s.code_hash=?",
                    (digest(normalized),),
                )
            )
        if (
            not found
            or found[0]["used_at"] is not None
            or found[0]["expires_at"] <= self.now()
            or found[0]["disabled"]
        ):
            self.failure("setup:" + digest(normalized), ip, "setup_failed")
            raise AuthError("invalid_setup_code")
        person = found[0]
        validate_password(password, person["username"])
        hashed = HASHER.hash(password)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            updated = conn.execute(
                "UPDATE setup_codes SET used_at=? WHERE id=? AND used_at IS NULL AND expires_at>?",
                (self.now(), person["setup_id"], self.now()),
            )
            if not updated.rowcount:
                raise AuthError("invalid_setup_code")
            conn.execute(
                "UPDATE users SET password_hash=?,password_changed_at=? WHERE id=?",
                (hashed, self.now(), person["id"]),
            )
            self.event(conn, "setup_completed", ip, person["id"])
        return public_user(person)
