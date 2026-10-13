"""Autenticación: el personal entra con email y los estudiantes con tipo + número de cédula."""

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import TokenData, create_access_token, hash_password, verify_password
from app.models import STAFF_ROLES, Nationality, RevokedToken, Role, User
from app.schemas.auth import TokenResponse


class InvalidCredentialsError(Exception):
    pass


class AccountDisabledError(Exception):
    pass


def _check(user: User | None, password: str) -> User:
    # verify_password se ejecuta siempre para que el tiempo de respuesta no
    # revele si el usuario existe.
    valid = verify_password(password, user.password_hash if user else None)
    if user is None or not valid:
        raise InvalidCredentialsError
    if not user.is_active:
        raise AccountDisabledError
    return user


async def authenticate_staff(db: AsyncSession, email: str, password: str) -> User:
    user = await db.scalar(
        select(User).where(User.email == email.lower(), User.role.in_(STAFF_ROLES))
    )
    return _check(user, password)


async def authenticate_student(
    db: AsyncSession, nationality: Nationality, cedula: int, password: str
) -> User:
    user = await db.scalar(
        select(User).where(
            User.nationality == nationality,
            User.cedula == cedula,
            User.role == Role.STUDENT,
        )
    )
    return _check(user, password)


async def change_password(
    db: AsyncSession, user: User, current_password: str, new_password: str
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise InvalidCredentialsError
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.token_version += 1  # cierra todas las sesiones, incluida la actual
    await db.commit()


def issue_token(user: User) -> TokenResponse:
    token, expires_in = create_access_token(user.id, user.role.value, user.token_version)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        role=user.role,
        must_change_password=user.must_change_password,
    )


async def logout(db: AsyncSession, token: TokenData) -> None:
    """Revoca el token actual y aprovecha para limpiar los revocados ya vencidos."""
    now = datetime.now(UTC).replace(tzinfo=None)
    await db.execute(delete(RevokedToken).where(RevokedToken.expires_at < now))
    if await db.get(RevokedToken, token.jti) is None:
        db.add(RevokedToken(jti=token.jti, expires_at=token.expires_at))
    await db.commit()
