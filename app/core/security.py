"""Hash de contraseñas (argon2), tokens JWT y generación de contraseñas."""

import secrets
import uuid
from dataclasses import dataclass
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
        # Identificador único del token, necesario para poder revocarlo (logout)
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(
        payload, settings.jwt_secret_key.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, expires_in


@dataclass(frozen=True)
class TokenData:
    user_id: int
    jti: str
    expires_at: datetime  # UTC sin zona horaria, igual que se guarda en BD


def decode_access_token(token: str) -> TokenData | None:
    """Devuelve los datos del token o None si es inválido o expiró."""
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp", "jti"]},
        )
        return TokenData(
            user_id=int(payload["sub"]),
            jti=str(payload["jti"]),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC).replace(tzinfo=None),
        )
    except (jwt.InvalidTokenError, ValueError, TypeError):
        return None
