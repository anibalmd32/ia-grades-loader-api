from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Numeric, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.import_job import utcnow


class ChangeSource(StrEnum):
    MANUAL = "manual"  # cargada o corregida a mano en la planilla
    IMPORT = "import"  # confirmada desde una importación de Excel
    EVALUATIONS = "evaluations"  # nota del lapso calculada a partir de sus evaluaciones


class GradeChange(Base):
    """Historial de notas: una fila por cada alta, corrección o borrado de una nota."""

    __tablename__ = "grade_changes"
    __table_args__ = (
        Index("ix_grade_changes_enrollment_id_created_at", "enrollment_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"))
    term: Mapped[int] = mapped_column(SmallInteger)
    # Nombre de la evaluación si el cambio es de una nota de evaluación; null = nota del lapso.
    # Se guarda el nombre (no el id) para que el historial se entienda aunque se borre.
    evaluation_name: Mapped[str | None] = mapped_column(String(100))
    # null en old_value = nota nueva; null en new_value = nota borrada
    old_value: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    new_value: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    # Inasistente antes / después del cambio (su valor es la nota mínima de la escala)
    old_absent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    new_absent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    changed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[ChangeSource] = mapped_column(
        Enum(
            ChangeSource, name="grade_change_source", values_callable=lambda e: [m.value for m in e]
        ),
        default=ChangeSource.MANUAL,
    )
    import_job_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("import_jobs.id", ondelete="SET NULL")
    )
    # UTC sin zona horaria
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
