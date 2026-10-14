"""Chat en lenguaje natural del estudiante sobre sus notas (API y Telegram)."""

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import LLMClient
from app.ai.prompts import STUDENT_CHAT_SYSTEM
from app.ai.student_tools import TOOLS
from app.core.config import get_settings
from app.models import User


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


async def ask(
    db: AsyncSession,
    llm: LLMClient,
    student: User,
    question: str,
    history: list[ChatMessage] | None = None,
) -> str:
    """Responde una pregunta del estudiante usando solo sus propias notas."""
    max_history = get_settings().chat_max_history
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": STUDENT_CHAT_SYSTEM.format(nombre=student.full_name)},
        *({"role": m.role, "content": m.content} for m in (history or [])[-max_history:]),
        {"role": "user", "content": question},
    ]

    async def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = TOOLS.get(name)
        if tool is None:
            return {"error": f"Herramienta desconocida: {name}"}
        # El id del estudiante sale de la sesión, nunca de los argumentos del modelo
        return await tool.run(db, student.id, args)

    return await llm.run_tools(messages, [t.openai_schema() for t in TOOLS.values()], call_tool)
