import logging

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.errors import register_error_handlers
from app.core.logging import RequestIdMiddleware, configure_logging


async def test_cada_respuesta_lleva_request_id(client):
    response = await client.get("/health")
    assert len(response.headers["X-Request-ID"]) == 32


async def test_se_respeta_el_request_id_del_cliente_si_es_valido(client):
    ok = await client.get("/health", headers={"X-Request-ID": "front-abc.123"})
    assert ok.headers["X-Request-ID"] == "front-abc.123"
    bad = await client.get("/health", headers={"X-Request-ID": "<script>" * 20})
    assert bad.headers["X-Request-ID"] != "<script>" * 20


async def test_error_inesperado_responde_json_con_request_id(caplog):
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("detalle interno que no debe filtrarse")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/boom", headers={"X-Request-ID": "req-42"})

    assert response.status_code == 500
    body = response.json()
    assert body["request_id"] == "req-42"
    assert "detalle interno" not in body["detail"]
    assert any(
        r.levelno == logging.ERROR and "Error inesperado" in r.getMessage() for r in caplog.records
    )


def test_las_librerias_ruidosas_no_registran_en_info():
    configure_logging("INFO")
    # httpx escribiría la URL de Telegram, que contiene el token del bot
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    assert logging.getLogger("app").isEnabledFor(logging.INFO)
