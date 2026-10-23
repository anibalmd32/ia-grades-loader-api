"""Secciones de cada periodo y sección en la que cursa cada estudiante.

Un estudiante cursa cada periodo en una sola sección y cada materia tiene un único profesor
por sección (TeacherSubject), así que un estudiante nunca tiene dos profesores en la misma
materia. Para matricularlo en una materia, antes hay que asignarle sección en su periodo.
"""

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import (
    Enrollment,
    EvaluationGrade,
    Grade,
    Role,
    Section,
    Semester,
    StudentSection,
    Subject,
    TeacherSubject,
    User,
)
from app.services import academic, users


def in_section(section_id: int):
    """Condición: la matrícula es de un estudiante de esa sección (la sección ya determina
    el periodo, que es el de la materia)."""
    return Enrollment.student_id.in_(
        select(StudentSection.student_id).where(StudentSection.section_id == section_id)
    )


async def get_section(db: AsyncSession, section_id: int) -> Section:
    section = await db.get(Section, section_id)
    if section is None:
        raise NotFoundError("Sección no encontrada")
    return section


async def _ensure_name_free(
    db: AsyncSession, semester_id: int, name: str, exclude_id: int | None = None
) -> None:
    query = select(Section.id).where(Section.semester_id == semester_id, Section.name == name)
    if exclude_id is not None:
        query = query.where(Section.id != exclude_id)
    if await db.scalar(query) is not None:
        raise ConflictError(f"Ya existe la sección «{name}» en ese periodo")


async def list_sections(db: AsyncSession, semester_id: int) -> list[Section]:
    await academic.get_semester(db, semester_id)
    return list(
        await db.scalars(
            select(Section).where(Section.semester_id == semester_id).order_by(Section.name)
        )
    )


async def students_per_section(db: AsyncSession, section_ids: list[int]) -> dict[int, int]:
    rows = await db.execute(
        select(StudentSection.section_id, func.count())
        .where(StudentSection.section_id.in_(section_ids))
        .group_by(StudentSection.section_id)
    )
    return {sid: 0 for sid in section_ids} | dict(rows.all())


async def create_section(db: AsyncSession, semester_id: int, name: str) -> Section:
    await academic.ensure_open(db, await academic.get_semester(db, semester_id))
    await _ensure_name_free(db, semester_id, name)
    section = Section(semester_id=semester_id, name=name)
    db.add(section)
    await db.commit()
    await db.refresh(section, ["semester"])
    return section


async def rename_section(db: AsyncSession, section_id: int, name: str) -> Section:
    section = await get_section(db, section_id)
    await _ensure_name_free(db, section.semester_id, name, exclude_id=section_id)
    section.name = name
    await db.commit()
    return section


async def delete_section(db: AsyncSession, section_id: int) -> None:
    section = await get_section(db, section_id)
    await academic.ensure_open(db, section.semester)
    if await db.scalar(select(exists().where(StudentSection.section_id == section_id))):
        raise ConflictError("No se puede eliminar una sección con estudiantes asignados")
    if await db.scalar(select(exists().where(TeacherSubject.section_id == section_id))):
        raise ConflictError("No se puede eliminar una sección con profesores asignados")
    await db.delete(section)
    await db.commit()


# --- Estudiantes de cada sección ------------------------------------------------


async def list_section_students(db: AsyncSession, section_id: int) -> list[User]:
    await get_section(db, section_id)
    return list(
        await db.scalars(
            select(User)
            .join(StudentSection, StudentSection.student_id == User.id)
            .where(StudentSection.section_id == section_id)
            .order_by(User.last_name, User.first_name)
        )
    )


async def _with_grades(db: AsyncSession, student_ids: set[int], semester_id: int) -> set[int]:
    """Estudiantes que ya tienen notas (de lapso o de evaluación) en materias del periodo."""
    in_semester = (
        select(Enrollment.id, Enrollment.student_id)
        .join(Subject, Subject.id == Enrollment.subject_id)
        .where(Subject.semester_id == semester_id, Enrollment.student_id.in_(student_ids))
        .subquery()
    )
    found = set()
    for model in (Grade, EvaluationGrade):
        found |= set(
            await db.scalars(
                select(in_semester.c.student_id)
                .join(model, model.enrollment_id == in_semester.c.id)
                .distinct()
            )
        )
    return found


