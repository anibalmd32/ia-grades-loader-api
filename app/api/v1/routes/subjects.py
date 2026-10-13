from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, require_roles
from app.models import Role
from app.schemas.academic import SubjectCreate, SubjectRead, SubjectUpdate
from app.schemas.common import Page
from app.services import academic

router = APIRouter(
    prefix="/subjects",
    tags=["Materias"],
    dependencies=[Depends(require_roles(Role.COORDINATOR))],
)


@router.get("", summary="Listar materias")
async def list_subjects(
    db: DbSession,
    semester_id: int | None = None,
    q: Annotated[str | None, Query(description="Busca en código o nombre")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[SubjectRead]:
    items, total = await academic.list_subjects(
        db, semester_id=semester_id, q=q, limit=limit, offset=offset
    )
    return Page(
        items=[SubjectRead.model_validate(s) for s in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="Crear materia")
async def create_subject(data: SubjectCreate, db: DbSession) -> SubjectRead:
    subject = await academic.create_subject(db, data.semester_id, data.code, data.name)
    return SubjectRead.model_validate(subject)


@router.get("/{subject_id}", summary="Ver materia")
async def get_subject(subject_id: int, db: DbSession) -> SubjectRead:
    return SubjectRead.model_validate(await academic.get_subject(db, subject_id))


@router.patch("/{subject_id}", summary="Modificar materia")
async def update_subject(subject_id: int, data: SubjectUpdate, db: DbSession) -> SubjectRead:
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    return SubjectRead.model_validate(await academic.update_subject(db, subject_id, changes))


@router.delete(
    "/{subject_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar materia (solo si no tiene estudiantes matriculados)",
)
async def delete_subject(subject_id: int, db: DbSession) -> None:
    await academic.delete_subject(db, subject_id)
