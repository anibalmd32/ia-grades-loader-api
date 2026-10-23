"""Vistas del profesor: sus materias (en cada sección que imparte), sus estudiantes y la
carga de notas. Cada materia se trabaja por sección: el profesor solo ve a los estudiantes
de las secciones en las que la imparte."""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)

from app.api.deps import DbSession, require_roles
from app.core.config import get_settings
from app.core.errors import InvalidDataError
from app.models import Role, Section, Subject, User
from app.schemas.academic import SectionRead, SubjectRead
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
from app.services import (
    academic,
    evaluations,
    grade_export,
    grades,
    import_jobs,
    notifications,
    rules,
)
from app.services.rules import MAX_TERMS
from app.worker import notify_new_job

router = APIRouter(prefix="/teacher", tags=["Profesor"])

Teacher = Annotated[User, Depends(require_roles(Role.TEACHER))]
CLASS = "/subjects/{subject_id}/sections/{section_id}"


async def _rules_for(db: DbSession, subject) -> rules.GradingRules:
    return rules.for_semester(await rules.get_rules(db), subject.semester)


async def _subject_grades(db: DbSession, subject: Subject, section: Section) -> SubjectGrades:
    roster = await grades.subject_roster(db, subject.id, section.id, await _rules_for(db, subject))
    return SubjectGrades(
        subject=SubjectRead.model_validate(subject),
        section=SectionRead.model_validate(section),
        computed_terms=sorted(await evaluations.computed_terms(db, subject.id, section.id)),
        students=[
            StudentGrades(
                student=StudentSummary.model_validate(row.student),
                grades=[
                    TermGrade(term=t, value=v, absent=t in row.absent)
                    for t, v in row.grades.items()
                ],
                average=row.average,
                final_grade=row.final_grade,
                passed=row.passed,
            )
            for row in roster
        ],
    )


@router.get("/subjects", summary="Mis materias")
async def my_subjects(teacher: Teacher, db: DbSession) -> list[TeacherSubject]:
    """Materias asignadas, una por cada sección en la que las imparte, con el número de
    estudiantes de la sección y de notas cargadas por lapso."""
    rows = await grades.teacher_subjects_with_counts(db, teacher.id)
    current = await rules.get_rules(db)
    per_term = await grades.grades_per_term(
        db,
        {
            (subject.id, section.id): rules.for_semester(current, subject.semester)
            for subject, section, _ in rows
        },
    )
    return [
        TeacherSubject(
            **{
                **SubjectRead.model_validate(subject).model_dump(),
                "section": SectionRead.model_validate(section),
                "students_count": count,
                "grades_count": per_term[(subject.id, section.id)],
            }
        )
        for subject, section, count in rows
    ]


@router.get(CLASS + "/grades", summary="Estudiantes y notas de una materia en una sección")
async def subject_grades(
    subject_id: int, section_id: int, teacher: Teacher, db: DbSession
) -> SubjectGrades:
    """Lista los estudiantes de la sección matriculados en la materia con su nota de cada
    lapso (null si falta; `absent` si fue inasistente)."""
    subject, section = await grades.get_teacher_subject(db, teacher.id, subject_id, section_id)
    return await _subject_grades(db, subject, section)


@router.put(CLASS + "/grades", summary="Cargar o corregir notas")
async def save_subject_grades(
    subject_id: int, section_id: int, data: GradesUpdate, teacher: Teacher, db: DbSession
) -> SubjectGrades:
    """Envía solo las notas que cambian. `value: null` borra la nota de ese lapso y
    `absent: true` (sin `value`) marca al estudiante como inasistente (cuenta con la nota
    mínima). En un lapso con plan de evaluación solo se puede marcar inasistente: lo queda
    en cada evaluación del lapso.

    Es todo o nada: si algún estudiante no está matriculado en la sección (404) o alguna
    nota o lapso no cumple la configuración académica (422) no se guarda ninguna. Si el
    periodo de la materia está cerrado responde 409.

    Cada estudiante con notas nuevas o corregidas recibe un aviso (`/student/notifications`).

    Si las notas salen de una importación, envía su `import_job_id` para marcarla como
    confirmada (debe ser de esta materia y estar en `done`; si no, 409).
    """
    subject, section = await grades.get_teacher_subject(db, teacher.id, subject_id, section_id)
    await academic.ensure_open(db, subject.semester)
    if data.import_job_id is not None:
        await import_jobs.mark_confirmed(db, teacher.id, subject.id, section.id, data.import_job_id)
    subject_rules = await _rules_for(db, subject)
    updates = await grades.save_grades(
        db,
        subject.id,
        section.id,
        [(g.student_id, g.term, g.value, g.absent) for g in data.grades],
        updated_by_id=teacher.id,
        rules=subject_rules,
        import_job_id=data.import_job_id,
    )
    created = await notifications.notify_grade_updates(db, subject, updates, subject_rules)
    await notifications.commit_and_publish(db, created)
    return await _subject_grades(db, subject, section)


