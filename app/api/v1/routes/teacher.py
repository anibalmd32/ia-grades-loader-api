"""Vistas del profesor: sus materias, sus estudiantes y la carga de notas."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, require_roles
from app.models import Role, User
from app.schemas.academic import SubjectRead
from app.schemas.grades import (
    GradesUpdate,
    StudentGrades,
    StudentSummary,
    SubjectGrades,
    TeacherSubject,
    TermGrade,
)
from app.services import grades

router = APIRouter(prefix="/teacher", tags=["Profesor"])

Teacher = Annotated[User, Depends(require_roles(Role.TEACHER))]


async def _subject_grades(db: DbSession, subject) -> SubjectGrades:
    roster = await grades.subject_roster(db, subject.id)
    return SubjectGrades(
        subject=SubjectRead.model_validate(subject),
        students=[
            StudentGrades(
                student=StudentSummary.model_validate(row.student),
                grades=[TermGrade(term=t, value=v) for t, v in row.grades.items()],
                average=row.average,
            )
            for row in roster
        ],
    )


@router.get("/subjects", summary="Mis materias")
async def my_subjects(teacher: Teacher, db: DbSession) -> list[TeacherSubject]:
    rows = await grades.teacher_subjects_with_counts(db, teacher.id)
    return [
        TeacherSubject(**SubjectRead.model_validate(subject).model_dump(), students_count=count)
        for subject, count in rows
    ]


@router.get("/subjects/{subject_id}/grades", summary="Estudiantes y notas de una materia")
async def subject_grades(subject_id: int, teacher: Teacher, db: DbSession) -> SubjectGrades:
    """Lista los estudiantes matriculados con su nota de cada lapso (null si falta)."""
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    return await _subject_grades(db, subject)


@router.put("/subjects/{subject_id}/grades", summary="Cargar o corregir notas")
async def save_subject_grades(
    subject_id: int, data: GradesUpdate, teacher: Teacher, db: DbSession
) -> SubjectGrades:
    """Envía solo las notas que cambian. `value: null` borra la nota de ese lapso.

    Es todo o nada: si algún estudiante no está matriculado no se guarda ninguna.
    """
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    await grades.save_grades(
        db,
        subject.id,
        [(g.student_id, g.term, g.value) for g in data.grades],
        updated_by_id=teacher.id,
    )
    return await _subject_grades(db, subject)
