"""Lógica del bot de Telegram, independiente de la librería (fácil de probar).

Cada función recibe el chat_id de Telegram y el texto, y devuelve la respuesta ya formateada
en el HTML que admite Telegram. El estudiante se identifica SIEMPRE por el chat vinculado:
nadie puede consultar notas ajenas escribiendo una cédula.
"""

import html
import logging
import re
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.client import LLMClient
from app.core.errors import AppError
from app.core.rate_limit import RateLimitedError
from app.models import ChatChannel
from app.services import chat, conversations, telegram_link

logger = logging.getLogger(__name__)

# Telegram rechaza mensajes de más de 4096 caracteres
MAX_MESSAGE_LENGTH = 4096

HELP = (
    "Soy el asistente de notas. Pregúntame lo que quieras sobre tus notas, por ejemplo:\n"
    "• ¿Cómo voy este semestre?\n"
    "• ¿Cuánto necesito en el 3er lapso de Física para aprobar?\n\n"
    "Comandos:\n"
    "/nueva — empezar una conversación nueva\n"
    "/desvincular — desvincular este Telegram de tu cuenta\n"
    "/ayuda — ver este mensaje"
)
NOT_LINKED = (
    "Este Telegram no está vinculado a ninguna cuenta. Para usarlo, entra en la app, ve a "
    "<b>Telegram</b> y pulsa <b>Vincular</b>: se abrirá este chat con tu enlace personal."
)


def to_telegram_html(markdown: str) -> str:
    """Convierte el Markdown sencillo que escribe la IA al HTML que entiende Telegram."""
    text = html.escape(markdown, quote=False)
    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.DOTALL)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
    return re.sub(r"^\s*[-*]\s+", "• ", text, flags=re.MULTILINE)


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Parte respuestas largas por párrafos para no superar el límite de Telegram."""
    parts: list[str] = []
    current = ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(paragraph) > limit:
            parts.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        parts.append(current)
    return parts or [""]


class TelegramBotService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        llm_factory: Callable[[], LLMClient],
    ):
        self.sessionmaker = sessionmaker
        self.llm_factory = llm_factory

    async def start(self, chat_id: int, code: str | None) -> str:
        async with self.sessionmaker() as db:
            if code:
                try:
                    student = await telegram_link.consume(db, code, chat_id)
                except AppError as exc:
                    return html.escape(exc.detail)
                return (
                    f"¡Listo, {html.escape(student.full_name)}! Tu cuenta quedó vinculada.\n\n"
                    + HELP
                )
            if await telegram_link.student_for_chat(db, chat_id) is None:
                return NOT_LINKED
            return HELP

    async def help(self, chat_id: int) -> str:
        return HELP

    async def new_conversation(self, chat_id: int) -> str:
        async with self.sessionmaker() as db:
            student = await telegram_link.student_for_chat(db, chat_id)
            if student is None:
                return NOT_LINKED
            await conversations.create(db, student.id, ChatChannel.TELEGRAM)
            return "Empezamos una conversación nueva. ¿Qué quieres saber de tus notas?"

    async def unlink(self, chat_id: int) -> str:
        async with self.sessionmaker() as db:
            student = await telegram_link.student_for_chat(db, chat_id)
            if student is None:
                return NOT_LINKED
            await telegram_link.unlink(db, student)
            return "Listo: este Telegram ya no está vinculado a tu cuenta."

    async def message(self, chat_id: int, text: str) -> str:
        async with self.sessionmaker() as db:
            student = await telegram_link.student_for_chat(db, chat_id)
            if student is None:
                return NOT_LINKED
            conversation = await conversations.current(db, student.id, ChatChannel.TELEGRAM)
            try:
                answer = await chat.ask(db, self.llm_factory(), student, conversation, text)
            except RateLimitedError as exc:
                return html.escape(exc.detail)
            except AppError as exc:
                logger.warning("Fallo respondiendo en Telegram al chat %s: %s", chat_id, exc)
                return "No pude responder en este momento. Inténtalo de nuevo en unos minutos."
            return to_telegram_html(answer)
