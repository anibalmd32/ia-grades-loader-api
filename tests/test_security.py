from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_password,
    hash_password,
    verify_password,
)


def test_hash_y_verificacion():
    hashed = hash_password("secreta123")
    assert hashed != "secreta123"
    assert verify_password("secreta123", hashed)
    assert not verify_password("otra", hashed)


def test_verificar_sin_hash_es_falso():
    assert not verify_password("loquesea", None)


def test_password_generada():
    password = generate_password(12)
    assert len(password) == 12
    assert not set(password) & set("0O1lI")
    assert generate_password(12) != password


def test_token_ida_y_vuelta():
    token, expires_in = create_access_token(42, "student")
    assert expires_in == get_settings().access_token_expire_minutes * 60
    assert decode_access_token(token) == 42


def test_token_invalido_o_expirado():
    settings = get_settings()
    expired = jwt.encode(
        {"sub": "1", "exp": datetime.now(UTC) - timedelta(seconds=1)},
        settings.jwt_secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
    assert decode_access_token(expired) is None
    assert decode_access_token("no-es-un-token") is None
    forged = jwt.encode(
        {"sub": "1", "exp": datetime.now(UTC) + timedelta(hours=1)},
        "otra-clave-de-firma-de-al-menos-32-bytes",
    )
    assert decode_access_token(forged) is None
