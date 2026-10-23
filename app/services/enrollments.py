"""Matrículas: qué materias cursa cada estudiante.

Para matricular a un estudiante en una materia debe tener sección en el periodo de la
materia (app.services.sections): su profesor es el de la materia en esa sección."""

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Enrollment, Grade, Role, Subject, User
from app.services import academic, sections, users
from app.services.assignments import ensure_subjects_exist


async def _ensure_students_exist(db: AsyncSession, student_ids: set[int]) -> None:
    found = set(
        await db.scalars(select(User.id).where(User.id.in_(student_ids), User.role == Role.STUDENT))
    )
    if missing := sorted(student_ids - found):
        raise NotFoundError(f"Estudiantes no encontrados: {', '.join(map(str, missing))}")


async def _enroll_pairs(db: AsyncSession, pairs: set[tuple[int, int]]) -> tuple[int, int]:
    """Matricula pares (estudiante, materia) ignorando los ya existentes.

    Devuelve (añadidas, ya existentes).
    """
    student_ids = {s for s, _ in pairs}
    subject_ids = {m for _, m in pairs}
    semester_of = {}
    for subject in await db.scalars(select(Subject).where(Subject.id.in_(subject_ids))):
        await academic.ensure_open(db, subject.semester)
        semester_of[subject.id] = subject.semester_id
    rows = await db.execute(
        select(Enrollment.student_id, Enrollment.subject_id).where(
            Enrollment.student_id.in_(student_ids),
            Enrollment.subject_id.in_(subject_ids),
        )
    )
    existing = {(s, m) for s, m in rows}
    new = pairs - existing
    await sections.ensure_assigned(db, {(s, semester_of[m]) for s, m in new})
    db.add_all(Enrollment(student_id=s, subject_id=m) for s, m in new)
    await db.commit()
    return len(new), len(pairs & existing)


async def list_student_subjects(db: AsyncSession, student_id: int) -> list[Subject]:
    await users.get_user(db, Role.STUDENT, student_id)
    query = (
        select(Subject)
        .join(Enrollment, Enrollment.subject_id == Subject.id)
        .where(Enrollment.student_id == student_id)
        .order_by(Subject.code)
    )
    return list(await db.scalars(query))


async def enroll_student(
    db: AsyncSession, student_id: int, subject_ids: list[int]
) -> tuple[int, int]:
    await users.get_user(db, Role.STUDENT, student_id)
    wanted = set(subject_ids)
    await ensure_subjects_exist(db, wanted)
    return await _enroll_pairs(db, {(student_id, s) for s in wanted})


async def list_subject_students(db: AsyncSession, subject_id: int) -> list[User]:
    await academic.get_subject(db, subject_id)
    query = (
        select(User)
        .join(Enrollment, Enrollment.student_id == User.id)
        .where(Enrollment.subject_id == subject_id)
        .order_by(User.last_name, User.first_name)
    )
    return list(await db.scalars(query))


async def enroll_in_subject(
    db: AsyncSession, subject_id: int, student_ids: list[int]
) -> tuple[int, int]:
    await academic.get_subject(db, subject_id)
    wanted = set(student_ids)
    await _ensure_students_exist(db, wanted)
    return await _enroll_pairs(db, {(s, subject_id) for s in wanted})


async def unenroll(db: AsyncSession, student_id: int, subject_id: int) -> None:
    enrollment = await db.scalar(
        select(Enrollment).where(
            Enrollment.student_id == student_id, Enrollment.subject_id == subject_id
        )
    )
    if enrollment is None:
        raise NotFoundError("El estudiante no está matriculado en esa materia")
    await academic.ensure_open(db, (await academic.get_subject(db, subject_id)).semester)
    if await db.scalar(select(exists().where(Grade.enrollment_id == enrollment.id))):
        raise ConflictError("No se puede retirar la matrícula: ya tiene notas cargadas")
    await db.delete(enrollment)
    await db.commit()
