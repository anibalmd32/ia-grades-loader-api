"""Dependencias comunes de los routers: sesión de BD, usuario autenticado y roles."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import decode_access_token
from app.db.session import get_db
from app.models import Role, User

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


async def get_current_user(
    db: DbSession, token: Annotated[str | None, Depends(oauth2_scheme)]
) -> User:
    user_id = decode_access_token(token) if token else None
    if user_id is None:
        raise _UNAUTHORIZED
    user = await db.get(User, user_id)
    # Un usuario desactivado pierde el acceso aunque su token siga vigente
    if user is None or not user.is_active:
        raise _UNAUTHORIZED
    return user


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
