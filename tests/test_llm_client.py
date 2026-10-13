import json

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, SecretStr

from app.ai.client import LLMClient, LLMError, LLMNotConfiguredError
from app.core.config import Settings


class Answer(BaseModel):
    value: int


def make_client(responses: list, api_key: str = "test-key") -> tuple[LLMClient, list[dict]]:
    """Cliente con un transporte HTTP falso que devuelve `responses` en orden."""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        item = responses[len(calls) - 1]
        if isinstance(item, int):
            return httpx.Response(item, json={"error": {"message": "fallo"}})
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": item},
                    }
                ],
            },
        )

    settings = Settings(
        llm_api_key=SecretStr(api_key), llm_model="modelo-x", llm_base_url="http://llm.test/v1"
    )
    openai = AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key="k",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return LLMClient(settings, openai), calls


async def test_respuesta_valida():
    client, calls = make_client(['{"value": 7}'])
    assert await client.complete_json("sistema", "usuario", Answer) == Answer(value=7)
    assert calls[0]["model"] == "modelo-x"
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in calls[0]["messages"]] == ["system", "user"]


async def test_reintenta_si_el_json_no_cumple_el_esquema():
    client, calls = make_client(['{"value": "no-numero"}', '{"value": 3}'])
    assert (await client.complete_json("s", "u", Answer)).value == 3
    assert len(calls) == 2
    assert "Error" in calls[1]["messages"][-1]["content"]


async def test_falla_tras_dos_respuestas_invalidas():
    client, _ = make_client(["no es json", "tampoco"])
    with pytest.raises(LLMError):
        await client.complete_json("s", "u", Answer)


async def test_error_del_proveedor():
    client, _ = make_client([500])
    with pytest.raises(LLMError):
        await client.complete_json("s", "u", Answer)


async def test_sin_api_key():
    client, calls = make_client([], api_key="")
    with pytest.raises(LLMNotConfiguredError):
        await client.complete_json("s", "u", Answer)
    assert calls == []
