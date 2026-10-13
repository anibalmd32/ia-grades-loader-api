from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, SmallInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

TERMS_PER_SEMESTER = 3
MIN_GRADE = 1
MAX_GRADE = 20
# Las notas admiten decimales (p. ej. 15.75); se guardan como DECIMAL exacto
GRADE_DECIMALS = 2


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
    """Nota de un lapso. Una por lapso y matrícula."""

    __tablename__ = "grades"
    __table_args__ = (
        UniqueConstraint("enrollment_id", "term"),
        CheckConstraint(f"term BETWEEN 1 AND {TERMS_PER_SEMESTER}", name="term_range"),
        CheckConstraint(f"value BETWEEN {MIN_GRADE} AND {MAX_GRADE}", name="value_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(ForeignKey("enrollments.id", ondelete="CASCADE"))
    term: Mapped[int] = mapped_column(SmallInteger)
    value: Mapped[Decimal] = mapped_column(Numeric(4, GRADE_DECIMALS))
    # Último usuario (profesor/coordinador) que cargó o modificó la nota
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    enrollment: Mapped[Enrollment] = relationship(back_populates="grades")
