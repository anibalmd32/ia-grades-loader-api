"""Materias que imparte cada profesor."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Role, Subject, TeacherSubject, User
from app.services import academic, users


async def ensure_subjects_exist(db: AsyncSession, subject_ids: set[int]) -> None:
    found = set(await db.scalars(select(Subject.id).where(Subject.id.in_(subject_ids))))
    if missing := sorted(subject_ids - found):
        raise NotFoundError(f"Materias no encontradas: {', '.join(map(str, missing))}")


async def list_teacher_subjects(db: AsyncSession, teacher_id: int) -> list[Subject]:
    await users.get_user(db, Role.TEACHER, teacher_id)
    query = (
        select(Subject)
        .join(TeacherSubject, TeacherSubject.subject_id == Subject.id)
        .where(TeacherSubject.teacher_id == teacher_id)
        .order_by(Subject.code)
    )
    return list(await db.scalars(query))


async def set_teacher_subjects(
    db: AsyncSession, teacher_id: int, subject_ids: list[int]
) -> list[Subject]:
    """Reemplaza el conjunto de materias del profesor por el indicado."""
    await users.get_user(db, Role.TEACHER, teacher_id)
    wanted = set(subject_ids)
    await ensure_subjects_exist(db, wanted)

    current = set(
        await db.scalars(
            select(TeacherSubject.subject_id).where(TeacherSubject.teacher_id == teacher_id)
        )
    )
    if to_remove := current - wanted:
        await db.execute(
            delete(TeacherSubject).where(
                TeacherSubject.teacher_id == teacher_id,
                TeacherSubject.subject_id.in_(to_remove),
            )
        )
    db.add_all(TeacherSubject(teacher_id=teacher_id, subject_id=s) for s in wanted - current)
    await db.commit()
    return await list_teacher_subjects(db, teacher_id)


async def list_subject_teachers(db: AsyncSession, subject_id: int) -> list[User]:
    await academic.get_subject(db, subject_id)
    query = (
        select(User)
        .join(TeacherSubject, TeacherSubject.teacher_id == User.id)
        .where(TeacherSubject.subject_id == subject_id)
        .order_by(User.full_name)
    )
    return list(await db.scalars(query))
