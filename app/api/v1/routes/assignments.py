from fastapi import APIRouter, Depends

from app.api.deps import DbSession, require_roles
from app.models import Role
from app.schemas.academic import SubjectRead, TeacherSubjectsUpdate
from app.schemas.user import UserRead
from app.services import assignments

router = APIRouter(dependencies=[Depends(require_roles(Role.COORDINATOR))])


@router.get("/teachers/{teacher_id}/subjects", tags=["Profesores"], summary="Materias del profesor")
async def get_teacher_subjects(teacher_id: int, db: DbSession) -> list[SubjectRead]:
    subjects = await assignments.list_teacher_subjects(db, teacher_id)
    return [SubjectRead.model_validate(s) for s in subjects]


@router.put(
    "/teachers/{teacher_id}/subjects",
    tags=["Profesores"],
    summary="Asignar materias al profesor",
)
async def set_teacher_subjects(
    teacher_id: int, data: TeacherSubjectsUpdate, db: DbSession
) -> list[SubjectRead]:
    """Envía la lista completa de materias: las que no estén se desasignan.
    Una lista vacía quita todas."""
    subjects = await assignments.set_teacher_subjects(db, teacher_id, data.subject_ids)
    return [SubjectRead.model_validate(s) for s in subjects]


@router.get(
    "/subjects/{subject_id}/teachers", tags=["Materias"], summary="Profesores de la materia"
)
async def get_subject_teachers(subject_id: int, db: DbSession) -> list[UserRead]:
    teachers = await assignments.list_subject_teachers(db, subject_id)
    return [UserRead.model_validate(t) for t in teachers]
