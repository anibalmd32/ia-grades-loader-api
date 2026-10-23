"""Secciones de cada periodo y sus estudiantes (coordinación)."""

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, require_roles
from app.models import Role, Section
from app.schemas.academic import (
    SectionCreate,
    SectionRead,
    SectionStudentsRequest,
    SectionStudentsResult,
    SectionUpdate,
    StudentSectionRead,
)
from app.schemas.user import UserRead
from app.services import sections

router = APIRouter(tags=["Secciones"], dependencies=[Depends(require_roles(Role.COORDINATOR))])


def _with_count(section: Section, counts: dict[int, int]) -> SectionRead:
    return SectionRead.model_validate(section).model_copy(
        update={"students_count": counts[section.id]}
    )


@router.get("/semesters/{semester_id}/sections", summary="Secciones del periodo")
async def list_sections(semester_id: int, db: DbSession) -> list[SectionRead]:
    items = await sections.list_sections(db, semester_id)
    counts = await sections.students_per_section(db, [s.id for s in items])
    return [_with_count(s, counts) for s in items]


@router.post(
    "/semesters/{semester_id}/sections",
    status_code=status.HTTP_201_CREATED,
    summary="Crear sección en el periodo",
)
async def create_section(semester_id: int, data: SectionCreate, db: DbSession) -> SectionRead:
    section = await sections.create_section(db, semester_id, data.name)
    return _with_count(section, {section.id: 0})


@router.patch("/sections/{section_id}", summary="Renombrar sección")
async def rename_section(section_id: int, data: SectionUpdate, db: DbSession) -> SectionRead:
    section = await sections.rename_section(db, section_id, data.name)
    return _with_count(section, await sections.students_per_section(db, [section.id]))


@router.delete(
    "/sections/{section_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar sección (solo sin estudiantes ni profesores)",
)
async def delete_section(section_id: int, db: DbSession) -> None:
    await sections.delete_section(db, section_id)


@router.get("/sections/{section_id}/students", summary="Estudiantes de la sección")
async def section_students(section_id: int, db: DbSession) -> list[UserRead]:
    return [
        UserRead.model_validate(u) for u in await sections.list_section_students(db, section_id)
    ]


@router.post("/sections/{section_id}/students", summary="Asignar estudiantes a la sección")
async def assign_students(
    section_id: int, data: SectionStudentsRequest, db: DbSession
) -> SectionStudentsResult:
    """Cada estudiante cursa el periodo en una sola sección: si estaba en otra, se cambia
    (409 si ya tiene notas en el periodo). Para matricularlo en materias del periodo
    necesita tener sección."""
    added, moved, already = await sections.assign_students(db, section_id, data.student_ids)
    return SectionStudentsResult(added=added, moved=moved, already_in_section=already)


@router.delete(
    "/sections/{section_id}/students/{student_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Quitar al estudiante de la sección (solo sin matrículas en el periodo)",
)
async def remove_student(section_id: int, student_id: int, db: DbSession) -> None:
    await sections.remove_student(db, section_id, student_id)


@router.get(
    "/students/{student_id}/sections",
    tags=["Estudiantes"],
    summary="Sección del estudiante en cada periodo",
)
async def student_sections(student_id: int, db: DbSession) -> list[StudentSectionRead]:
    return [
        StudentSectionRead.model_validate(s)
        for s in await sections.student_sections(db, student_id)
    ]
