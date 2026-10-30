"""HTTPS-only account API. Password hashing is bounded to two worker threads;
session lookups run on the reserved session threads, outside that gate, and
account changes on the shared pool."""

import asyncio
import functools
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr

from ..auth import Auth, AuthError, COOKIE, DAY, browser_name
from ..security import session_thread
from ..api_models import (
    ActivationDTO,
    DeviceSessionDTO,
    InviteDTO,
    UserDTO,
    SessionsDTO,
    RevokedDTO,
    RevokedOthersDTO,
    PasswordChangedDTO,
)


# Password checks that may be hashing (two) or waiting at once.
HASHING_LIMIT = 10


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: SecretStr = Field(max_length=256)


class DeviceCredentials(Credentials):
    device_name: str = Field(min_length=1, max_length=128)
    platform: Literal["ios", "watchos"]


class PasswordChange(BaseModel):
    current_password: SecretStr = Field(max_length=256)
    password: SecretStr = Field(min_length=12, max_length=256)


class Activation(BaseModel):
    code: SecretStr = Field(min_length=8, max_length=64)
    password: SecretStr = Field(min_length=12, max_length=256)


class OwnerSetup(Credentials):
    code: SecretStr = Field(min_length=16, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)
    password: SecretStr = Field(min_length=12, max_length=256)


class Disabled(BaseModel):
    disabled: bool


