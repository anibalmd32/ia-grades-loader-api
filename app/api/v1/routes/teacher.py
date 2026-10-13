"""Vistas del profesor: sus materias, sus estudiantes y la carga de notas."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.ai.client import LLMClient, get_llm
from app.api.deps import DbSession, require_roles
from app.core.config import get_settings
from app.models import Role, User
from app.schemas.academic import SubjectRead
from app.schemas.grades import (
    GradesUpdate,
    ImportPreview,
    ImportRow,
    ImportSummary,
    StudentGrades,
    StudentSummary,
    SubjectGrades,
    TeacherSubject,
    TermGrade,
)
from app.services import grade_import, grades

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


@router.post(
    "/subjects/{subject_id}/grades/import",
    summary="Previsualizar notas desde un Excel (IA)",
)
async def preview_grades_import(
    subject_id: int,
    teacher: Teacher,
    db: DbSession,
    llm: Annotated[LLMClient, Depends(get_llm)],
    file: Annotated[UploadFile, File(description="Planilla .xlsx de la materia")],
    default_term: Annotated[
        int | None,
        Form(ge=1, le=3, description="Lapso a usar si la planilla no lo indica"),
    ] = None,
) -> ImportPreview:
    """La IA lee la planilla y el servidor identifica a cada estudiante entre los
    matriculados. **No guarda nada**: revisa las filas y envía las que quieras guardar
    (`status` ok o warning, ya corregidas) a `PUT /teacher/subjects/{id}/grades`.
    """
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    max_bytes = get_settings().max_upload_size_mb * 1024 * 1024
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"El archivo supera el máximo de {get_settings().max_upload_size_mb} MB",
        )

    preview = await grade_import.preview_import(db, llm, subject, content, default_term)
    rows = [
        ImportRow(
            sheet=r.extracted.sheet,
            row=r.extracted.row,
            raw_cedula=None if r.extracted.cedula is None else str(r.extracted.cedula),
            raw_name=r.extracted.student_name,
            raw_value=None if r.extracted.value is None else str(r.extracted.value),
            student=StudentSummary.model_validate(r.student) if r.student else None,
            term=r.term,
            value=r.value,
            current_value=r.current_value,
            status=r.status,
            issues=r.issues,
        )
        for r in preview.rows
    ]
    return ImportPreview(
        subject=SubjectRead.model_validate(subject),
        rows=rows,
        summary=ImportSummary(
            **{s: sum(r.status == s for r in rows) for s in ("ok", "warning", "error")}
        ),
        warnings=preview.warnings,
        students_without_grades=[
            StudentSummary.model_validate(s) for s in preview.students_without_grades
        ],
    )
