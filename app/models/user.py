from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"
    COORDINATOR = "coordinator"
    TEACHER = "teacher"
    STUDENT = "student"


# Roles que inician sesión con email; los estudiantes lo hacen con cédula.
STAFF_ROLES = frozenset({Role.SUPER_ADMIN, Role.COORDINATOR, Role.TEACHER})


class Nationality(StrEnum):
    VENEZOLANO = "V"
    EXTRANJERO = "E"


def _enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


class User(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("nationality", "cedula"),
        CheckConstraint("cedula IS NULL OR cedula > 0", name="cedula_positive"),
        CheckConstraint("role = 'student' OR email IS NOT NULL", name="staff_requires_email"),
        CheckConstraint(
            "role <> 'student' OR (cedula IS NOT NULL AND nationality IS NOT NULL)",
            name="student_requires_cedula",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role", values_callable=_enum_values))
    full_name: Mapped[str] = mapped_column(String(150))
    # Credencial del personal (super admin, coordinador, profesor). Se guarda en minúsculas.
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    # Credencial de los estudiantes: tipo (V/E) + número de cédula sin puntos ni letras
    nationality: Mapped[Nationality | None] = mapped_column(
        Enum(Nationality, name="nationality", values_callable=_enum_values)
    )
    cedula: Mapped[int | None] = mapped_column(Integer)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    # True mientras el usuario conserve la contraseña generada por el sistema
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    # Se incrementa al cambiar o restablecer la contraseña: invalida todas las sesiones
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES

    @property
    def telegram_linked(self) -> bool:
        return self.telegram_chat_id is not None

    @property
    def cedula_display(self) -> str | None:
        if self.cedula is None or self.nationality is None:
            return None
        return f"{self.nationality.value}-{self.cedula}"
