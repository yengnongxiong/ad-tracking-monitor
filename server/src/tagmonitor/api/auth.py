"""Sign up, log in, log out, who am I (PRD §17)."""

from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, status

from tagmonitor.api import security
from tagmonitor.api.deps import PoolDep, SettingsDep, UserDep
from tagmonitor.api.schemas import Credentials, UserOut
from tagmonitor.config import Settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        security.SESSION_COOKIE,
        token,
        max_age=int(security.SESSION_TTL.total_seconds()),
        httponly=True,  # JavaScript can't read it, so XSS can't steal it
        samesite="lax",  # not sent on cross-site POSTs; plus the X-Requested-With check
        secure=settings.session_cookie_secure,  # HTTPS-only in production
        path="/",
    )


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(
    body: Credentials, response: Response, pool: PoolDep, settings: SettingsDep
) -> UserOut:
    password_hash = await security.hash_password(body.password)
    async with pool.connection() as conn, conn.transaction():
        cursor = await conn.execute(
            "INSERT INTO users (email, password_hash) VALUES (%s, %s) "
            "ON CONFLICT (email) DO NOTHING RETURNING id, email, is_admin",
            (body.email, password_hash),
        )
        row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email exists.")
        token = await security.create_session(conn, row["id"])
    _set_session_cookie(response, token, settings)
    return UserOut(id=row["id"], email=str(row["email"]), is_admin=row["is_admin"])


@router.post("/login")
async def login(
    body: Credentials,
    request: Request,
    response: Response,
    pool: PoolDep,
    settings: SettingsDep,
) -> UserOut:
    key = security.login_key(body.email, security.client_ip(request, settings.trust_proxy_headers))
    async with pool.connection() as conn:
        retry_after = await security.login_retry_after(conn, key)
        if retry_after is not None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many failed attempts. Try again in a few minutes.",
                headers={"Retry-After": str(retry_after)},
            )
        cursor = await conn.execute(
            "SELECT id, email, is_admin, password_hash FROM users WHERE email = %s",
            (body.email,),
        )
        user = await cursor.fetchone()
        stored_hash = user["password_hash"] if user else None
        if user is None or not await security.verify_password(stored_hash, body.password):
            await security.record_failed_login(conn, key)
            # One message for both cases: don't reveal which emails have accounts.
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password.")
        await security.clear_failed_logins(conn, key)
        if security.needs_rehash(user["password_hash"]):
            await conn.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (await security.hash_password(body.password), user["id"]),
            )
        token = await security.create_session(conn, user["id"])
    _set_session_cookie(response, token, settings)
    return UserOut(id=user["id"], email=str(user["email"]), is_admin=user["is_admin"])


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    pool: PoolDep,
    token: Annotated[str | None, Cookie(alias=security.SESSION_COOKIE)] = None,
) -> None:
    if token:
        async with pool.connection() as conn:
            await security.delete_session(conn, token)
    response.delete_cookie(security.SESSION_COOKIE, path="/")


@router.get("/me")
async def me(user: UserDep) -> UserOut:
    return UserOut(id=user.id, email=user.email, is_admin=user.is_admin)
