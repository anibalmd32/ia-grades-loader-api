import json

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.ai.client import LLMClient, LLMError, get_llm
from app.ai.student_tools import TOOLS
from app.core.config import Settings
from app.main import app as fastapi_app
from app.models import Role

# --- Herramientas ------------------------------------------------------------


async def test_resumen_de_notas(campus, session):
    ana = campus["students"]["Ana"]["id"]
    result = await TOOLS["resumen_de_notas"].run(session, ana, {})
    assert result["escala"]["nota_aprobatoria"] == 10
    by_code = {m["codigo"]: m for m in result["materias"]}
    assert set(by_code) == {"FIS-101", "MAT-101", "MAT-201"}
    assert by_code["MAT-101"]["notas_por_lapso"] == {
        "lapso_1": 15,
        "lapso_2": 18.5,
        "lapso_3": None,
    }
    assert by_code["MAT-101"]["promedio"] == 16.75
    assert by_code["FIS-101"]["situacion"]["nota_minima_necesaria_en_cada_lapso_pendiente"] == 10
    assert by_code["FIS-101"]["situacion"]["lapsos_pendientes"] == [2, 3]
    assert by_code["MAT-101"]["situacion"]["estado"].startswith("aprobada asegurada")
    assert result["promedio_general"] == 13.38
    json.dumps(result)  # serializable para el modelo


@pytest.mark.parametrize("query", ["física", "FISICA", "fis-101", "Fís"])
async def test_notas_de_materia_por_nombre_o_codigo(campus, session, query):
    ana = campus["students"]["Ana"]["id"]
    result = await TOOLS["notas_de_materia"].run(session, ana, {"materia": query})
    assert result["codigo"] == "FIS-101"
    assert result["notas_por_lapso"]["lapso_1"] == 10


async def test_notas_de_materia_ambigua_o_inexistente(campus, session):
    ana = campus["students"]["Ana"]["id"]
    ambiguous = await TOOLS["notas_de_materia"].run(session, ana, {"materia": "mate"})
    assert "varias" in ambiguous["error"]
    assert {m["codigo"] for m in ambiguous["materias_del_estudiante"]} == {"MAT-101", "MAT-201"}

    missing = await TOOLS["notas_de_materia"].run(session, ana, {"materia": "química"})
    assert "No se encontró" in missing["error"]
    assert len(missing["materias_del_estudiante"]) == 3


async def test_las_herramientas_no_aceptan_id_de_estudiante():
    for tool in TOOLS.values():
        params = tool.openai_schema()["function"]["parameters"].get("properties", {})
        assert not any("id" in name or "estudiante" in name for name in params)


async def test_solo_devuelve_las_materias_propias(campus, session):
    beto = campus["students"]["Beto"]["id"]
    result = await TOOLS["resumen_de_notas"].run(session, beto, {})
    assert [m["codigo"] for m in result["materias"]] == ["MAT-101"]
    other = await TOOLS["notas_de_materia"].run(session, beto, {"materia": "Física"})
    assert "error" in other  # Física es de Ana, no de Beto


# --- Bucle de herramientas del cliente LLM (streaming) ------------------------


def chunk(delta: dict) -> str:
    body = {
        "id": "x",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }
    return f"data: {json.dumps(body)}\n\n"


def text(*parts: str) -> str:
    """Respuesta de texto troceada, como la envía el proveedor en streaming."""
    return "".join(chunk({"content": p}) for p in parts) + "data: [DONE]\n\n"


def tool_call(name: str, args: dict, call_id: str = "call_1") -> str:
    """Llamada a herramienta con los argumentos partidos en dos trozos."""
    raw = json.dumps(args)
    half = len(raw) // 2
    first = {"index": 0, "id": call_id, "type": "function"}
    return (
        chunk({"tool_calls": [{**first, "function": {"name": name, "arguments": raw[:half]}}]})
        + chunk({"tool_calls": [{"index": 0, "function": {"arguments": raw[half:]}}]})
        + "data: [DONE]\n\n"
    )


