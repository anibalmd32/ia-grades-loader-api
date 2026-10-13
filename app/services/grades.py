"""Notas: consulta y carga por parte del profesor de sus materias."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Enrollment, Grade, Subject, TeacherSubject, User
from app.models.enrollment import GRADE_DECIMALS, TERMS_PER_SEMESTER

TERMS = range(1, TERMS_PER_SEMESTER + 1)
_QUANTUM = Decimal(1).scaleb(-GRADE_DECIMALS)  # 0.01


def quantize(value: Decimal) -> Decimal:
    """Redondeo escolar (la mitad hacia arriba) a los decimales admitidos."""
    return value.quantize(_QUANTUM, rounding=ROUND_HALF_UP)


def average(values: list[Decimal | None]) -> Decimal | None:
    loaded = [v for v in values if v is not None]
    return quantize(sum(loaded, Decimal(0)) / len(loaded)) if loaded else None


@dataclass
class StudentRow:
    student: User
    grades: dict[int, Decimal | None]  # lapso -> nota

    @property
    def average(self) -> Decimal | None:
        return average(list(self.grades.values()))


async def teacher_subjects_with_counts(
    db: AsyncSession, teacher_id: int
) -> list[tuple[Subject, int]]:
    counts = (
        select(Enrollment.subject_id, func.count().label("students"))
        .group_by(Enrollment.subject_id)
        .subquery()
    )
    query = (
        select(Subject, func.coalesce(counts.c.students, 0))
        .join(TeacherSubject, TeacherSubject.subject_id == Subject.id)
        .outerjoin(counts, counts.c.subject_id == Subject.id)
        .where(TeacherSubject.teacher_id == teacher_id)
        .order_by(Subject.code)
    )
    return [(subject, count) for subject, count in await db.execute(query)]


async def get_teacher_subject(db: AsyncSession, teacher_id: int, subject_id: int) -> Subject:
    """La materia solo es visible para el profesor si la tiene asignada."""
    subject = await db.scalar(
        select(Subject)
        .join(TeacherSubject, TeacherSubject.subject_id == Subject.id)
        .where(Subject.id == subject_id, TeacherSubject.teacher_id == teacher_id)
    )
    if subject is None:
        raise NotFoundError("Materia no encontrada entre las que impartes")
    return subject


async def subject_roster(db: AsyncSession, subject_id: int) -> list[StudentRow]:
    """Estudiantes matriculados en la materia con sus notas por lapso."""
    enrollments = (
        await db.execute(
            select(Enrollment.id, User)
            .join(User, User.id == Enrollment.student_id)
            .where(Enrollment.subject_id == subject_id)
            .order_by(User.full_name)
        )
    ).all()
    grades: dict[int, dict[int, Decimal]] = defaultdict(dict)
    rows = await db.execute(
        select(Grade.enrollment_id, Grade.term, Grade.value).where(
            Grade.enrollment_id.in_([e for e, _ in enrollments])
        )
    )
    for enrollment_id, term, value in rows:
        grades[enrollment_id][term] = value
    return [
        StudentRow(student=user, grades={t: grades[enrollment_id].get(t) for t in TERMS})
        for enrollment_id, user in enrollments
    ]


async def save_grades(
    db: AsyncSession,
    subject_id: int,
    entries: list[tuple[int, int, Decimal | None]],
    updated_by_id: int,
) -> None:
    """Crea, modifica o borra (valor None) notas de (estudiante, lapso, valor).

    Todo o nada: si algún estudiante no está matriculado no se guarda ninguna.
    """
    student_ids = {student_id for student_id, _, _ in entries}
    enrollment_by_student = dict(
        (
            await db.execute(
                select(Enrollment.student_id, Enrollment.id).where(
                    Enrollment.subject_id == subject_id,
                    Enrollment.student_id.in_(student_ids),
                )
            )
        ).all()
    )
    if missing := sorted(student_ids - enrollment_by_student.keys()):
        raise NotFoundError(
            f"Estudiantes no matriculados en la materia: {', '.join(map(str, missing))}"
        )

    existing = {
        (g.enrollment_id, g.term): g
        for g in await db.scalars(
            select(Grade).where(Grade.enrollment_id.in_(enrollment_by_student.values()))
        )
    }
    for student_id, term, value in entries:
        enrollment_id = enrollment_by_student[student_id]
        grade = existing.get((enrollment_id, term))
        if value is None:
            if grade is not None:
                await db.execute(delete(Grade).where(Grade.id == grade.id))
        elif grade is None:
            db.add(
                Grade(
                    enrollment_id=enrollment_id, term=term, value=value, updated_by_id=updated_by_id
                )
            )
        elif grade.value != value:
            grade.value = value
            grade.updated_by_id = updated_by_id
    await db.commit()
