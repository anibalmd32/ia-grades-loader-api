"""Notas: consulta y carga por parte del profesor de sus materias."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import InvalidDataError, NotFoundError
from app.models import ChangeSource, Enrollment, Grade, GradeChange, Subject, TeacherSubject, User
from app.services.evaluations import computed_terms
from app.services.rules import GradingRules


@dataclass
class StudentRow:
    student: User
    grades: dict[int, Decimal | None]  # lapso -> nota
    average: Decimal | None  # ponderado de los lapsos cargados
    final_grade: Decimal | None  # solo con todos los lapsos cargados
    passed: bool | None  # None mientras falten lapsos


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


async def grades_per_term(
    db: AsyncSession, rules_by_subject: dict[int, GradingRules]
) -> dict[int, dict[int, int]]:
    """Notas cargadas en cada lapso de cada materia (siempre todos sus lapsos, 0 si no hay).
    Recibe las reglas de cada materia (las de su periodo)."""
    subject_ids = list(rules_by_subject)
    counts = {sid: dict.fromkeys(r.terms, 0) for sid, r in rules_by_subject.items()}
    rows = await db.execute(
        select(Enrollment.subject_id, Grade.term, func.count())
        .join(Grade, Grade.enrollment_id == Enrollment.id)
        .where(Enrollment.subject_id.in_(subject_ids))
        .group_by(Enrollment.subject_id, Grade.term)
    )
    for subject_id, term, count in rows:
        if term in counts[subject_id]:
            counts[subject_id][term] = count
    return counts


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


def student_row(student: User, grades: dict[int, Decimal | None], rules: GradingRules):
    final = rules.final_grade(grades)
    return StudentRow(
        student=student,
        grades=grades,
        average=rules.average(grades),
        final_grade=final,
        passed=None if final is None else rules.passes(final),
    )


async def subject_roster(
    db: AsyncSession, subject_id: int, rules: GradingRules
) -> list[StudentRow]:
    """Estudiantes matriculados en la materia con sus notas por lapso."""
    enrollments = (
        await db.execute(
            select(Enrollment.id, User)
            .join(User, User.id == Enrollment.student_id)
            .where(Enrollment.subject_id == subject_id)
            .order_by(User.last_name, User.first_name)
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
        student_row(user, {t: grades[enrollment_id].get(t) for t in rules.terms}, rules)
        for enrollment_id, user in enrollments
    ]


def validate_entries(entries: list[tuple[int, int, Decimal | None]], rules: GradingRules) -> None:
    """Lapso y nota dentro de la configuración vigente (todo o nada)."""
    for student_id, term, value in entries:
        problem = rules.term_error(term) or (None if value is None else rules.grade_error(value))
        if problem:
            raise InvalidDataError(
                f"{problem} (estudiante {student_id}, {rules.term_label.lower()} {term})"
            )


async def save_grades(
    db: AsyncSession,
    subject_id: int,
    entries: list[tuple[int, int, Decimal | None]],
    updated_by_id: int,
    rules: GradingRules,
    import_job_id: str | None = None,
) -> None:
    """Crea, modifica o borra (valor None) notas de (estudiante, lapso, valor).

    Todo o nada: si algún estudiante no está matriculado o alguna nota no cumple la
    configuración académica no se guarda ninguna. Cada cambio efectivo queda en el
    historial (grade_changes) con su autor y su origen (a mano o importación).
    """
    validate_entries(entries, rules)
    computed = await computed_terms(db, subject_id)
    if blocked := sorted({term for _, term, _ in entries if term in computed}):
        names = ", ".join(rules.term_name(t).lower() for t in blocked)
        raise InvalidDataError(
            f"La nota del {names} se calcula con su plan de evaluación: carga las notas de "
            "cada evaluación"
        )
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
    source = ChangeSource.MANUAL if import_job_id is None else ChangeSource.IMPORT
    for student_id, term, value in entries:
        enrollment_id = enrollment_by_student[student_id]
        grade = existing.get((enrollment_id, term))
        old = None if grade is None else grade.value
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
        if old != value:
            db.add(
                GradeChange(
                    enrollment_id=enrollment_id,
                    term=term,
                    old_value=old,
                    new_value=value,
                    changed_by_id=updated_by_id,
                    source=source,
                    import_job_id=import_job_id,
                )
            )
    await db.commit()


async def history(
    db: AsyncSession,
    subject_id: int,
    *,
    student_id: int | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[GradeChange, User, str | None]], int]:
    """Cambios de notas de una materia, del más reciente al más antiguo.

    Devuelve (cambio, estudiante, nombre de quien lo hizo) y el total."""
    changer = aliased(User)
    query = (
        select(GradeChange, User, changer.first_name + " " + changer.last_name)
        .join(Enrollment, Enrollment.id == GradeChange.enrollment_id)
        .join(User, User.id == Enrollment.student_id)
        .outerjoin(changer, changer.id == GradeChange.changed_by_id)
        .where(Enrollment.subject_id == subject_id)
    )
    if student_id is not None:
        query = query.where(Enrollment.student_id == student_id)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = await db.execute(
        query.order_by(GradeChange.created_at.desc(), GradeChange.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [tuple(row) for row in rows], total or 0
