from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, require_roles
from app.models import Role
from app.schemas.academic import SemesterCreate, SemesterRead, SemesterUpdate
from app.services import academic

router = APIRouter(
    prefix="/semesters",
    tags=["Semestres"],
    dependencies=[Depends(require_roles(Role.COORDINATOR))],
)


@router.get("", summary="Listar semestres")
async def list_semesters(db: DbSession) -> list[SemesterRead]:
    return [SemesterRead.model_validate(s) for s in await academic.list_semesters(db)]


@router.post("", status_code=status.HTTP_201_CREATED, summary="Crear semestre")
async def create_semester(data: SemesterCreate, db: DbSession) -> SemesterRead:
    return SemesterRead.model_validate(await academic.create_semester(db, data.name))


@router.get("/{semester_id}", summary="Ver semestre")
async def get_semester(semester_id: int, db: DbSession) -> SemesterRead:
    return SemesterRead.model_validate(await academic.get_semester(db, semester_id))


@router.patch("/{semester_id}", summary="Renombrar semestre")
async def update_semester(semester_id: int, data: SemesterUpdate, db: DbSession) -> SemesterRead:
    return SemesterRead.model_validate(await academic.update_semester(db, semester_id, data.name))


@router.delete(
    "/{semester_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar semestre (solo si no tiene materias)",
)
async def delete_semester(semester_id: int, db: DbSession) -> None:
    await academic.delete_semester(db, semester_id)
