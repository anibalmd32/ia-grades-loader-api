from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

INSTALLATION_ID = 1


class Installation(Base):
    """Registro único que marca el sistema como instalado.

    La PK está fijada a 1 por una restricción CHECK: si dos peticiones intentan
    instalar a la vez, la segunda falla en el INSERT, así que nunca puede haber
    dos super admins creados por el instalador.
    """

    __tablename__ = "installation"
    __table_args__ = (CheckConstraint(f"id = {INSTALLATION_ID}", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    super_admin_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    installed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
