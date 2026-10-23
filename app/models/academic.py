from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class Semester(TimestampMixin, Base):
    __tablename__ = "semesters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    # Cerrado: ya no se cargan ni modifican notas, ni se cambian matrículas o materias
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Reglas de evaluación congeladas al cerrarlo (app.services.rules.snapshot). null = usa
    # la configuración académica vigente. Así cambiar la configuración no altera periodos
    # ya cerrados.
    rules: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))

    # lazy="raise": en async hay que cargarlas explícitamente (selectinload)
    subjects: Mapped[list["Subject"]] = relationship(back_populates="semester", lazy="raise")


class Section(TimestampMixin, Base):
    """Sección de un periodo (A, B, 01...). Cada estudiante cursa el periodo en una sola
    sección y cada materia tiene un único profesor por sección: así un estudiante nunca
    tiene dos profesores en la misma materia."""

    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("semester_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    semester_id: Mapped[int] = mapped_column(ForeignKey("semesters.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(20))

    semester: Mapped[Semester] = relationship(lazy="joined")


class StudentSection(Base):
    """Sección en la que cursa cada estudiante un periodo (una por periodo)."""

    __tablename__ = "student_sections"

    student_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    semester_id: Mapped[int] = mapped_column(
        ForeignKey("semesters.id", ondelete="CASCADE"), primary_key=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), index=True
    )


class Subject(TimestampMixin, Base):
    __tablename__ = "subjects"

    id: Mapped[int] = mapped_column(primary_key=True)
    semester_id: Mapped[int] = mapped_column(ForeignKey("semesters.id", ondelete="RESTRICT"))
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(150))

    # Se carga siempre junto a la materia (se muestra en todas las respuestas)
    semester: Mapped[Semester] = relationship(back_populates="subjects", lazy="joined")


class TeacherSubject(Base):
    """Profesor de cada materia en cada sección: uno solo por (materia, sección)."""

    __tablename__ = "teacher_subjects"

    subject_id: Mapped[int] = mapped_column(
        ForeignKey("subjects.id", ondelete="CASCADE"), primary_key=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), primary_key=True
    )
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
