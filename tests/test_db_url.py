import ssl

import pytest

from app.db.url import engine_url_and_args

BASE = "mysql+asyncmy://u:p%40ss@db.example.com:3306/notas"


def test_url_sin_ssl_no_cambia():
    assert engine_url_and_args(BASE) == (BASE, {})


@pytest.mark.parametrize("param", ["ssl-mode", "ssl_mode", "sslmode"])
def test_ssl_mode_required_se_convierte_en_contexto_ssl(param):
    url, args = engine_url_and_args(f"{BASE}?{param}=REQUIRED")
    assert url == BASE  # el parámetro desaparece y la clave sigue escapada
    context = args["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False


def test_conserva_otros_parametros():
    url, args = engine_url_and_args(f"{BASE}?charset=utf8mb4&ssl-mode=REQUIRED")
    assert url == f"{BASE}?charset=utf8mb4"
    assert "ssl" in args


def test_modos_que_verifican_el_certificado():
    _, args = engine_url_and_args(f"{BASE}?ssl-mode=VERIFY_IDENTITY")
    assert args["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert args["ssl"].check_hostname is True

    _, args = engine_url_and_args(f"{BASE}?ssl-mode=verify-ca")
    assert args["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert args["ssl"].check_hostname is False


def test_ssl_desactivado():
    assert engine_url_and_args(f"{BASE}?ssl-mode=DISABLED") == (BASE, {})
