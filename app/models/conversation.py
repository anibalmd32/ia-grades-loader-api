from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_job import utcnow


class ChatChannel(StrEnum):
    APP = "app"
    TELEGRAM = "telegram"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


def _values(enum_cls: type[StrEnum]) -> list[str]:
    return [m.value for m in enum_cls]


class Conversation(Base):
    """Conversación del estudiante con el asistente de notas (app o Telegram)."""

    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_student_id_updated_at", "student_id", "updated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    channel: Mapped[ChatChannel] = mapped_column(
        Enum(ChatChannel, name="chat_channel", values_callable=_values),
        default=ChatChannel.APP,
    )
    # Se toma de la primera pregunta
    title: Mapped[str | None] = mapped_column(String(120))
    # UTC sin zona horaria (igual que la cola de importaciones)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[MessageRole] = mapped_column(
        Enum(MessageRole, name="message_role", values_callable=_values)
    )
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
