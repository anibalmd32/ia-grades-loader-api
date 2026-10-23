from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, SmallInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Enrollment(TimestampMixin, Base):
    """Matrícula: un estudiante cursando una materia."""

    __tablename__ = "enrollments"
    __table_args__ = (UniqueConstraint("student_id", "subject_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="RESTRICT"))

    grades: Mapped[list["Grade"]] = relationship(
        back_populates="enrollment", cascade="all, delete-orphan", order_by="Grade.term"
    )


class Grade(TimestampMixin, Base):
    """Nota de un lapso. Una por lapso y matrícula.

    El rango del lapso y de la nota depende de la configuración académica (la valida
    app.services.rules), por eso no hay restricciones CHECK con números fijos.

    Un estudiante que no se presentó queda como inasistente (`absent`): su nota cuenta como
    la mínima de la escala, que es lo que se guarda en `value` para que promedios, definitiva
    y proyección no necesiten un caso aparte.
    """

    __tablename__ = "grades"
    __table_args__ = (UniqueConstraint("enrollment_id", "term"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"))
    term: Mapped[int] = mapped_column(SmallInteger)
    # Hasta 100 con 2 decimales (escala configurable)
    value: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    absent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Último usuario (profesor/coordinador) que cargó o modificó la nota
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    enrollment: Mapped[Enrollment] = relationship(back_populates="grades")