class Person(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    display_name: str = Field(min_length=1, max_length=128)


def error(exc):
    raise HTTPException(exc.status, detail=exc.code)


def set_cookie(response, token):
    response.set_cookie(
        COOKIE,
        token,
        max_age=14 * DAY,
        secure=True,
        httponly=True,
        samesite="strict",
        path="/",
    )


def auth_router(auth: Auth, security=None, origin: str | None = None):
    router = APIRouter(prefix="/api/v1", tags=["accounts"])
    hashing = asyncio.Semaphore(2)
    entered = 0

    async def call(threads, function, *args, **kwargs):
        try:
            return await threads(functools.partial(function, *args, **kwargs))
        except AuthError as exc:
            error(exc)

    async def hashed(function, *args):
        """Password work (Argon2, 64 MiB each): at most two at a time, on the
        shared pool so it never occupies the reserved session threads. Past
        HASHING_LIMIT hashing or waiting, more are refused at once instead of
        queueing behind a burst of anonymous attempts."""
        nonlocal entered
        if entered >= HASHING_LIMIT:
            raise HTTPException(429, "rate_limited")
        entered += 1
        try:
            async with hashing:
                return await call(asyncio.to_thread, function, *args)
        finally:
            entered -= 1

    async def run(function, *args, **kwargs):
        """Session and account lookups never wait behind password hashing, so
        anonymous sign-in attempts can't slow signed-in requests (audit S3)."""
        return await call(session_thread, function, *args, **kwargs)

    async def write(function, *args, **kwargs):
        """Account changes can wait up to 10 s for a SQLite writer: the shared
        pool, never the reserved session threads (Codex round 1 on 6f00ce2)."""
        return await call(asyncio.to_thread, function, *args, **kwargs)

    async def read(function, *args):
        """Owner pages' reads: the shared pool, never the event loop."""
        return await call(asyncio.to_thread, function, *args)

    def require_https(request: Request):
        if request.url.scheme != "https":
            raise HTTPException(400, "https_required")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if (
                not request.headers.get("authorization", "").startswith("Bearer ")
                and request.headers.get("x-pig-request") != "1"
            ):
                raise HTTPException(403, "request_header_required")
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                raise HTTPException(403, "invalid_origin")

    async def current(request: Request, response: Response):
        require_https(request)
        bearer = request.headers.get("authorization", "")
        if bearer:
            if not bearer.startswith("Bearer "):
                raise HTTPException(401, "authentication_required")
            token, kind = bearer[7:], "device"
        else:
            token, kind = request.cookies.get(COOKIE, ""), "web"
        result = await run(auth.authenticate, token, kind)
        person, session = result
        if kind == "web" and auth.now() - session["last_seen_at"] >= 3600:
            set_cookie(response, token)
        request.state.user, request.state.session = person, session
        return result

    def invite(result):
        link = origin and f"{origin}/#/join?code={result['code']}"
        return {**result, "invite_url": link}

    async def owner(result=Depends(current)):
        if result[0]["role"] != "owner":
            raise HTTPException(403, "owner_required")
        return result

    @router.get("/setup/owner", dependencies=[Depends(require_https)])
    async def owner_setup_status():
        return {"setup_required": not await run(auth.has_owner)}

    @router.post("/setup/owner", dependencies=[Depends(require_https)])
    async def create_first_owner(
        body: OwnerSetup, request: Request, response: Response
    ):
        if security is None:
            raise HTTPException(503, "setup_unavailable")
        result = await hashed(
            auth.claim_owner,
            body.code.get_secret_value(),
            security.installation_code(),
            body.username,
            body.password.get_secret_value(),
            body.display_name.strip(),
            request.client.host,
            browser_name(request.headers.get("user-agent", "")),
        )
        set_cookie(response, result["token"])
        return {"user": result["user"]}

    @router.post("/auth/login", dependencies=[Depends(require_https)])
    async def login(body: Credentials, request: Request, response: Response):
        result = await hashed(
            auth.login,
            body.username,
            body.password.get_secret_value(),
            "web",
            browser_name(request.headers.get("user-agent", "")),
            "web",
            request.client.host,
        )
        set_cookie(response, result["token"])
        return {"user": result["user"]}

    @router.post(
        "/auth/device",
        dependencies=[Depends(require_https)],
        response_model=DeviceSessionDTO,
    )
    async def device(body: DeviceCredentials, request: Request):
        return await hashed(
            auth.login,
            body.username,
            body.password.get_secret_value(),
            "device",
            body.device_name,
            body.platform,
            request.client.host,
        )

    @router.post(
        "/setup", dependencies=[Depends(require_https)], response_model=ActivationDTO
    )
    async def activate(body: Activation, request: Request):
        return {
            "user": await hashed(
                auth.activate,
                body.code.get_secret_value(),
                body.password.get_secret_value(),
                request.client.host,
            )
        }

    @router.get("/me", response_model=UserDTO)
    async def me(result=Depends(current)):
        return result[0]

    @router.get("/me/sessions", response_model=SessionsDTO)
    async def sessions(result=Depends(current)):
        mine = result[1]["public_id"]
        return {
            "sessions": [
                {**s, "current": s["id"] == mine}
                for s in await run(auth.sessions, result[0]["id"])
            ]
        }

    @router.delete("/me/sessions", response_model=RevokedOthersDTO)
    async def revoke_others(result=Depends(current)):
        """Sign out every device except this one."""
        return {
            "revoked": await write(
                auth.revoke_others, result[0]["id"], result[1]["public_id"]
            )
        }

    @router.delete("/me/sessions/{identity}", response_model=RevokedDTO)
    async def revoke(identity: str, result=Depends(current)):
        await write(auth.revoke, result[0]["id"], identity)
        return {"revoked": True}

    @router.post("/auth/logout")
    async def logout(response: Response, result=Depends(current)):
        await write(auth.revoke, result[0]["id"], result[1]["public_id"])
        response.delete_cookie(
            COOKIE, path="/", secure=True, httponly=True, samesite="strict"
        )
        return {"logged_out": True}

    @router.post("/me/password", response_model=PasswordChangedDTO)
    async def password(body: PasswordChange, result=Depends(current)):
        await hashed(
            auth.change_password,
            result[0],
            result[1],
            body.current_password.get_secret_value(),
            body.password.get_secret_value(),
        )
        return {"changed": True}

    @router.get("/people")
    async def people(result=Depends(owner)):
        return {"people": await read(auth.people)}

    @router.post("/people", response_model=InviteDTO)
    async def add(body: Person, result=Depends(owner)):
        return invite(
            await write(
                auth.setup_code, result[0]["id"], body.username, body.display_name
            )
        )

    @router.post("/people/{identity}/setup-code", response_model=InviteDTO)
    async def reset(identity: str, result=Depends(owner)):
        return invite(await write(auth.setup_code, result[0]["id"], identity=identity))

    @router.put("/people/{identity}/disabled")
    async def disable(identity: str, body: Disabled, result=Depends(owner)):
        return await write(auth.disable, result[0]["id"], identity, body.disabled)

    @router.get("/people/activity")
    async def activity(result=Depends(owner)):
        return {
            "events": await read(auth.activity),
            "warnings": await read(auth.warnings),
        }

    return router
