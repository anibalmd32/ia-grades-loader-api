"""Dependencias comunes de los routers: sesión de BD, usuario autenticado y roles."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import TokenData, decode_access_token
from app.db.session import get_db
from app.models import RevokedToken, Role, User

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


async def get_token_data(
    db: DbSession, token: Annotated[str | None, Depends(oauth2_scheme)]
) -> TokenData:
    data = decode_access_token(token) if token else None
    # Un token revocado por logout deja de valer aunque no haya expirado
    if data is None or await db.get(RevokedToken, data.jti) is not None:
        raise _UNAUTHORIZED
    return data


CurrentToken = Annotated[TokenData, Depends(get_token_data)]


async def get_current_user(db: DbSession, token: CurrentToken) -> User:
    user = await db.get(User, token.user_id)
    # Un usuario desactivado, o cuya contraseña cambió después de emitirse el token,
    # pierde el acceso aunque el token no haya expirado
    if user is None or not user.is_active or user.token_version != token.token_version:
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
