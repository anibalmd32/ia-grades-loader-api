from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BiometricDevice(Base):
    """Teléfono en el que el usuario activó el inicio de sesión con huella.

    La app guarda la credencial (`<id>.<secreto>`) y solo la envía tras verificar la huella.
    Del secreto solo se guarda el hash (SHA-256). Deja de valer al cambiar o restablecer la
    contraseña (`token_version`), igual que las sesiones.
    """

    __tablename__ = "biometric_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    token_version: Mapped[int] = mapped_column(Integer)
    # UTC sin zona horaria
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime)
