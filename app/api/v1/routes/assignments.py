from fastapi import APIRouter, Depends

from app.api.deps import DbSession, require_roles
from app.models import Role
from app.schemas.academic import (
    SectionRead,
    SubjectRead,
    SubjectTeacherRead,
    TeacherAssignmentRead,
    TeacherSubjectsUpdate,
)
from app.schemas.user import UserRead
from app.services import assignments

router = APIRouter(dependencies=[Depends(require_roles(Role.COORDINATOR))])


def _assignments(rows) -> list[TeacherAssignmentRead]:
    return [
        TeacherAssignmentRead(
            subject=SubjectRead.model_validate(subject), section=SectionRead.model_validate(section)
        )
        for subject, section in rows
    ]


@router.get(
    "/teachers/{teacher_id}/subjects",
    tags=["Profesores"],
    summary="Materias (y secciones) del profesor",
)
async def get_teacher_subjects(teacher_id: int, db: DbSession) -> list[TeacherAssignmentRead]:
    return _assignments(await assignments.list_teacher_subjects(db, teacher_id))


@router.put(
    "/teachers/{teacher_id}/subjects",
    tags=["Profesores"],
    summary="Asignar materias al profesor en sus secciones",
)
async def set_teacher_subjects(
    teacher_id: int, data: TeacherSubjectsUpdate, db: DbSession
) -> list[TeacherAssignmentRead]:
    """Envía la lista completa de (materia, sección): las que no estén se desasignan. Una
    lista vacía quita todas. La sección debe ser del periodo de la materia (422) y cada
    materia tiene un solo profesor por sección (409 si ya la imparte otro)."""
    pairs = [(a.subject_id, a.section_id) for a in data.assignments]
    return _assignments(await assignments.set_teacher_subjects(db, teacher_id, pairs))


@router.get(
    "/subjects/{subject_id}/teachers",
    tags=["Materias"],
    summary="Profesor de la materia en cada sección",
)
async def get_subject_teachers(subject_id: int, db: DbSession) -> list[SubjectTeacherRead]:
    return [
        SubjectTeacherRead(
            section=SectionRead.model_validate(section), teacher=UserRead.model_validate(teacher)
        )
        for section, teacher in await assignments.list_subject_teachers(db, subject_id)
    ]
