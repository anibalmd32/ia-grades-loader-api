"""Cliente LLM agnóstico al proveedor.

Usa la API Chat Completions compatible con OpenAI, que ofrecen DeepSeek, OpenAI, Groq,
Ollama, etc. Cambiar de modelo o proveedor = cambiar LLM_BASE_URL, LLM_API_KEY y LLM_MODEL.
Nada en este módulo es específico de un proveedor.
"""

import json
import logging
from functools import lru_cache

from openai import AsyncOpenAI, OpenAIError
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, get_settings
from app.core.errors import AppError

logger = logging.getLogger(__name__)


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
                logger.warning("Respuesta del LLM inválida (intento %s): %s", attempt + 1, exc)
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
