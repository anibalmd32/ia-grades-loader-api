"""Notas vistas por el propio estudiante.

Es la única fuente de datos para la consulta tradicional (F6), las preguntas en lenguaje
natural (F7) y Telegram (F8): todas reciben el id del estudiante autenticado, nunca uno
elegido por el usuario o por la IA.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Enrollment, Grade, Semester, Subject, TeacherSubject, User
from app.services.grades import TERMS, average


@dataclass
class SubjectGrades:
    subject: Subject
    teachers: list[str]
    grades: dict[int, Decimal | None]  # lapso -> nota

    @property
    def average(self) -> Decimal | None:
        return average(list(self.grades.values()))


async def student_subjects(
    db: AsyncSession, student_id: int, subject_id: int | None = None
) -> list[SubjectGrades]:
    """Materias en las que está matriculado el estudiante, con sus notas por lapso."""
    query = (
        select(Enrollment.id, Subject)
        .join(Subject, Subject.id == Enrollment.subject_id)
        .join(Semester, Semester.id == Subject.semester_id)
        .where(Enrollment.student_id == student_id)
        .order_by(Semester.name, Subject.code)
    )
    if subject_id is not None:
        query = query.where(Subject.id == subject_id)
    enrollments = (await db.execute(query)).all()
    if not enrollments:
        return []

    grades: dict[int, dict[int, Decimal]] = defaultdict(dict)
    rows = await db.execute(
        select(Grade.enrollment_id, Grade.term, Grade.value).where(
            Grade.enrollment_id.in_([e for e, _ in enrollments])
        )
    )
    for enrollment_id, term, value in rows:
        grades[enrollment_id][term] = value

    teachers: dict[int, list[str]] = defaultdict(list)
    rows = await db.execute(
        select(TeacherSubject.subject_id, User.full_name)
        .join(User, User.id == TeacherSubject.teacher_id)
        .where(TeacherSubject.subject_id.in_([s.id for _, s in enrollments]))
        .order_by(User.full_name)
    )
    for sid, name in rows:
        teachers[sid].append(name)

    return [
        SubjectGrades(
            subject=subject,
            teachers=teachers[subject.id],
            grades={t: grades[enrollment_id].get(t) for t in TERMS},
        )
        for enrollment_id, subject in enrollments
    ]


async def student_subject(db: AsyncSession, student_id: int, subject_id: int) -> SubjectGrades:
    found = await student_subjects(db, student_id, subject_id)
    if not found:
        raise NotFoundError("No estás matriculado en esa materia")
    return found[0]


def overall_average(subjects: list[SubjectGrades]) -> Decimal | None:
    """Promedio de los promedios de las materias que ya tienen alguna nota."""
    return average([s.average for s in subjects])
