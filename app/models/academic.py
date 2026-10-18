from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Semester(TimestampMixin, Base):
    __tablename__ = "semesters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    # Cerrado: ya no se cargan ni modifican notas, ni se cambian matrículas o materias
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)

    # lazy="raise": en async hay que cargarlas explícitamente (selectinload)
    subjects: Mapped[list["Subject"]] = relationship(back_populates="semester", lazy="raise")


class Subject(TimestampMixin, Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    semester_id: Mapped[int] = mapped_column(ForeignKey("semesters.id", ondelete="RESTRICT"))
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(150))

    # Se carga siempre junto a la materia (se muestra en todas las respuestas)
    semester: Mapped[Semester] = relationship(back_populates="subjects", lazy="joined")


class TeacherSubject(Base):
    """Materias que imparte cada profesor."""

    __tablename__ = "teacher_subjects"

    teacher_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True
    )
