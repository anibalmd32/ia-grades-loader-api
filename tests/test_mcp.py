import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken

import app.mcp.server as mcp_server
from app.core.security import create_access_token
from app.mcp.server import StudentTokenVerifier, create_mcp
from app.models import Role, User


async def token_for(session, user_id: int) -> str:
    user = await session.get(User, user_id)
    return create_access_token(user.id, user.role.value, user.token_version)[0]


# --- Verificación del token --------------------------------------------------


async def test_verificador_acepta_estudiante(campus, session, sessionmaker):
    ana = campus["students"]["Ana"]["id"]
    access = await StudentTokenVerifier(sessionmaker).verify_token(await token_for(session, ana))
    assert access is not None
    assert access.claims == {"user_id": ana}


async def test_verificador_rechaza_personal_y_tokens_invalidos(create_user, session, sessionmaker):
    verifier = StudentTokenVerifier(sessionmaker)
    teacher = await create_user(Role.TEACHER, email="t@uni.edu")
    assert await verifier.verify_token(await token_for(session, teacher.id)) is None
    assert await verifier.verify_token("no-es-un-token") is None


async def test_verificador_rechaza_sesion_cerrada_por_cambio_de_password(
    campus, session, sessionmaker
):
    ana_id = campus["students"]["Ana"]["id"]
    token = await token_for(session, ana_id)
    ana = await session.get(User, ana_id)
    ana.token_version += 1
    await session.commit()
    assert await StudentTokenVerifier(sessionmaker).verify_token(token) is None


# --- Herramientas ------------------------------------------------------------


def fake_identity(monkeypatch, user_id: int | None) -> None:
    token = (
        None
        if user_id is None
        else AccessToken(token="t", client_id="c", scopes=[], claims={"user_id": user_id})
    )
    monkeypatch.setattr(mcp_server, "get_access_token", lambda: token)


async def call(sessionmaker, name: str, args: dict | None = None) -> dict:
    async with Client(create_mcp(sessionmaker)) as client:
        result = await client.call_tool(name, args or {})
    return json.loads(result.content[0].text)


async def test_lista_las_dos_herramientas(sessionmaker):
    async with Client(create_mcp(sessionmaker)) as client:
        tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == {"resumen_de_notas", "notas_de_materia"}
    # Ninguna herramienta permite elegir de qué estudiante son las notas
    for tool in tools.values():
        assert not any(
            "id" in p or "estudiante" in p for p in tool.input_schema.get("properties", {})
        )


async def test_herramientas_devuelven_las_notas_del_dueno_del_token(
    campus, sessionmaker, monkeypatch
):
    fake_identity(monkeypatch, campus["students"]["Beto"]["id"])
    summary = await call(sessionmaker, "resumen_de_notas")
    assert [m["codigo"] for m in summary["materias"]] == ["MAT-101"]

    fake_identity(monkeypatch, campus["students"]["Ana"]["id"])
    detail = await call(sessionmaker, "notas_de_materia", {"materia": "física"})
    assert detail["notas_por_lapso"]["lapso_1"] == 10


async def test_sin_identidad_las_herramientas_fallan(sessionmaker, monkeypatch):
    fake_identity(monkeypatch, None)
    with pytest.raises(ToolError, match="No autenticado"):
        await call(sessionmaker, "resumen_de_notas")


# --- Montaje HTTP ------------------------------------------------------------


async def test_endpoint_mcp_exige_token(client):
    response = await client.post(
        "http://test/mcp/",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Accept": "application/json, text/event-stream"},
    )
    assert response.status_code == 401
