"""Notas vistas por el propio estudiante.

Es la única fuente de datos para la consulta tradicional (F6), las preguntas en lenguaje
natural (F7) y Telegram (F8): todas reciben el id del estudiante autenticado, nunca uno
elegido por el usuario o por la IA.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.models import Enrollment, Grade, Semester, Subject, TeacherSubject, User
from app.models.enrollment import GRADE_DECIMALS, MAX_GRADE, MIN_GRADE
from app.services.grades import TERMS, average


class Standing(StrEnum):
    PASSED = "passed"  # todos los lapsos cargados y definitiva >= nota aprobatoria
    FAILED = "failed"  # todos los lapsos cargados y definitiva < nota aprobatoria
    SECURED = "secured"  # faltan lapsos, pero aprueba aunque saque la nota mínima
    IN_PROGRESS = "in_progress"  # faltan lapsos y aún puede aprobar
    IMPOSSIBLE = "impossible"  # faltan lapsos, pero ni con la nota máxima aprueba


@dataclass(frozen=True)
class Projection:
    standing: Standing
    final_grade: Decimal | None  # definitiva, solo con todos los lapsos cargados
    pending_terms: list[int]
    # Promedio mínimo que necesita en los lapsos pendientes para aprobar
    needed_average: Decimal | None


def project(grades: dict[int, Decimal | None]) -> Projection:
    """Definitiva = promedio simple de los lapsos (pesan igual); se aprueba con
    PASSING_GRADE. Calculado por el servidor para que el número sea siempre exacto."""
    passing = Decimal(str(get_settings().passing_grade))
    pending = [term for term, value in grades.items() if value is None]
    loaded_sum = sum((v for v in grades.values() if v is not None), Decimal(0))

    if not pending:
        final = average(list(grades.values()))
        standing = Standing.PASSED if final >= passing else Standing.FAILED
        return Projection(standing, final, [], None)

    # Lo que deben sumar los lapsos pendientes, repartido entre ellos y redondeado hacia
    # arriba: sacar exactamente esa nota garantiza aprobar
    needed = ((passing * len(grades) - loaded_sum) / len(pending)).quantize(
        Decimal(1).scaleb(-GRADE_DECIMALS), rounding=ROUND_CEILING
    )
    if needed <= MIN_GRADE:
        return Projection(Standing.SECURED, None, pending, None)
    if needed > MAX_GRADE:
        return Projection(Standing.IMPOSSIBLE, None, pending, None)
    return Projection(Standing.IN_PROGRESS, None, pending, needed)


@dataclass
class SubjectGrades:
    subject: Subject
    teachers: list[str]
    grades: dict[int, Decimal | None]  # lapso -> nota

    @property
    def average(self) -> Decimal | None:
        return average(list(self.grades.values()))

    @property
    def projection(self) -> Projection:
        return project(self.grades)


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


@dataclass
class SemesterGrades:
    semester: Semester
    subjects: list[SubjectGrades]

    @property
    def average(self) -> Decimal | None:
        return overall_average(self.subjects)

    def count(self, standing: Standing) -> int:
        return sum(1 for s in self.subjects if s.projection.standing == standing)


def by_semester(subjects: list[SubjectGrades]) -> list[SemesterGrades]:
    """Agrupa las materias por semestre, en el mismo orden en que llegan."""
    groups: dict[int, SemesterGrades] = {}
    for item in subjects:
        semester = item.subject.semester
        groups.setdefault(semester.id, SemesterGrades(semester, [])).subjects.append(item)
    return list(groups.values())
