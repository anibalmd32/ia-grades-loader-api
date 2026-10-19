from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Evaluation(TimestampMixin, Base):
    """Evaluación del plan de un lapso (examen, taller, exposición...). La define el profesor.

    Si un lapso de la materia tiene evaluaciones, su nota ya no se carga a mano: la calcula
    app.services.evaluations (suma de cada nota por su porcentaje) cuando el plan llega al
    100 % y el estudiante tiene todas las evaluaciones calificadas.
    """

    __tablename__ = "evaluations"
    __table_args__ = (UniqueConstraint("subject_id", "term", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    term: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str] = mapped_column(String(100))
    # Porcentaje de la nota del lapso (los del lapso suman 100 como máximo)
    weight: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    due_date: Mapped[date | None] = mapped_column(Date)


class EvaluationGrade(TimestampMixin, Base):
    """Nota de un estudiante en una evaluación (en la escala del periodo)."""

    __tablename__ = "evaluation_grades"
    __table_args__ = (UniqueConstraint("evaluation_id", "enrollment_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"))
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"))
    value: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
