from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TelegramLinkCode(Base):
    """Código de un solo uso para vincular la cuenta de Telegram de un estudiante.

    Solo se guarda el hash (SHA-256): si alguien lee la tabla no puede usar los códigos.
    """

    __tablename__ = "telegram_link_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # UTC sin zona horaria
    expires_at: Mapped[datetime] = mapped_column(DateTime)
