"""Vinculación de la cuenta de Telegram de un estudiante.

1. Desde la app el estudiante pide un enlace t.me/<bot>?start=<código> (create_link).
2. Al abrirlo, Telegram envía "/start <código>" al bot, que llama a consume() con el chat_id.
Un chat de Telegram solo puede estar vinculado a un estudiante, y viceversa.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.models import Role, User
from app.models.import_job import utcnow
from app.models.telegram_link import TelegramLinkCode


class TelegramNotConfiguredError(AppError):
    status_code = 503

    def __init__(self):
        super().__init__(
            "Telegram no está configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_BOT_USERNAME)"
        )


class InvalidLinkCodeError(AppError):
    def __init__(self):
        super().__init__("El enlace no es válido o ya caducó. Genera uno nuevo desde la app.")


@dataclass(frozen=True)
class LinkInvite:
    url: str
    expires_at: datetime


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def create_link(db: AsyncSession, student: User) -> LinkInvite:
    settings = get_settings()
    if not settings.telegram_enabled or not settings.telegram_bot_username:
        raise TelegramNotConfiguredError
    # Solo vale el último enlace generado
    await db.execute(delete(TelegramLinkCode).where(TelegramLinkCode.student_id == student.id))
    # Telegram admite en /start hasta 64 caracteres [A-Za-z0-9_-]: token_urlsafe cumple
    code = secrets.token_urlsafe(24)
    expires_at = utcnow() + timedelta(minutes=settings.telegram_link_code_ttl_minutes)
    db.add(TelegramLinkCode(code_hash=_hash(code), student_id=student.id, expires_at=expires_at))
    await db.commit()
    username = settings.telegram_bot_username.lstrip("@")
    return LinkInvite(url=f"https://t.me/{username}?start={code}", expires_at=expires_at)


async def consume(db: AsyncSession, code: str, chat_id: int) -> User:
    """Vincula el chat con el estudiante dueño del código. El código se borra al usarse."""
    link = await db.get(TelegramLinkCode, _hash(code))
    if link is None or link.expires_at < utcnow():
        raise InvalidLinkCodeError
    student = await db.get(User, link.student_id)
    await db.delete(link)
    if student is None or student.role != Role.STUDENT or not student.is_active:
        await db.commit()
        raise InvalidLinkCodeError
    # Si este Telegram estaba vinculado a otra cuenta, pasa a la nueva
    await db.execute(
        update(User)
        .where(User.telegram_chat_id == chat_id, User.id != student.id)
        .values(telegram_chat_id=None)
    )
    student.telegram_chat_id = chat_id
    await db.commit()
    return student


async def unlink(db: AsyncSession, student: User) -> None:
    student.telegram_chat_id = None
    await db.execute(delete(TelegramLinkCode).where(TelegramLinkCode.student_id == student.id))
    await db.commit()


async def student_for_chat(db: AsyncSession, chat_id: int) -> User | None:
    """Estudiante activo vinculado a ese chat de Telegram, si lo hay."""
    return await db.scalar(
        select(User).where(
            User.telegram_chat_id == chat_id, User.role == Role.STUDENT, User.is_active.is_(True)
        )
    )
