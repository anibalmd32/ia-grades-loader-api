"""Chat en lenguaje natural del estudiante sobre sus notas (app y Telegram).

El historial vive en la base de datos (tabla conversations): el cliente solo indica en qué
conversación pregunta.
"""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import LLMClient
from app.ai.prompts import LANGUAGE_REMINDER, TELEGRAM_STYLE, student_chat_system
from app.ai.student_tools import TOOLS
from app.core.config import get_settings
from app.core.rate_limit import RateLimiter
from app.models import ChatChannel, Conversation, MessageRole, User
from app.services import conversations, rules

# Preguntas por minuto y estudiante, sumando app y Telegram (cada una llama a la IA)
question_limiter = RateLimiter(lambda: get_settings().chat_rate_limit_per_minute)


async def _prepare(
    db: AsyncSession, student: User, conversation: Conversation, question: str
) -> tuple[list[dict[str, Any]], Any]:
    """Carga el contexto, guarda la pregunta y devuelve (mensajes, ejecutor de herramientas)."""
    question_limiter.hit(student.id)
    history = await conversations.messages(
        db, conversation.id, last=get_settings().chat_max_history
    )
    await conversations.add_message(db, conversation, MessageRole.USER, question)
    system = student_chat_system(student.first_name, await rules.get_rules(db))
    if conversation.channel == ChatChannel.TELEGRAM:
        system += TELEGRAM_STYLE
    system += LANGUAGE_REMINDER
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        *({"role": m.role.value, "content": m.content} for m in history),
        {"role": "user", "content": question},
    ]

    async def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = TOOLS.get(name)
        if tool is None:
            return {"error": "Esa consulta no existe. Usa solo las disponibles."}
        # El id del estudiante sale de la sesión, nunca de los argumentos del modelo
        return await tool.run(db, student.id, args)

    return messages, call_tool


def _tool_schemas() -> list[dict[str, Any]]:
    return [t.openai_schema() for t in TOOLS.values()]


async def ask(
    db: AsyncSession, llm: LLMClient, student: User, conversation: Conversation, question: str
) -> str:
    """Responde una pregunta usando solo las notas del estudiante y la guarda."""
    messages, call_tool = await _prepare(db, student, conversation, question)
    answer = await llm.run_tools(messages, _tool_schemas(), call_tool)
    await conversations.add_message(db, conversation, MessageRole.ASSISTANT, answer)
    return answer


async def ask_stream(
    db: AsyncSession, llm: LLMClient, student: User, conversation: Conversation, question: str
) -> AsyncIterator[str]:
    """Igual que ask() pero emitiendo la respuesta a medida que la escribe la IA.

    Lo recibido se guarda aunque el cliente corte la conexión a mitad.
    """
    messages, call_tool = await _prepare(db, student, conversation, question)
    parts: list[str] = []
    try:
        async for part in llm.stream_tools(messages, _tool_schemas(), call_tool):
            parts.append(part)
            yield part
    finally:
        if parts:
            await conversations.add_message(db, conversation, MessageRole.ASSISTANT, "".join(parts))