def llm_with(responses: list[str]) -> tuple[LLMClient, list[dict]]:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=responses[len(sent) - 1].encode(),
            headers={"content-type": "text/event-stream"},
        )

    settings = Settings(llm_api_key=SecretStr("k"), llm_model="m")
    client = AsyncOpenAI(
        base_url="http://llm.test/v1",
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return LLMClient(settings, client), sent


async def test_stream_tools_emite_trozos_y_ejecuta_herramientas():
    llm, sent = llm_with([tool_call("sumar", {"a": 2}), text("El resultado ", "es 3")])
    calls = []

    async def call_tool(name, args):
        calls.append((name, args))
        return {"resultado": args["a"] + 1}

    parts = [p async for p in llm.stream_tools([{"role": "user", "content": "?"}], [], call_tool)]
    assert parts == ["El resultado ", "es 3"]
    assert calls == [("sumar", {"a": 2})]  # argumentos reensamblados de dos trozos
    assert sent[0]["stream"] is True
    # Segunda petición: lleva la llamada del modelo y el resultado de la herramienta
    assert [m["role"] for m in sent[1]["messages"]] == ["user", "assistant", "tool"]
    assert sent[1]["messages"][1]["tool_calls"][0]["function"]["arguments"] == '{"a": 2}'
    assert json.loads(sent[1]["messages"][-1]["content"]) == {"resultado": 3}


async def test_run_tools_devuelve_la_respuesta_completa():
    llm, _ = llm_with([text("Hola, ", "Ana")])

    async def call_tool(name, args):
        return {}

    assert await llm.run_tools([{"role": "user", "content": "?"}], [], call_tool) == "Hola, Ana"


async def test_argumentos_invalidos_no_rompen():
    llm, sent = llm_with([tool_call("x", {}), text("ok")])

    async def call_tool(name, args):
        raise KeyError("falta 'a'")

    assert await llm.run_tools([{"role": "user", "content": "?"}], [], call_tool) == "ok"
    assert "Llamada inválida" in sent[1]["messages"][-1]["content"]


async def test_limita_las_rondas():
    llm, _ = llm_with([tool_call("x", {}, f"c{i}") for i in range(4)])

    async def call_tool(name, args):
        return {}

    with pytest.raises(LLMError):
        await llm.run_tools([{"role": "user", "content": "?"}], [], call_tool)


# --- Endpoints ---------------------------------------------------------------


class ScriptedLLM:
    """Simula al modelo: pide las herramientas indicadas y responde en trozos."""

    def __init__(self, tool_requests: list[tuple[str, dict]], parts=("respuesta ", "simulada")):
        self.tool_requests = tool_requests
        self.parts = parts
        self.calls: list[list[dict]] = []
        self.results: list[dict] = []

    async def stream_tools(self, messages, tools, call_tool):
        self.calls.append(messages)
        self.tools = tools
        for name, args in self.tool_requests:
            self.results.append(await call_tool(name, args))
        for part in self.parts:
            if isinstance(part, Exception):
                raise part
            yield part

    async def run_tools(self, messages, tools, call_tool):
        return "".join([p async for p in self.stream_tools(messages, tools, call_tool)])

    @property
    def messages(self) -> list[dict]:
        return self.calls[-1]


@pytest.fixture
def scripted_llm():
    def _use(tool_requests, **kwargs) -> ScriptedLLM:
        fake = ScriptedLLM(tool_requests, **kwargs)
        fastapi_app.dependency_overrides[get_llm] = lambda: fake
        return fake

    yield _use
    fastapi_app.dependency_overrides.pop(get_llm, None)


async def ask(client, headers, message, conversation_id=None):
    response = await client.post(
        "/student/chat",
        json={"message": message, "conversation_id": conversation_id},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_chat_usa_las_notas_del_estudiante_autenticado(client, campus, scripted_llm):
    fake = scripted_llm([("resumen_de_notas", {}), ("inventada", {})])
    beto = campus["students"]["Beto"]
    body = await ask(client, beto["headers"], "¿Cómo voy?")
    assert body["answer"] == "respuesta simulada"
    assert isinstance(body["conversation_id"], int)

    assert [m["role"] for m in fake.messages] == ["system", "user"]
    assert "Beto" in fake.messages[0]["content"]
    assert {t["function"]["name"] for t in fake.tools} == set(TOOLS)
    # La herramienta devolvió las notas de Beto (no las de Ana)
    assert [m["codigo"] for m in fake.results[0]["materias"]] == ["MAT-101"]
    assert "desconocida" in fake.results[1]["error"]


async def test_la_conversacion_se_guarda_y_da_contexto(client, campus, scripted_llm):
    fake = scripted_llm([])
    headers = campus["students"]["Ana"]["headers"]
    first = await ask(client, headers, "Hola")
    conversation_id = first["conversation_id"]
    await ask(client, headers, "¿Y en Física?", conversation_id)

    # La segunda pregunta lleva el intercambio anterior como contexto
    assert [(m["role"], m["content"]) for m in fake.messages[1:]] == [
        ("user", "Hola"),
        ("assistant", "respuesta simulada"),
        ("user", "¿Y en Física?"),
    ]

    listed = (await client.get("/student/conversations", headers=headers)).json()
    assert listed["total"] == 1
    assert listed["items"][0]["title"] == "Hola"

    url = f"/student/conversations/{conversation_id}"
    detail = (await client.get(url, headers=headers)).json()
    assert [(m["role"], m["content"]) for m in detail["messages"]] == [
        ("user", "Hola"),
        ("assistant", "respuesta simulada"),
        ("user", "¿Y en Física?"),
        ("assistant", "respuesta simulada"),
    ]


async def test_historial_recortado(client, campus, scripted_llm, monkeypatch):
    from app.core.config import get_settings

    fake = scripted_llm([])
    headers = campus["students"]["Ana"]["headers"]
    conversation_id = (await ask(client, headers, "m1"))["conversation_id"]
    await ask(client, headers, "m2", conversation_id)
    monkeypatch.setattr(get_settings(), "chat_max_history", 2)
    await ask(client, headers, "m3", conversation_id)
    # Solo los 2 mensajes previos más recientes + la pregunta actual
    assert [m["content"] for m in fake.messages[1:]] == ["m2", "respuesta simulada", "m3"]


async def test_conversaciones_privadas(client, campus, scripted_llm):
    scripted_llm([])
    ana = campus["students"]["Ana"]["headers"]
    beto = campus["students"]["Beto"]["headers"]
    conversation_id = (await ask(client, ana, "Hola"))["conversation_id"]

    url = f"/student/conversations/{conversation_id}"
    assert (await client.get(url, headers=beto)).status_code == 404
    response = await client.post(
        "/student/chat", json={"message": "?", "conversation_id": conversation_id}, headers=beto
    )
    assert response.status_code == 404
    assert (await client.get("/student/conversations", headers=beto)).json()["total"] == 0


async def test_borrar_conversacion(client, campus, scripted_llm):
    scripted_llm([])
    headers = campus["students"]["Ana"]["headers"]
    conversation_id = (await ask(client, headers, "Hola"))["conversation_id"]
    url = f"/student/conversations/{conversation_id}"
    assert (await client.delete(url, headers=headers)).status_code == 204
    assert (await client.get(url, headers=headers)).status_code == 404


def parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.replace("\r\n", "\n").strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events.append((fields.get("event"), json.loads(fields["data"])))
    return events


async def test_chat_en_streaming(client, campus, scripted_llm):
    scripted_llm([], parts=("Vas ", "bien ", "en todo"))
    headers = campus["students"]["Ana"]["headers"]
    response = await client.post(
        "/student/chat/stream", json={"message": "¿Cómo voy?"}, headers=headers
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(response.text)
    assert events[0][0] == "conversation"
    conversation_id = events[0][1]["id"]
    assert [d["text"] for e, d in events if e == "delta"] == ["Vas ", "bien ", "en todo"]
    assert events[-1] == ("done", {})

    # La respuesta completa quedó guardada al terminar el stream
    url = f"/student/conversations/{conversation_id}"
    detail = (await client.get(url, headers=headers)).json()
    assert detail["messages"][-1]["role"] == "assistant"
    assert detail["messages"][-1]["content"] == "Vas bien en todo"


async def test_streaming_informa_errores_y_guarda_lo_recibido(client, campus, scripted_llm):
    scripted_llm([], parts=("Empiezo a ", LLMError("La IA se cayó")))
    headers = campus["students"]["Ana"]["headers"]
    response = await client.post("/student/chat/stream", json={"message": "?"}, headers=headers)
    events = parse_sse(response.text)
    assert events[-1] == ("error", {"detail": "La IA se cayó"})

    url = f"/student/conversations/{events[0][1]['id']}"
    detail = (await client.get(url, headers=headers)).json()
    assert detail["messages"][-1]["content"] == "Empiezo a "


async def test_chat_valida_entrada(client, campus, scripted_llm):
    scripted_llm([])
    headers = campus["students"]["Ana"]["headers"]
    empty = await client.post("/student/chat", json={"message": ""}, headers=headers)
    assert empty.status_code == 422
    missing = await client.post(
        "/student/chat", json={"message": "?", "conversation_id": 999}, headers=headers
    )
    assert missing.status_code == 404


async def test_chat_solo_estudiantes(client, login_as, scripted_llm):
    scripted_llm([])
    headers = await login_as(Role.TEACHER, email="t@u.edu")
    for url in ("/student/chat", "/student/chat/stream"):
        assert (await client.post(url, json={"message": "?"}, headers=headers)).status_code == 403
    assert (await client.get("/student/conversations", headers=headers)).status_code == 403
