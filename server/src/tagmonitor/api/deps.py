"""FastAPI dependencies shared by the routers.

Resources live on app.state (set up in the lifespan), so tests can build an app with their own
pool, storage and DNS resolver without running the lifespan.
"""

from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request, status

from tagmonitor.api.security import SESSION_COOKIE, CurrentUser, user_for_session
from tagmonitor.browser.ssrf import Resolver
from tagmonitor.config import Settings
from tagmonitor.db.pool import Pool
from tagmonitor.storage import ObjectStorage


def get_pool(request: Request) -> Pool:
    return cast(Pool, request.app.state.pool)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_storage(request: Request) -> ObjectStorage:
    return cast(ObjectStorage, request.app.state.storage)


def get_resolver(request: Request) -> Resolver:
    return cast(Resolver, request.app.state.resolver)


PoolDep = Annotated[Pool, Depends(get_pool)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[ObjectStorage, Depends(get_storage)]
ResolverDep = Annotated[Resolver, Depends(get_resolver)]


async def current_user(request: Request, pool: PoolDep) -> CurrentUser:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        async with pool.connection() as conn:
            user = await user_for_session(conn, token)
        if user is not None:
            return user
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please log in.")


UserDep = Annotated[CurrentUser, Depends(current_user)]


async def admin_user(user: UserDep) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only.")
    return user


AdminDep = Annotated[CurrentUser, Depends(admin_user)]
