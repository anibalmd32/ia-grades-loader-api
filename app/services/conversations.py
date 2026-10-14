"""Conversaciones guardadas del chat del estudiante."""

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import ChatChannel, Conversation, ConversationMessage, MessageRole
from app.models.import_job import utcnow

TITLE_LENGTH = 60


async def create(db: AsyncSession, student_id: int, channel: ChatChannel) -> Conversation:
    conversation = Conversation(student_id=student_id, channel=channel)
    db.add(conversation)
    await db.commit()
    return conversation


async def get(db: AsyncSession, student_id: int, conversation_id: int) -> Conversation:
    conversation = await db.get(Conversation, conversation_id)
    if conversation is None or conversation.student_id != student_id:
        raise NotFoundError("Conversación no encontrada")
    return conversation


async def current(db: AsyncSession, student_id: int, channel: ChatChannel) -> Conversation:
    """La conversación más reciente del canal, o una nueva si no hay (uso en Telegram)."""
    latest = await db.scalar(
        select(Conversation)
        .where(Conversation.student_id == student_id, Conversation.channel == channel)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(1)
    )
    return latest or await create(db, student_id, channel)


async def list_for(
    db: AsyncSession, student_id: int, channel: ChatChannel, limit: int, offset: int
) -> tuple[list[Conversation], int]:
    query = select(Conversation).where(
        Conversation.student_id == student_id, Conversation.channel == channel
    )
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    items = await db.scalars(
        query.order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(items), total or 0


async def messages(
    db: AsyncSession, conversation_id: int, last: int | None = None
) -> list[ConversationMessage]:
    """Mensajes en orden cronológico; con `last`, solo los últimos N."""
    query = select(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation_id
    )
    if last is None:
        return list(await db.scalars(query.order_by(ConversationMessage.id)))
    recent = await db.scalars(query.order_by(ConversationMessage.id.desc()).limit(last))
    return list(reversed(list(recent)))


async def add_message(
    db: AsyncSession, conversation: Conversation, role: MessageRole, content: str
) -> ConversationMessage:
    message = ConversationMessage(conversation_id=conversation.id, role=role, content=content)
    db.add(message)
    conversation.updated_at = utcnow()
    if conversation.title is None and role == MessageRole.USER:
        title = " ".join(content.split())
        conversation.title = (
            title if len(title) <= TITLE_LENGTH else title[: TITLE_LENGTH - 1] + "…"
        )
    await db.commit()
    return message


async def remove(db: AsyncSession, student_id: int, conversation_id: int) -> None:
    conversation = await get(db, student_id, conversation_id)
    await db.execute(
        delete(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id)
    )
    await db.delete(conversation)
    await db.commit()
