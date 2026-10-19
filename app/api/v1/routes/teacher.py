"""Vistas del profesor: sus materias, sus estudiantes y la carga de notas."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.api.deps import DbSession, require_roles
from app.core.config import get_settings
from app.core.errors import InvalidDataError
from app.models import Role, User
from app.schemas.academic import SubjectRead
from app.schemas.common import Page
from app.schemas.grades import (
    GradeChangeRead,
    GradesUpdate,
    StudentGrades,
    StudentSummary,
    SubjectGrades,
    TeacherSubject,
    TermGrade,
)
from app.schemas.import_jobs import ImportJobRead, ImportJobSummary
from app.services import academic, evaluations, grades, import_jobs, notifications, rules
from app.services.rules import MAX_TERMS
from app.worker import notify_new_job

router = APIRouter(prefix="/teacher", tags=["Profesor"])

Teacher = Annotated[User, Depends(require_roles(Role.TEACHER))]


async def _rules_for(db: DbSession, subject) -> rules.GradingRules:
    return rules.for_semester(await rules.get_rules(db), subject.semester)


async def _subject_grades(db: DbSession, subject) -> SubjectGrades:
    roster = await grades.subject_roster(db, subject.id, await _rules_for(db, subject))
    return SubjectGrades(
        subject=SubjectRead.model_validate(subject),
        computed_terms=sorted(await evaluations.computed_terms(db, subject.id)),
        students=[
            StudentGrades(
                student=StudentSummary.model_validate(row.student),
                grades=[TermGrade(term=t, value=v) for t, v in row.grades.items()],
                average=row.average,
                final_grade=row.final_grade,
                passed=row.passed,
            )
            for row in roster
        ],
    )


@router.get("/subjects", summary="Mis materias")
async def my_subjects(teacher: Teacher, db: DbSession) -> list[TeacherSubject]:
    """Materias asignadas con el número de estudiantes y de notas cargadas por lapso."""
    rows = await grades.teacher_subjects_with_counts(db, teacher.id)
    current = await rules.get_rules(db)
    per_term = await grades.grades_per_term(
        db, {subject.id: rules.for_semester(current, subject.semester) for subject, _ in rows}
    )
    return [
        TeacherSubject(
            **{
                **SubjectRead.model_validate(subject).model_dump(),
                "students_count": count,
                "grades_count": per_term[subject.id],
            }
        )
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

    Es todo o nada: si algún estudiante no está matriculado (404) o alguna nota o lapso no
    cumple la configuración académica (422) no se guarda ninguna. Si el periodo de la
    materia está cerrado responde 409.

    Cada estudiante con notas nuevas o corregidas recibe un aviso (`/student/notifications`).

    Si las notas salen de una importación, envía su `import_job_id` para marcarla como
    confirmada (debe ser de esta materia y estar en `done`; si no, 409).
    """
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    await academic.ensure_open(db, subject.semester)
    if data.import_job_id is not None:
        await import_jobs.mark_confirmed(db, teacher.id, subject.id, data.import_job_id)
    subject_rules = await _rules_for(db, subject)
    updates = await grades.save_grades(
        db,
        subject.id,
        [(g.student_id, g.term, g.value) for g in data.grades],
        updated_by_id=teacher.id,
        rules=subject_rules,
        import_job_id=data.import_job_id,
    )
    await notifications.notify_grade_updates(db, subject, updates, subject_rules)
    await db.commit()
    return await _subject_grades(db, subject)


@router.get("/subjects/{subject_id}/grade-history", summary="Historial de cambios de notas")
async def subject_grade_history(
    subject_id: int,
    teacher: Teacher,
    db: DbSession,
    student_id: Annotated[int | None, Query(description="Solo los de este estudiante")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[GradeChangeRead]:
    """Altas, correcciones y borrados de notas de la materia, del más reciente al más
    antiguo, con quién los hizo y si fueron a mano o desde una importación de Excel."""
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    items, total = await grades.history(
        db, subject.id, student_id=student_id, limit=limit, offset=offset
    )
    return Page(
        items=[GradeChangeRead.from_row(*row) for row in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/subjects/{subject_id}/grades/import",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Encolar la lectura de un Excel de notas (IA)",
)
async def enqueue_grades_import(
    subject_id: int,
    teacher: Teacher,
    db: DbSession,
    file: Annotated[UploadFile, File(description="Planilla .xlsx de la materia")],
    default_term: Annotated[
        int | None,
        Form(ge=1, le=MAX_TERMS, description="Lapso a usar si la planilla no lo indica"),
    ] = None,
) -> ImportJobSummary:
    """Responde al instante con el trabajo en cola (`status: pending`); la IA procesa el
    archivo en segundo plano. Consulta `GET /teacher/import-jobs/{job_id}` hasta que
    `status` sea `done` (o `failed`).

    El resultado es una previsualización: **no se guarda nada**. Envía las filas que
    quieras guardar (ok o warning, ya corregidas) a `PUT /teacher/subjects/{id}/grades`.
    """
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    await academic.ensure_open(db, subject.semester)
    if default_term is not None:
        problem = (await _rules_for(db, subject)).term_error(default_term)
        if problem:
            raise InvalidDataError(problem)
    max_mb = get_settings().max_upload_size_mb
    content = await file.read(max_mb * 1024 * 1024 + 1)
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"El archivo supera el máximo de {max_mb} MB"
        )
    job = await import_jobs.enqueue(
        db,
        teacher_id=teacher.id,
        subject_id=subject_id,
        file_name=file.filename or "planilla.xlsx",
        content=content,
        default_term=default_term,
    )
    notify_new_job()
    return ImportJobSummary.model_validate(job)


@router.get("/import-jobs", summary="Mis importaciones recientes")
async def list_import_jobs(
    teacher: Teacher,
    db: DbSession,
    subject_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[ImportJobSummary]:
    jobs = await import_jobs.list_jobs(db, teacher.id, subject_id, limit)
    return [ImportJobSummary.model_validate(j) for j in jobs]


@router.get("/import-jobs/{job_id}", summary="Estado y resultado de una importación")
async def get_import_job(job_id: str, teacher: Teacher, db: DbSession) -> ImportJobRead:
    return ImportJobRead.model_validate(await import_jobs.get_job(db, teacher.id, job_id))
