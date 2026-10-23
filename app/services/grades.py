"""Notas: consulta y carga por parte del profesor de sus materias (en cada sección)."""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.errors import InvalidDataError, NotFoundError
from app.models import (
    ChangeSource,
    Enrollment,
    Grade,
    GradeChange,
    Section,
    StudentSection,
    Subject,
    TeacherSubject,
    User,
)
from app.services import evaluations
from app.services.evaluations import GradeUpdate, computed_terms
from app.services.rules import GradingRules
from app.services.sections import in_section


@dataclass
class StudentRow:
    student: User
    grades: dict[int, Decimal | None]  # lapso -> nota
    average: Decimal | None  # ponderado de los lapsos cargados
    final_grade: Decimal | None  # solo con todos los lapsos cargados
    passed: bool | None  # None mientras falten lapsos
    absent: set[int] = field(default_factory=set)  # lapsos en los que es inasistente


async def teacher_subjects_with_counts(
    db: AsyncSession, teacher_id: int
) -> list[tuple[Subject, Section, int]]:
    """(materia, sección, estudiantes matriculados en la materia en esa sección)."""
    counts = (
        select(Enrollment.subject_id, StudentSection.section_id, func.count().label("students"))
        .join(Subject, Subject.id == Enrollment.subject_id)
        .join(
            StudentSection,
            (StudentSection.student_id == Enrollment.student_id)
            & (StudentSection.semester_id == Subject.semester_id),
        )
        .group_by(Enrollment.subject_id, StudentSection.section_id)
        .subquery()
    )
    query = (
        select(Subject, Section, func.coalesce(counts.c.students, 0))
        .join(TeacherSubject, TeacherSubject.subject_id == Subject.id)
        .join(Section, Section.id == TeacherSubject.section_id)
        .outerjoin(
            counts,
            (counts.c.subject_id == Subject.id) & (counts.c.section_id == Section.id),
        )
        .where(TeacherSubject.teacher_id == teacher_id)
        .order_by(Subject.code, Section.name)
    )
    return [tuple(row) for row in await db.execute(query)]


async def grades_per_term(
    db: AsyncSession, rules_by_class: dict[tuple[int, int], GradingRules]
) -> dict[tuple[int, int], dict[int, int]]:
    """Notas cargadas en cada lapso de cada (materia, sección) (siempre todos sus lapsos, 0
    si no hay). Recibe las reglas de cada materia (las de su periodo)."""
    counts = {key: dict.fromkeys(r.terms, 0) for key, r in rules_by_class.items()}
    rows = await db.execute(
        select(Enrollment.subject_id, StudentSection.section_id, Grade.term, func.count())
        .join(Grade, Grade.enrollment_id == Enrollment.id)
        .join(Subject, Subject.id == Enrollment.subject_id)
        .join(
            StudentSection,
            (StudentSection.student_id == Enrollment.student_id)
            & (StudentSection.semester_id == Subject.semester_id),
        )
        .where(Enrollment.subject_id.in_({s for s, _ in rules_by_class}))
        .group_by(Enrollment.subject_id, StudentSection.section_id, Grade.term)
    )
    for subject_id, section_id, term, count in rows:
        if term in counts.get((subject_id, section_id), {}):
            counts[(subject_id, section_id)][term] = count
    return counts


async def get_teacher_subject(
    db: AsyncSession, teacher_id: int, subject_id: int, section_id: int
) -> tuple[Subject, Section]:
    """La materia solo es visible para el profesor en las secciones que tiene asignadas."""
    row = (
        await db.execute(
            select(Subject, Section)
            .join(TeacherSubject, TeacherSubject.subject_id == Subject.id)
            .join(Section, Section.id == TeacherSubject.section_id)
            .where(
                Subject.id == subject_id,
                Section.id == section_id,
                TeacherSubject.teacher_id == teacher_id,
            )
        )
    ).first()
    if row is None:
        raise NotFoundError("Materia no encontrada entre las que impartes")
    return row[0], row[1]


def student_row(
    student: User,
    grades: dict[int, Decimal | None],
    rules: GradingRules,
    absent: set[int] | None = None,
):
    final = rules.final_grade(grades)
    return StudentRow(
        student=student,
        grades=grades,
        average=rules.average(grades),
        final_grade=final,
        passed=None if final is None else rules.passes(final),
        absent=absent or set(),
    )


async def subject_roster(
    db: AsyncSession, subject_id: int, section_id: int, rules: GradingRules
) -> list[StudentRow]:
    """Estudiantes de la sección matriculados en la materia con sus notas por lapso."""
    enrollments = (
        await db.execute(
            select(Enrollment.id, User)
            .join(User, User.id == Enrollment.student_id)
            .where(Enrollment.subject_id == subject_id, in_section(section_id))
            .order_by(User.last_name, User.first_name)
        )
    ).all()
    grades: dict[int, dict[int, Decimal]] = defaultdict(dict)
    absent: dict[int, set[int]] = defaultdict(set)
    rows = await db.execute(
        select(Grade.enrollment_id, Grade.term, Grade.value, Grade.absent).where(
            Grade.enrollment_id.in_([e for e, _ in enrollments])
        )
    )
    for enrollment_id, term, value, is_absent in rows:
        grades[enrollment_id][term] = value
        if is_absent:
            absent[enrollment_id].add(term)
    return [
        student_row(
            user,
            {t: grades[enrollment_id].get(t) for t in rules.terms},
            rules,
            absent[enrollment_id],
        )
        for enrollment_id, user in enrollments
    ]


