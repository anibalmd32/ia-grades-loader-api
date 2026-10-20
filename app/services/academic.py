"""Semestres y materias."""

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.search import LIKE_ESCAPE, contains_pattern
from app.models import Enrollment, Semester, Subject, TeacherSubject
from app.models.import_job import utcnow
from app.services import rules

# --- Semestres ---------------------------------------------------------------


async def get_semester(db: AsyncSession, semester_id: int) -> Semester:
    semester = await db.get(Semester, semester_id)
    if semester is None:
        raise NotFoundError("Semestre no encontrado")
    return semester


async def _ensure_semester_name_free(db: AsyncSession, name: str, exclude_id: int | None = None):
    query = select(Semester.id).where(Semester.name == name)
    if exclude_id is not None:
        query = query.where(Semester.id != exclude_id)
    if await db.scalar(query) is not None:
        raise ConflictError(f"Ya existe el semestre «{name}»")


async def list_semesters(db: AsyncSession) -> list[Semester]:
    return list(await db.scalars(select(Semester).order_by(Semester.name)))


async def subjects_per_semester(db: AsyncSession, semester_ids: list[int]) -> dict[int, int]:
    """Número de materias de cada semestre (una sola consulta para todo el listado)."""
    rows = await db.execute(
        select(Subject.semester_id, func.count())
        .where(Subject.semester_id.in_(semester_ids))
        .group_by(Subject.semester_id)
    )
    return {sid: 0 for sid in semester_ids} | dict(rows.all())


async def create_semester(db: AsyncSession, name: str) -> Semester:
    await _ensure_semester_name_free(db, name)
    semester = Semester(name=name)
    db.add(semester)
    await db.commit()
    return semester


async def update_semester(db: AsyncSession, semester_id: int, name: str) -> Semester:
    semester = await get_semester(db, semester_id)
    await _ensure_semester_name_free(db, name, exclude_id=semester_id)
    semester.name = name
    await db.commit()
    return semester


async def ensure_open(db: AsyncSession, semester: Semester) -> None:
    """Lanza 409 si el periodo está cerrado (sus notas y matrículas quedan fijas)."""
    if semester.is_closed:
        label = (await rules.get_rules(db)).period_label.lower()
        raise ConflictError(
            f"El {label} {semester.name} está cerrado: no se pueden modificar sus notas, "
            "matrículas ni materias. Pide a coordinación que lo reabra."
        )


async def set_closed(db: AsyncSession, semester_id: int, closed: bool) -> Semester:
    semester = await get_semester(db, semester_id)
    semester.is_closed = closed
    semester.closed_at = utcnow() if closed else None
    if closed and semester.rules is None:
        # Se congelan las reglas con que se evaluó; se conservan aunque se reabra
        semester.rules = rules.snapshot(await rules.get_rules(db))
    await db.commit()
    return semester


async def delete_semester(db: AsyncSession, semester_id: int) -> None:
    semester = await get_semester(db, semester_id)
    if await db.scalar(select(exists().where(Subject.semester_id == semester_id))):
        raise ConflictError("No se puede eliminar un semestre que tiene materias")
    await db.delete(semester)
    await db.commit()


# --- Materias ----------------------------------------------------------------


async def get_subject(db: AsyncSession, subject_id: int) -> Subject:
    subject = await db.get(Subject, subject_id)
    if subject is None:
        raise NotFoundError("Materia no encontrada")
    return subject


async def _ensure_code_free(db: AsyncSession, code: str, exclude_id: int | None = None):
    query = select(Subject.id).where(Subject.code == code)
    if exclude_id is not None:
        query = query.where(Subject.id != exclude_id)
    if await db.scalar(query) is not None:
        raise ConflictError(f"Ya existe una materia con el código «{code}»")


async def list_subjects(
    db: AsyncSession,
    *,
    semester_id: int | None,
    q: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Subject], int]:
    query = select(Subject)
    if semester_id is not None:
        query = query.where(Subject.semester_id == semester_id)
    if q:
        pattern = contains_pattern(q.strip())
        query = query.where(
            Subject.name.ilike(pattern, escape=LIKE_ESCAPE)
            | Subject.code.ilike(pattern, escape=LIKE_ESCAPE)
        )
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    items = await db.scalars(query.order_by(Subject.code).limit(limit).offset(offset))
    return list(items), total or 0


async def subject_counts(db: AsyncSession, subject_ids: list[int]) -> dict[int, tuple[int, int]]:
    """(profesores, estudiantes) de cada materia, en dos consultas para todo el listado."""
    counts = []
    for model in (TeacherSubject, Enrollment):
        rows = await db.execute(
            select(model.subject_id, func.count())
            .where(model.subject_id.in_(subject_ids))
            .group_by(model.subject_id)
        )
        counts.append(dict(rows.all()))
    teachers, students = counts
    return {sid: (teachers.get(sid, 0), students.get(sid, 0)) for sid in subject_ids}


async def create_subject(db: AsyncSession, semester_id: int, code: str, name: str) -> Subject:
    await ensure_open(db, await get_semester(db, semester_id))
    await _ensure_code_free(db, code)
    subject = Subject(semester_id=semester_id, code=code, name=name)
    db.add(subject)
    await db.commit()
    await db.refresh(subject, ["semester"])
    return subject


async def update_subject(db: AsyncSession, subject_id: int, changes: dict) -> Subject:
    subject = await get_subject(db, subject_id)
    if "semester_id" in changes and changes["semester_id"] != subject.semester_id:
        await ensure_open(db, subject.semester)
        await ensure_open(db, await get_semester(db, changes["semester_id"]))
    if "code" in changes:
        await _ensure_code_free(db, changes["code"], exclude_id=subject_id)
    for field, value in changes.items():
        setattr(subject, field, value)
    await db.commit()
    await db.refresh(subject, ["semester"])
    return subject


async def delete_subject(db: AsyncSession, subject_id: int) -> None:
    subject = await get_subject(db, subject_id)
    # Un periodo cerrado conserva su historial: sus materias no se borran
    await ensure_open(db, subject.semester)
    if await db.scalar(select(exists().where(Enrollment.subject_id == subject_id))):
        raise ConflictError("No se puede eliminar una materia con estudiantes matriculados")
    await db.delete(subject)
    await db.commit()
