"""Consulta de notas por el estudiante autenticado."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, require_roles
from app.models import Role, User
from app.schemas.academic import SubjectRead
from app.schemas.grades import StudentOverview, StudentSubjectGrades, TermGrade
from app.services import student_grades
from app.services.student_grades import SubjectGrades

router = APIRouter(prefix="/student", tags=["Estudiante"])

Student = Annotated[User, Depends(require_roles(Role.STUDENT))]


def _to_schema(item: SubjectGrades) -> StudentSubjectGrades:
    return StudentSubjectGrades(
        subject=SubjectRead.model_validate(item.subject),
        teachers=item.teachers,
        grades=[TermGrade(term=t, value=v) for t, v in item.grades.items()],
        average=item.average,
    )


@router.get("/subjects", summary="Mis materias y notas")
async def my_subjects(student: Student, db: DbSession) -> StudentOverview:
    """Todas las materias en las que estás matriculado, con la nota de cada lapso
    (null si aún no se ha cargado), el promedio de cada una y el promedio general."""
    subjects = await student_grades.student_subjects(db, student.id)
    return StudentOverview(
        subjects=[_to_schema(s) for s in subjects],
        average=student_grades.overall_average(subjects),
    )


@router.get("/subjects/{subject_id}", summary="Detalle de una materia")
async def my_subject(subject_id: int, student: Student, db: DbSession) -> StudentSubjectGrades:
    return _to_schema(await student_grades.student_subject(db, student.id, subject_id))
