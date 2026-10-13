from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RevokedToken(Base):
    """Tokens invalidados por un cierre de sesión antes de su expiración.

    Solo hace falta guardarlos hasta que expiran; los vencidos se borran al
    registrar nuevos logouts.
    """

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(32), primary_key=True)
    # UTC sin zona horaria
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
