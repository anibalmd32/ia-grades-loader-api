"""Profesor de cada materia en cada sección (uno solo por materia y sección)."""

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidDataError, NotFoundError
from app.models import Role, Section, Subject, TeacherSubject, User
from app.services import academic, users


async def ensure_subjects_exist(db: AsyncSession, subject_ids: set[int]) -> None:
    found = set(await db.scalars(select(Subject.id).where(Subject.id.in_(subject_ids))))
    if missing := sorted(subject_ids - found):
        raise NotFoundError(f"Materias no encontradas: {', '.join(map(str, missing))}")


async def list_teacher_subjects(db: AsyncSession, teacher_id: int) -> list[tuple[Subject, Section]]:
    await users.get_user(db, Role.TEACHER, teacher_id)
    query = (
        select(Subject, Section)
        .join(TeacherSubject, TeacherSubject.subject_id == Subject.id)
        .join(Section, Section.id == TeacherSubject.section_id)
        .where(TeacherSubject.teacher_id == teacher_id)
        .order_by(Subject.code, Section.name)
    )
    return [(subject, section) for subject, section in await db.execute(query)]


async def set_teacher_subjects(
    db: AsyncSession, teacher_id: int, pairs: list[tuple[int, int]]
) -> list[tuple[Subject, Section]]:
    """Reemplaza las (materia, sección) del profesor por las indicadas.

    Cada materia tiene un solo profesor por sección: si otra persona ya la imparte en esa
    sección responde 409 (hay que quitársela antes)."""
    await users.get_user(db, Role.TEACHER, teacher_id)
    wanted = set(pairs)
    await ensure_subjects_exist(db, {s for s, _ in wanted})
    section_ids = {sec for _, sec in wanted}
    sections = {
        s.id: s for s in await db.scalars(select(Section).where(Section.id.in_(section_ids)))
    }
    if missing := sorted(section_ids - sections.keys()):
        raise NotFoundError(f"Secciones no encontradas: {', '.join(map(str, missing))}")
    subjects = {
        s.id: s
        for s in await db.scalars(select(Subject).where(Subject.id.in_({s for s, _ in wanted})))
    }
    for subject_id, section_id in sorted(wanted):
        subject, section = subjects[subject_id], sections[section_id]
        if section.semester_id != subject.semester_id:
            raise InvalidDataError(
                f"La sección {section.name} es de {section.semester.name} y la materia "
                f"{subject.code} de {subject.semester.name}"
            )

    if wanted:
        taken = (
            await db.execute(
                select(TeacherSubject.subject_id, TeacherSubject.section_id, User)
                .join(User, User.id == TeacherSubject.teacher_id)
                .where(
                    tuple_(TeacherSubject.subject_id, TeacherSubject.section_id).in_(list(wanted)),
                    TeacherSubject.teacher_id != teacher_id,
                )
            )
        ).all()
        if taken:
            detail = "; ".join(
                f"{subjects[s].code} sección {sections[sec].name}: {other.full_name}"
                for s, sec, other in sorted(taken, key=lambda t: (subjects[t[0]].code, t[1]))
            )
            raise ConflictError(
                f"Cada materia tiene un solo profesor por sección y ya están asignadas: {detail}"
            )

    current = set(
        (
            await db.execute(
                select(TeacherSubject.subject_id, TeacherSubject.section_id).where(
                    TeacherSubject.teacher_id == teacher_id
                )
            )
        ).all()
    )
    if to_remove := current - wanted:
        await db.execute(
            delete(TeacherSubject).where(
                TeacherSubject.teacher_id == teacher_id,
                tuple_(TeacherSubject.subject_id, TeacherSubject.section_id).in_(list(to_remove)),
            )
        )
    db.add_all(
        TeacherSubject(teacher_id=teacher_id, subject_id=s, section_id=sec)
        for s, sec in wanted - current
    )
    await db.commit()
    return await list_teacher_subjects(db, teacher_id)


async def list_subject_teachers(db: AsyncSession, subject_id: int) -> list[tuple[Section, User]]:
    """Profesor de la materia en cada sección que lo tiene asignado."""
    await academic.get_subject(db, subject_id)
    query = (
        select(Section, User)
        .join(TeacherSubject, TeacherSubject.section_id == Section.id)
        .join(User, User.id == TeacherSubject.teacher_id)
        .where(TeacherSubject.subject_id == subject_id)
        .order_by(Section.name)
    )
    return [(section, teacher) for section, teacher in await db.execute(query)]