@router.get(
    CLASS + "/grades/export",
    summary="Descargar las notas de la materia en Excel",
    response_class=Response,
    responses={200: {"content": {grade_export.XLSX: {}}, "description": "Archivo .xlsx"}},
)
async def export_subject_grades(
    subject_id: int, section_id: int, teacher: Teacher, db: DbSession
) -> Response:
    """Hoja «Planilla» con la nota de cada lapso, el promedio, la definitiva y la situación
    de cada estudiante de la sección, y una hoja más por cada lapso con plan de evaluación
    (notas de cada evaluación, acumulado y nota del lapso). También en periodos cerrados."""
    subject, section = await grades.get_teacher_subject(db, teacher.id, subject_id, section_id)
    current = await rules.get_rules(db)
    subject_rules = rules.for_semester(current, subject.semester)
    roster = await grades.subject_roster(db, subject.id, section.id, subject_rules)
    term_plans = {
        term: await evaluations.term_sheet(db, subject.id, section.id, term, subject_rules)
        for term in sorted(await evaluations.computed_terms(db, subject.id, section.id))
        if subject_rules.term_error(term) is None
    }
    content = grade_export.subject_workbook(
        subject, section, subject_rules, roster, term_plans, current.document_label
    )
    name = grade_export.file_name(subject, section, subject_rules)
    return Response(
        content,
        media_type=grade_export.XLSX,
        headers={"Content-Disposition": grade_export.content_disposition(name)},
    )


@router.get(CLASS + "/grade-history", summary="Historial de cambios de notas")
async def subject_grade_history(
    subject_id: int,
    section_id: int,
    teacher: Teacher,
    db: DbSession,
    student_id: Annotated[int | None, Query(description="Solo los de este estudiante")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[GradeChangeRead]:
    """Altas, correcciones y borrados de notas de la materia en la sección, del más
    reciente al más antiguo, con quién los hizo y si fueron a mano o desde una importación de
    Excel."""
    subject, section = await grades.get_teacher_subject(db, teacher.id, subject_id, section_id)
    items, total = await grades.history(
        db, subject.id, student_id=student_id, limit=limit, offset=offset, section_id=section.id
    )
    return Page(
        items=[GradeChangeRead.from_row(*row) for row in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    CLASS + "/grades/import",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Encolar la lectura de un Excel de notas (IA)",
)
async def enqueue_grades_import(
    subject_id: int,
    section_id: int,
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

    El resultado es una previsualización con los estudiantes de la sección: **no se guarda
    nada**. Envía las filas que quieras guardar (ok o warning, ya corregidas) a
    `PUT /teacher/subjects/{id}/sections/{section_id}/grades`. Las celdas «I», «NP»,
    «Inasistente»... se leen como inasistencias.
    """
    subject, section = await grades.get_teacher_subject(db, teacher.id, subject_id, section_id)
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
        subject_id=subject.id,
        section_id=section.id,
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
    section_id: int | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[ImportJobSummary]:
    jobs = await import_jobs.list_jobs(db, teacher.id, subject_id, limit, section_id)
    return [ImportJobSummary.model_validate(j) for j in jobs]


@router.get("/import-jobs/{job_id}", summary="Estado y resultado de una importación")
async def get_import_job(job_id: str, teacher: Teacher, db: DbSession) -> ImportJobRead:
    return ImportJobRead.model_validate(await import_jobs.get_job(db, teacher.id, job_id))
