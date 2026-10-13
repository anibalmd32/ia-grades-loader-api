"""Hash de contraseñas (argon2), tokens JWT y generación de contraseñas."""

import secrets
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

from app.core.config import get_settings

_password_hash = PasswordHash.recommended()

# Hash calculado una vez para gastar el mismo tiempo cuando el usuario no existe
# y no revelar por tiempo de respuesta qué credenciales están registradas.
_DUMMY_HASH = _password_hash.hash(secrets.token_urlsafe(16))

# Sin caracteres ambiguos (0/O, 1/l/I) para que sea fácil de dictar o copiar a mano
_PASSWORD_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if password_hash is None:
        _password_hash.verify(password, _DUMMY_HASH)
        return False
    return _password_hash.verify(password, password_hash)


def generate_password(length: int | None = None) -> str:
    length = length or get_settings().generated_password_length
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def create_access_token(user_id: int, role: str) -> tuple[str, int]:
    """Devuelve el token y su duración en segundos."""
    settings = get_settings()
    expires_in = settings.access_token_expire_minutes * 60
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, expires_in


def decode_access_token(token: str) -> int | None:
    """Devuelve el id del usuario o None si el token es inválido o expiró."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
        return int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError):
        return None
