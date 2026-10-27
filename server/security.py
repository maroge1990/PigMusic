"""Default-deny API guard and session-bound, revocable playback URLs."""

import asyncio
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import stat
import tempfile
from urllib.parse import urlencode

from fastapi import HTTPException, Request

from .auth import AuthError, COOKIE, public_user, records
from .request_timing import phase

PUBLIC = frozenset(
    {
        ("GET", "/api/health"),
        ("HEAD", "/api/health"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/device"),
        ("POST", "/api/v1/setup"),
        ("GET", "/api/v1/setup/owner"),
        ("POST", "/api/v1/setup/owner"),
    }
)
PERSONAL_ACCOUNT_PREFIXES = ("/api/v1/me", "/api/v1/auth/logout")
# Paths an audio element may open with a signed, expiring query instead of a session.
SIGNED_PREFIXES = ("/api/v1/playback/files/", "/api/v1/remote-playback/streams/")


def secret_file(directory):
    path = Path(directory) / "auth-secret"
    if not path.exists():
        with tempfile.NamedTemporaryFile(
            dir=directory, prefix=".auth-secret-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(secrets.token_bytes(32))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError:
                pass
        finally:
            temporary.unlink()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("Authentication secret must be a regular file")
        os.fchmod(stream.fileno(), 0o600)
        value = stream.read(33)
    if len(value) != 32:
        raise ValueError("Authentication secret must be 32 bytes")
    return value


class Security:
    def __init__(self, auth, legacy_test=False):
        self.auth = auth
        self.legacy_test = legacy_test
        self.secret = secret_file(auth.store.directory)

    def installation_code(self):
        # Domain-separated proof of access to this installation, not a session.
        return hmac.new(
            self.secret, b"pig-music:first-owner:v1", hashlib.sha256
        ).hexdigest()[:32]

    async def current_user(self, request: Request):
        if not request.url.path.startswith("/api/"):
            return None
        if self.legacy_test:
            return None
        if request.url.path == "/api/health" and request.method in {"GET", "HEAD"}:
            return None
        if (request.method, request.url.path) in {
            ("GET", "/api/v1/setup/owner"),
            ("POST", "/api/v1/setup/owner"),
        }:
            return None
        with phase("auth-ready"):
            ready = await asyncio.to_thread(self.auth.ready)
        if not ready:
            raise HTTPException(503, "setup_required")
        if (request.method, request.url.path) in PUBLIC:
            return None
        authorization = request.headers.get("authorization", "")
        token = request.cookies.get(COOKIE, "")
        kind = "web"
        if authorization:
            if not authorization.startswith("Bearer "):
                raise HTTPException(401, "authentication_required")
            token, kind = authorization[7:], "device"
        try:
            if (
                not token
                and request.url.path.startswith(SIGNED_PREFIXES)
                and request.method in {"GET", "HEAD"}
                and any(key in request.query_params for key in ("exp", "sid", "sig"))
            ):
                with phase("auth-link"):
                    person, session = await asyncio.to_thread(
                        self.verify_url, request.url.path, dict(request.query_params)
                    )
            else:
                with phase("auth-session"):
                    person, session = await asyncio.to_thread(
                        self.auth.authenticate, token, kind
                    )
        except AuthError as exc:
            raise HTTPException(exc.status, exc.code)
        if kind == "web" and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if (
                request.url.scheme != "https"
                or request.headers.get("x-pig-request") != "1"
            ):
                raise HTTPException(403, "request_header_required")
        if (
            kind == "web"
            and token
            and self.auth.now() - session["last_seen_at"] >= 3600
        ):
            request.state.refresh_cookie = token
        # Members only reach personal-library routes and their own media. The
        # household projection and server administration remain owner-only.
        scoped = "personal-library" in getattr(request.scope.get("route"), "tags", [])
        media_path = request.url.path.startswith(
            "/api/v1/playback/files/"
        ) or request.url.path.startswith("/api/v1/playback/")
        media_path = media_path or request.url.path.startswith("/api/v1/tracks/")
        if media_path:
            from .media import PLAYBACK_CACHE_SECONDS, saved_track

            track_id = request.path_params.get("track_id")
            if track_id and track_id.startswith("mix:"):
                try:
                    if not (
                        request.url.path.startswith(
                            ("/api/v1/playback/files/", "/api/v1/playback/")
                        )
                        or request.url.path == f"/api/v1/tracks/{track_id}/file"
                    ):
                        raise LookupError()
                    # Asking for an address needs only the person's own entry:
                    # the endpoint finds a stand-in for an edition no longer
                    # served. Files and streams still need a playable edition.
                    asking = request.url.path == f"/api/v1/playback/{track_id}/url"
                    with phase("auth-mix"):
                        await asyncio.to_thread(
                            self.auth.store.mixes.entry,
                            person["id"],
                            track_id,
                            playback=not asking,
                        )
                except (LookupError, AttributeError):
                    raise HTTPException(404, "Track not found")
                request.state.user = person
                request.state.session = session
                return person
            # Household files stream for every member without being added first.
            # Device copies stay limited to the member's own library.
            streaming = (
                request.url.path.startswith(
                    ("/api/v1/playback/files/", "/api/v1/playback/")
                )
                or request.url.path == f"/api/v1/tracks/{track_id}/file"
            )

            def media_denied():
                return not self.auth.store.user_libraries.contains(
                    person["id"], track_id
                ) and (
                    not streaming
                    or not track_id
                    or saved_track(self.auth.store, track_id, PLAYBACK_CACHE_SECONDS)[0]
                    is None
                )

            # Database and NAS reads: never on the event loop (LOOP1).
            with phase("auth-media"):
                denied = await asyncio.to_thread(media_denied)
            if denied:
                raise HTTPException(404, "Track not found")
            scoped = True
        if person["role"] != "owner" and request.url.path.startswith(
            ("/api/v1/covers/", "/api/v1/library-artwork/", "/api/v1/artwork/")
        ):
            access = await asyncio.to_thread(
                self.auth.store.user_libraries.artwork_access, person["id"]
            )
            path = request.url.path.removesuffix("/metadata")
            if path.startswith("/api/v1/artwork/"):
                path = path.replace("/api/v1/artwork/", "/api/v1/covers/") + ".jpg"
            if path.startswith("/api/v1/library-artwork/"):
                kind, item_id = request.path_params.get(
                    "kind"
                ), request.path_params.get("item_id")
                allowed = (
                    kind in {"artists", "albums"} and item_id in access["items"][kind]
                )
            else:
                allowed = path in access["paths"]
            if not allowed:
                raise HTTPException(404, "Artwork not found")
            scoped = True
        if (
            person["role"] != "owner"
            and not scoped
            and not request.url.path.startswith(PERSONAL_ACCOUNT_PREFIXES)
        ):
            raise HTTPException(403, "owner_only")
        request.state.user, request.state.session = person, session
        return person

    def sign_url(self, path, person, session):
        if not path.startswith(SIGNED_PREFIXES) or "?" in path:
            raise ValueError("Only a saved-track playback path can be signed")
        expiry = min(self.auth.now() + 6 * 3600, session["expires_at"])
        message = f"{person['id']}|{session['public_id']}|{path}|{expiry}"
        signature = hmac.new(self.secret, message.encode(), hashlib.sha256).hexdigest()
        return (
            path
            + "?"
            + urlencode({"exp": expiry, "sid": session["public_id"], "sig": signature})
        )

    def verify_url(self, path, params):
        try:
            expiry = int(params["exp"])
            sid = params["sid"]
            signature = params["sig"]
        except (KeyError, ValueError):
            raise AuthError("invalid_playback_url", 403)
        now = self.auth.now()
        if (
            expiry <= now
            or expiry > now + 6 * 3600
            or len(sid) > 64
            or len(signature) != 64
        ):
            raise AuthError("invalid_playback_url", 403)
        with self.auth.store.connect() as conn:
            found = records(
                conn.execute("SELECT * FROM sessions WHERE public_id=?", (sid,))
            )
        if not found:
            raise AuthError("invalid_playback_url", 403)
        session = found[0]
        if (
            session["revoked_at"] is not None
            or session["expires_at"] <= now
            or (session["kind"] == "web" and session["created_at"] + 60 * 86400 <= now)
        ):
            raise AuthError("invalid_playback_url", 403)
        person = self.auth.user(session["user_id"])
        message = f"{person['id']}|{sid}|{path}|{expiry}"
        expected = hmac.new(self.secret, message.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise AuthError("invalid_playback_url", 403)
        return public_user(person), session


def api_routes(app):
    """Every effective API route, including those behind included routers.

    FastAPI 0.14x wraps ``include_router`` results lazily, so ``app.routes``
    alone omits them; their effective contexts carry the app dependencies.
    """

    def walk(routes):
        for route in routes:
            contexts = getattr(route, "effective_route_contexts", None)
            if contexts is not None:
                yield from contexts()
            elif getattr(route, "original_router", None) is not None:
                yield from walk(route.original_router.routes)
            else:
                yield route

    for route in walk(app.routes):
        if getattr(route, "path", "").startswith("/api/"):
            yield route


def audit_routes(app, guard):
    for route in api_routes(app):
        if not any(dep.call == guard for dep in route.dependant.dependencies):
            raise RuntimeError(
                "API route is missing its default-deny guard: " + route.path
            )
