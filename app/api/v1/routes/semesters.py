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


def _with_count(semester, counts: dict[int, int]) -> SemesterRead:
    return SemesterRead.model_validate(semester).model_copy(
        update={"subjects_count": counts[semester.id]}
    )


@router.get("", summary="Listar semestres")
async def list_semesters(db: DbSession) -> list[SemesterRead]:
    semesters = await academic.list_semesters(db)
    counts = await academic.subjects_per_semester(db, [s.id for s in semesters])
    return [_with_count(s, counts) for s in semesters]


@router.post("", status_code=status.HTTP_201_CREATED, summary="Crear semestre")
async def create_semester(data: SemesterCreate, db: DbSession) -> SemesterRead:
    return SemesterRead.model_validate(await academic.create_semester(db, data.name))


@router.get("/{semester_id}", summary="Ver semestre")
async def get_semester(semester_id: int, db: DbSession) -> SemesterRead:
    semester = await academic.get_semester(db, semester_id)
    return _with_count(semester, await academic.subjects_per_semester(db, [semester.id]))


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
