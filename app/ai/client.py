"""Cliente LLM agnóstico al proveedor.

Usa la API Chat Completions compatible con OpenAI, que ofrecen DeepSeek, OpenAI, Groq,
Ollama, etc. Cambiar de modelo o proveedor = cambiar LLM_BASE_URL, LLM_API_KEY y LLM_MODEL.
Nada en este módulo es específico de un proveedor.
"""

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from functools import lru_cache
from typing import Any

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)


ToolCaller = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class LLMNotConfiguredError(AppError):
    status_code = 503

    def __init__(self):
        super().__init__("El modelo de IA no está configurado (falta LLM_API_KEY)")


class LLMError(AppError):
    status_code = 502


class LLMClient:
    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None):
        self.settings = settings
        self._client = client or AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value() or "sin-clave",
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    async def complete_json[T: BaseModel](self, system: str, user: str, schema: type[T]) -> T:
        """Pide una respuesta en JSON y la valida contra `schema`.

        Si la respuesta no cumple el esquema se reintenta una vez indicándole al modelo
        el error, algo que suele bastar para corregirlo.
        """
        if not self.settings.llm_enabled:
            raise LLMNotConfiguredError
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        for attempt in range(2):
            content = await self._chat(messages)
            try:
                return schema.model_validate(json.loads(content))
            except (json.JSONDecodeError, ValidationError) as exc:
                # Sin el detalle: incluiría nombres y cédulas del Excel
                problem = (
                    f"{exc.error_count()} errores de validación"
                    if isinstance(exc, ValidationError)
                    else "JSON mal formado"
                )
                logger.warning("Respuesta del LLM inválida (intento %s): %s", attempt + 1, problem)
                messages += [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "Tu respuesta no es un JSON válido para el formato pedido. "
                            f"Error: {exc}. Responde de nuevo solo con el JSON corregido."
                        ),
                    },
                ]
        raise LLMError("La IA devolvió una respuesta con formato inválido")

    async def run_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        call_tool: ToolCaller,
        max_rounds: int = 4,
    ) -> str:
        """Igual que stream_tools() pero devuelve la respuesta completa."""
        answer = "".join(
            [part async for part in self.stream_tools(messages, tools, call_tool, max_rounds)]
        )
        if not answer:
            raise LLMError("La IA devolvió una respuesta vacía")
        return answer

    async def stream_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        call_tool: ToolCaller,
        max_rounds: int = 4,
    ) -> AsyncIterator[str]:
        """Conversación con herramientas (function calling) en streaming.

        Emite el texto de la respuesta a medida que llega. Si el modelo pide herramientas,
        el servidor las ejecuta con `call_tool`, le devuelve el resultado y sigue, hasta
        que responde con texto (o se agotan las rondas).
        """
        if not self.settings.llm_enabled:
            raise LLMNotConfiguredError
        messages = list(messages)
        for _ in range(max_rounds):
            text: list[str] = []
            calls: dict[int, dict[str, str]] = {}
            try:
                stream = await self._client.chat.completions.create(
                    model=self.settings.llm_model,
                    messages=messages,
                    temperature=self.settings.llm_temperature,
                    tools=tools,
                    stream=True,
                    max_tokens=self.settings.llm_chat_max_tokens,
                )
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        text.append(delta.content)
                        yield delta.content
                    # Las llamadas a herramientas también llegan troceadas: se acumulan
                    for part in delta.tool_calls or []:
                        call = calls.setdefault(part.index, {"id": "", "name": "", "args": ""})
                        call["id"] = part.id or call["id"]
                        if part.function:
                            call["name"] += part.function.name or ""
                            call["args"] += part.function.arguments or ""
            except OpenAIError as exc:
                logger.exception("Error llamando al LLM (%s)", self.settings.llm_provider)
                raise LLMError("No se pudo contactar con el servicio de IA") from exc

            if not calls:
                return
            messages.append(
                {
                    "role": "assistant",
                    "content": "".join(text) or None,
                    "tool_calls": [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": c["args"] or "{}"},
                        }
                        for c in calls.values()
                    ],
                }
            )
            for c in calls.values():
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": c["id"],
                        "content": json.dumps(
                            await self._safe_call(call_tool, c["name"], c["args"]),
                            ensure_ascii=False,
                        ),
                    }
                )
        raise LLMError("La IA no llegó a una respuesta")

    @staticmethod
    async def _safe_call(call_tool: ToolCaller, name: str, raw_args: str) -> dict[str, Any]:
        try:
            return await call_tool(name, json.loads(raw_args or "{}"))
        except (json.JSONDecodeError, ValidationError, KeyError) as exc:
            return {"error": f"Llamada inválida a la herramienta: {exc}"}

    async def _chat(self, messages: list[dict]) -> str:
        try:
            response = await self._client.chat.completions.create(
                model=self.settings.llm_model,
                messages=messages,
                temperature=self.settings.llm_temperature,
                response_format={"type": "json_object"},
            )
        except OpenAIError as exc:
            logger.exception("Error llamando al LLM (%s)", self.settings.llm_provider)
            raise LLMError("No se pudo contactar con el servicio de IA") from exc
        content = response.choices[0].message.content if response.choices else None
        if not content:
            raise LLMError("La IA devolvió una respuesta vacía")
        return content


@lru_cache
def get_llm() -> LLMClient:
    """Dependencia de FastAPI (se sustituye en los tests)."""
    return LLMClient(get_settings())
