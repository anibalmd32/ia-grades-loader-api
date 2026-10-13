from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"
    COORDINATOR = "coordinator"
    TEACHER = "teacher"
    STUDENT = "student"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Cédula: identificador de inicio de sesión de todos los roles
    cedula: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150))
    role: Mapped[Role] = mapped_column(
        Enum(Role, name="user_role", values_callable=lambda e: [r.value for r in e])
    )
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    # True mientras el usuario conserve la contraseña generada por el sistema
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
