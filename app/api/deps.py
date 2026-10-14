"""Dependencias comunes de los routers: sesión de BD, usuario autenticado y roles."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import TokenData
from app.db.session import get_db
from app.models import Role, User
from app.services import auth as auth_service

DbSession = Annotated[AsyncSession, Depends(get_db)]

# auto_error=False para responder siempre con nuestro propio mensaje de 401
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{get_settings().api_v1_prefix}/auth/token", auto_error=False
)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="No autenticado o sesión expirada",
    headers={"WWW-Authenticate": "Bearer"},
)


async def _authenticate(
    db: DbSession, token: Annotated[str | None, Depends(oauth2_scheme)]
) -> tuple[TokenData, User]:
    session = await auth_service.user_from_token(db, token)
    if session is None:
        raise _UNAUTHORIZED
    return session


async def get_token_data(
    session: Annotated[tuple[TokenData, User], Depends(_authenticate)],
) -> TokenData:
    return session[0]


CurrentToken = Annotated[TokenData, Depends(get_token_data)]


async def get_current_user(
    session: Annotated[tuple[TokenData, User], Depends(_authenticate)],
) -> User:
    return session[1]


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[[User], Awaitable[User]]:
    """Restringe un endpoint a ciertos roles.

    El super admin hereda los permisos del coordinador: cualquier endpoint
    abierto a coordinadores también lo está para él.
    """
    allowed = set(roles)
    if Role.COORDINATOR in allowed:
        allowed.add(Role.SUPER_ADMIN)

    async def dependency(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permiso para esta acción")
        return user

    return dependency