async def assign_students(
    db: AsyncSession, section_id: int, student_ids: list[int]
) -> tuple[int, int, int]:
    """Asigna estudiantes a la sección; si estaban en otra sección del periodo, los cambia.

    Un estudiante con notas en el periodo no se puede cambiar de sección (sus evaluaciones y
    su profesor son los de su sección actual). Todo o nada.
    Devuelve (añadidos, cambiados de sección, ya estaban).
    """
    section = await get_section(db, section_id)
    await academic.ensure_open(db, section.semester)
    wanted = set(student_ids)
    found = set(
        await db.scalars(select(User.id).where(User.id.in_(wanted), User.role == Role.STUDENT))
    )
    if missing := sorted(wanted - found):
        raise NotFoundError(f"Estudiantes no encontrados: {', '.join(map(str, missing))}")

    current = {
        row.student_id: row
        for row in await db.scalars(
            select(StudentSection).where(
                StudentSection.semester_id == section.semester_id,
                StudentSection.student_id.in_(wanted),
            )
        )
    }
    moving = {sid for sid, row in current.items() if row.section_id != section_id}
    if blocked := await _with_grades(db, moving, section.semester_id):
        names = await db.scalars(
            select(User.first_name + " " + User.last_name)
            .where(User.id.in_(blocked))
            .order_by(User.last_name, User.first_name)
        )
        raise ConflictError(
            "No se puede cambiar de sección a quien ya tiene notas en el periodo: "
            + ", ".join(names)
        )
    for sid in moving:
        current[sid].section_id = section_id
    new = wanted - current.keys()
    db.add_all(
        StudentSection(student_id=sid, semester_id=section.semester_id, section_id=section_id)
        for sid in new
    )
    await db.commit()
    return len(new), len(moving), len(wanted) - len(new) - len(moving)


async def remove_student(db: AsyncSession, section_id: int, student_id: int) -> None:
    section = await get_section(db, section_id)
    row = await db.get(StudentSection, (student_id, section.semester_id))
    if row is None or row.section_id != section_id:
        raise NotFoundError("El estudiante no está en esa sección")
    await academic.ensure_open(db, section.semester)
    enrolled = await db.scalar(
        select(
            exists().where(
                Enrollment.student_id == student_id,
                Enrollment.subject_id.in_(
                    select(Subject.id).where(Subject.semester_id == section.semester_id)
                ),
            )
        )
    )
    if enrolled:
        raise ConflictError(
            "No se puede quitar de la sección: está matriculado en materias del periodo. "
            "Retira antes sus matrículas"
        )
    await db.delete(row)
    await db.commit()


async def student_sections(db: AsyncSession, student_id: int) -> list[Section]:
    """Sección del estudiante en cada periodo."""
    await users.get_user(db, Role.STUDENT, student_id)
    return list(
        await db.scalars(
            select(Section)
            .join(StudentSection, StudentSection.section_id == Section.id)
            .join(Semester, Semester.id == Section.semester_id)
            .where(StudentSection.student_id == student_id)
            .order_by(Semester.name)
        )
    )


async def ensure_assigned(db: AsyncSession, pairs: set[tuple[int, int]]) -> None:
    """Comprueba que cada (estudiante, periodo) tiene sección: sin ella no se matricula."""
    if not pairs:
        return
    student_ids = {s for s, _ in pairs}
    assigned = set(
        (
            await db.execute(
                select(StudentSection.student_id, StudentSection.semester_id).where(
                    StudentSection.student_id.in_(student_ids)
                )
            )
        ).all()
    )
    missing = pairs - assigned
    if not missing:
        return
    semesters = {
        s.id: s.name
        for s in await db.scalars(select(Semester).where(Semester.id.in_({m for _, m in missing})))
    }
    people = {
        u.id: u.full_name
        for u in await db.scalars(select(User).where(User.id.in_({s for s, _ in missing})))
    }
    detail = "; ".join(
        f"{people[s]} ({semesters[m]})"
        for s, m in sorted(missing, key=lambda p: (semesters[p[1]], people[p[0]]))
    )
    raise ConflictError(
        f"Sin sección en el periodo de la materia: {detail}. Asígnales una sección antes de "
        "matricularlos"
    )
