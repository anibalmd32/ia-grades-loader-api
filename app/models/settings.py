from decimal import Decimal
from enum import StrEnum

from sqlalchemy import JSON, Boolean, CheckConstraint, Enum, Numeric, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

SETTINGS_ID = 1


class PeriodType(StrEnum):
    SEMESTER = "semester"
    TRIMESTER = "trimester"
    QUADRIMESTER = "quadrimester"
    ANNUAL = "annual"


class AcademicSettings(TimestampMixin, Base):
    """Reglas académicas de la institución (una sola fila, la edita el super admin).

    Si la fila no existe se usan los valores por defecto (DEFAULT_RULES): semestres de
    3 lapsos que pesan igual, notas del 1 al 20 con 2 decimales y se aprueba con 10.
    """

    __tablename__ = "academic_settings"
    __table_args__ = (CheckConstraint(f"id = {SETTINGS_ID}", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False, default=SETTINGS_ID)
    institution_name: Mapped[str] = mapped_column(String(150))
    period_type: Mapped[PeriodType] = mapped_column(
        Enum(PeriodType, name="period_type", values_callable=lambda e: [m.value for m in e])
    )
    terms_count: Mapped[int] = mapped_column(SmallInteger)
    # Cómo se llama cada evaluación del periodo: "Lapso", "Corte", "Parcial"...
    term_label: Mapped[str] = mapped_column(String(30))
    # Porcentaje de cada lapso (suman 100). null = todos pesan igual
    term_weights: Mapped[list[float] | None] = mapped_column(JSON)
    min_grade: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    max_grade: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    passing_grade: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    grade_decimals: Mapped[int] = mapped_column(SmallInteger)
    # True: la definitiva se redondea al entero (9.5 -> 10) antes de decidir si aprueba
    round_final_grade: Mapped[bool] = mapped_column(Boolean)
    # Documento de identidad de los estudiantes: cómo se llama y qué tipos hay
    document_label: Mapped[str] = mapped_column(String(30), default="Cédula")
    # [{"code": "V", "name": "Venezolano"}, ...]
    document_types: Mapped[list[dict[str, str]]] = mapped_column(JSON)
