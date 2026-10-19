"""Notas vistas por el propio estudiante.

Es la única fuente de datos para la consulta tradicional (F6), las preguntas en lenguaje
natural (F7) y Telegram (F8): todas reciben el id del estudiante autenticado, nunca uno
elegido por el usuario o por la IA.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Enrollment, Evaluation, Grade, Semester, Subject, TeacherSubject, User
from app.services.evaluations import student_evaluations
from app.services.rules import GradingRules, for_semester


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


def project(grades: dict[int, Decimal | None], rules: GradingRules) -> Projection:
    """Situación de la materia según la configuración académica (pesos, escala, nota
    aprobatoria y redondeo). Calculada por el servidor para que el número sea exacto."""
    pending = [term for term in rules.terms if grades.get(term) is None]
    if not pending:
        final = rules.final_grade(grades)
        standing = Standing.PASSED if rules.passes(final) else Standing.FAILED
        return Projection(standing, final, [], None)

    needed = rules.needed_in_pending(grades)
    if needed <= rules.min_grade:
        return Projection(Standing.SECURED, None, pending, None)
    if needed > rules.max_grade:
        return Projection(Standing.IMPOSSIBLE, None, pending, None)
    return Projection(Standing.IN_PROGRESS, None, pending, needed)


@dataclass
class SubjectGrades:
    subject: Subject
    teachers: list[str]
    grades: dict[int, Decimal | None]  # lapso -> nota
    rules: GradingRules
    # Plan de evaluación de la materia con la nota del estudiante en cada evaluación
    evaluations: list[tuple[Evaluation, Decimal | None]] = field(default_factory=list)

    @property
    def average(self) -> Decimal | None:
        return self.rules.average(self.grades)

    @property
    def projection(self) -> Projection:
        return project(self.grades, self.rules)


async def student_subjects(
    db: AsyncSession, student_id: int, rules: GradingRules, subject_id: int | None = None
) -> list[SubjectGrades]:
    """Materias en las que está matriculado el estudiante, con sus notas por lapso.

    `rules` son las vigentes; cada materia se evalúa con las de su periodo (pueden estar
    congeladas si el periodo se cerró)."""
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
        .order_by(User.last_name, User.first_name)
    )
    for sid, name in rows:
        teachers[sid].append(name)

    plans = await student_evaluations(db, [e for e, _ in enrollments])

    result = []
    for enrollment_id, subject in enrollments:
        subject_rules = for_semester(rules, subject.semester)
        result.append(
            SubjectGrades(
                subject=subject,
                teachers=teachers[subject.id],
                grades={t: grades[enrollment_id].get(t) for t in subject_rules.terms},
                rules=subject_rules,
                evaluations=plans.get(enrollment_id, []),
            )
        )
    return result


async def student_subject(
    db: AsyncSession, student_id: int, subject_id: int, rules: GradingRules
) -> SubjectGrades:
    found = await student_subjects(db, student_id, rules, subject_id)
    if not found:
        raise NotFoundError("No estás matriculado en esa materia")
    return found[0]


def overall_average(subjects: list[SubjectGrades]) -> Decimal | None:
    """Promedio de los promedios de las materias que ya tienen alguna nota."""
    averages = [s.average for s in subjects if s.average is not None]
    if not averages:
        return None
    return subjects[0].rules.quantize(sum(averages, Decimal(0)) / len(averages))


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