# (estudiante, lapso, nota, inasistente)
GradeEntry = tuple[int, int, Decimal | None, bool]


def validate_entries(entries: list[GradeEntry], rules: GradingRules) -> None:
    """Lapso y nota dentro de la configuración vigente (todo o nada)."""
    for student_id, term, value, _ in entries:
        problem = rules.term_error(term) or (None if value is None else rules.grade_error(value))
        if problem:
            raise InvalidDataError(
                f"{problem} (estudiante {student_id}, {rules.term_label.lower()} {term})"
            )


async def save_grades(
    db: AsyncSession,
    subject_id: int,
    section_id: int,
    entries: list[GradeEntry],
    updated_by_id: int,
    rules: GradingRules,
    import_job_id: str | None = None,
) -> list[GradeUpdate]:
    """Crea, modifica o borra (valor None) notas de (estudiante, lapso, valor, inasistente).

    Un inasistente guarda la nota mínima de la escala. En un lapso con plan de evaluación la
    nota no se carga directamente, pero sí se puede marcar al estudiante como inasistente
    en todo el lapso (lo queda en cada evaluación).

    Todo o nada: si algún estudiante no está matriculado en la sección o alguna nota no
    cumple la configuración académica no se guarda ninguna. Cada cambio efectivo queda en
    el historial (grade_changes) con su autor y su origen (a mano o importación) y se
    devuelve (para avisar a los estudiantes).
    """
    validate_entries(entries, rules)
    computed = await computed_terms(db, subject_id, section_id)
    if blocked := sorted(
        {term for _, term, _, absent in entries if term in computed and not absent}
    ):
        names = ", ".join(rules.term_name(t).lower() for t in blocked)
        raise InvalidDataError(
            f"La nota del {names} se calcula con su plan de evaluación: carga las notas de "
            "cada evaluación"
        )
    student_ids = {entry[0] for entry in entries}
    enrollment_by_student = dict(
        (
            await db.execute(
                select(Enrollment.student_id, Enrollment.id).where(
                    Enrollment.subject_id == subject_id,
                    Enrollment.student_id.in_(student_ids),
                    in_section(section_id),
                )
            )
        ).all()
    )
    if missing := sorted(student_ids - enrollment_by_student.keys()):
        raise NotFoundError(
            "Estudiantes no matriculados en la materia en esta sección: "
            + ", ".join(map(str, missing))
        )

    existing = {
        (g.enrollment_id, g.term): g
        for g in await db.scalars(
            select(Grade).where(Grade.enrollment_id.in_(enrollment_by_student.values()))
        )
    }
    source = ChangeSource.MANUAL if import_job_id is None else ChangeSource.IMPORT
    updates = []
    absent_in_computed: dict[int, list[int]] = defaultdict(list)
    for student_id, term, value, absent in entries:
        if term in computed:
            absent_in_computed[term].append(student_id)
            continue
        if absent:
            value = rules.min_grade
        enrollment_id = enrollment_by_student[student_id]
        grade = existing.get((enrollment_id, term))
        old = None if grade is None else grade.value
        old_absent = grade is not None and grade.absent
        if value is None:
            if grade is not None:
                await db.execute(delete(Grade).where(Grade.id == grade.id))
        elif grade is None:
            db.add(
                Grade(
                    enrollment_id=enrollment_id,
                    term=term,
                    value=value,
                    absent=absent,
                    updated_by_id=updated_by_id,
                )
            )
        elif (grade.value, grade.absent) != (value, absent):
            grade.value = value
            grade.absent = absent
            grade.updated_by_id = updated_by_id
        if (old, old_absent) != (value, absent):
            db.add(
                GradeChange(
                    enrollment_id=enrollment_id,
                    term=term,
                    old_value=old,
                    new_value=value,
                    old_absent=old_absent,
                    new_absent=absent,
                    changed_by_id=updated_by_id,
                    source=source,
                    import_job_id=import_job_id,
                )
            )
            updates.append(GradeUpdate(student_id, term, None, value, absent))
    for term, students in sorted(absent_in_computed.items()):
        updates += await evaluations.mark_term_absent(
            db, subject_id, section_id, term, students, updated_by_id, rules
        )
    await db.commit()
    return updates


async def history(
    db: AsyncSession,
    subject_id: int,
    *,
    student_id: int | None,
    limit: int,
    offset: int,
    section_id: int | None = None,
) -> tuple[list[tuple[GradeChange, User, str | None]], int]:
    """Cambios de notas de una materia (o de una sección), del más reciente al más antiguo.

    Devuelve (cambio, estudiante, nombre de quien lo hizo) y el total."""
    changer = aliased(User)
    query = (
        select(GradeChange, User, changer.first_name + " " + changer.last_name)
        .join(Enrollment, Enrollment.id == GradeChange.enrollment_id)
        .join(User, User.id == Enrollment.student_id)
        .outerjoin(changer, changer.id == GradeChange.changed_by_id)
        .where(Enrollment.subject_id == subject_id)
    )
    if section_id is not None:
        query = query.where(in_section(section_id))
    if student_id is not None:
        query = query.where(Enrollment.student_id == student_id)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    rows = await db.execute(
        query.order_by(GradeChange.created_at.desc(), GradeChange.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [tuple(row) for row in rows], total or 0
