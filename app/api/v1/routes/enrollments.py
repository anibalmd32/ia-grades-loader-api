from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, require_roles
from app.models import Role
from app.schemas.academic import (
    EnrollmentResult,
    EnrollStudentsRequest,
    EnrollSubjectsRequest,
    SubjectRead,
)
from app.schemas.user import UserRead
from app.services import enrollments

router = APIRouter(dependencies=[Depends(require_roles(Role.COORDINATOR))])


@router.get(
    "/students/{student_id}/subjects",
    tags=["Estudiantes"],
    summary="Materias en las que está matriculado",
)
async def get_student_subjects(student_id: int, db: DbSession) -> list[SubjectRead]:
    subjects = await enrollments.list_student_subjects(db, student_id)
    return [SubjectRead.model_validate(s) for s in subjects]


@router.post(
    "/students/{student_id}/subjects",
    tags=["Estudiantes"],
    summary="Matricular al estudiante en una o varias materias",
)
async def enroll_student(
    student_id: int, data: EnrollSubjectsRequest, db: DbSession
) -> EnrollmentResult:
    """Las materias en las que ya estaba matriculado se ignoran."""
    added, existing = await enrollments.enroll_student(db, student_id, data.subject_ids)
    return EnrollmentResult(added=added, already_enrolled=existing)


@router.delete(
    "/students/{student_id}/subjects/{subject_id}",
    tags=["Estudiantes"],
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retirar matrícula (solo si no tiene notas)",
)
async def unenroll(student_id: int, subject_id: int, db: DbSession) -> None:
    await enrollments.unenroll(db, student_id, subject_id)


@router.get(
    "/subjects/{subject_id}/students",
    tags=["Materias"],
    summary="Estudiantes matriculados en la materia",
)
async def get_subject_students(subject_id: int, db: DbSession) -> list[UserRead]:
    students = await enrollments.list_subject_students(db, subject_id)
    return [UserRead.model_validate(s) for s in students]


@router.post(
    "/subjects/{subject_id}/students",
    tags=["Materias"],
    summary="Matricular varios estudiantes en la materia",
)
async def enroll_in_subject(
    subject_id: int, data: EnrollStudentsRequest, db: DbSession
) -> EnrollmentResult:
    """Matrícula en lote. Los estudiantes ya matriculados se ignoran."""
    added, existing = await enrollments.enroll_in_subject(db, subject_id, data.student_ids)
    return EnrollmentResult(added=added, already_enrolled=existing)
