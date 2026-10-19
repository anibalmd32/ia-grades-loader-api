"""Plan de evaluación de cada lapso y notas por evaluación (profesor de la materia)."""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status

from app.api.deps import DbSession, require_roles
from app.core.errors import InvalidDataError
from app.models import Evaluation, Role, Subject, User
from app.schemas.academic import SubjectRead
from app.schemas.evaluations import (
    EvaluationCreate,
    EvaluationGradesUpdate,
    EvaluationPlan,
    EvaluationRead,
    EvaluationUpdate,
    EvaluationValue,
    StudentEvaluationGrades,
    TermEvaluationGrades,
    TermPlan,
)
from app.schemas.grades import StudentSummary
from app.services import academic, evaluations, grade_export, grades, notifications, rules
from app.services.rules import MAX_TERMS

router = APIRouter(prefix="/teacher/subjects/{subject_id}", tags=["Profesor: evaluaciones"])

Teacher = Annotated[User, Depends(require_roles(Role.TEACHER))]
TermPath = Annotated[int, Path(ge=1, le=MAX_TERMS, description="Lapso")]


async def _rules_for(db: DbSession, subject: Subject) -> rules.GradingRules:
    return rules.for_semester(await rules.get_rules(db), subject.semester)


async def _checked_rules(db: DbSession, subject: Subject, term: int) -> rules.GradingRules:
    """Reglas de la materia, comprobando que el lapso existe en ellas (422 si no)."""
    subject_rules = await _rules_for(db, subject)
    if problem := subject_rules.term_error(term):
        raise InvalidDataError(problem)
    return subject_rules


async def _notify(db: DbSession, subject: Subject, updates, subject_rules) -> None:
    """Avisa a los estudiantes de sus notas nuevas o corregidas."""
    created = await notifications.notify_grade_updates(db, subject, updates, subject_rules)
    if created:
        await notifications.commit_and_publish(db, created)


def _read(evaluation: Evaluation, graded: int) -> EvaluationRead:
    return EvaluationRead(
        id=evaluation.id,
        term=evaluation.term,
        name=evaluation.name,
        weight=evaluation.weight,
        due_date=evaluation.due_date,
        graded_count=graded,
    )


def _term_plan(term: int, plan: list[Evaluation]) -> TermPlan:
    total = sum((e.weight for e in plan), Decimal(0))
    return TermPlan(
        term=term,
        weights_total=total,
        evaluations_count=len(plan),
        complete=bool(plan) and total == evaluations.FULL_PLAN,
    )


async def _graded_counts(db: DbSession, subject_id: int) -> dict[int, int]:
    return {e.id: count for e, count in await evaluations.list_plan(db, subject_id)}


@router.get("/evaluations", summary="Plan de evaluación de la materia")
async def evaluation_plan(subject_id: int, teacher: Teacher, db: DbSession) -> EvaluationPlan:
    """Evaluaciones de cada lapso con su porcentaje. Un lapso con evaluaciones calcula su
    nota a partir de ellas cuando sus porcentajes suman 100 %."""
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    subject_rules = await _rules_for(db, subject)
    items = await evaluations.list_plan(db, subject.id)
    return EvaluationPlan(
        subject=SubjectRead.model_validate(subject),
        terms=[
            _term_plan(term, [e for e, _ in items if e.term == term])
            for term in subject_rules.terms
        ],
        evaluations=[_read(e, count) for e, count in items],
    )


@router.post("/evaluations", status_code=status.HTTP_201_CREATED, summary="Añadir una evaluación")
async def create_evaluation(
    subject_id: int, data: EvaluationCreate, teacher: Teacher, db: DbSession
) -> EvaluationRead:
    """Los porcentajes de un lapso no pueden pasar del 100 % (422). La primera evaluación
    de un lapso exige que no tenga notas cargadas directamente (409)."""
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    await academic.ensure_open(db, subject.semester)
    evaluation = await evaluations.create_evaluation(
        db,
        subject,
        await _rules_for(db, subject),
        term=data.term,
        name=data.name,
        weight=data.weight,
        due_date=data.due_date,
    )
    return _read(evaluation, 0)


@router.patch("/evaluations/{evaluation_id}", summary="Modificar una evaluación")
async def update_evaluation(
    subject_id: int, evaluation_id: int, data: EvaluationUpdate, teacher: Teacher, db: DbSession
) -> EvaluationRead:
    """Cambiar el porcentaje recalcula la nota del lapso de todos los estudiantes."""
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    await academic.ensure_open(db, subject.semester)
    evaluation = await evaluations.get_evaluation(db, subject.id, evaluation_id)
    changes = data.model_dump(exclude_unset=True)
    changes = {k: v for k, v in changes.items() if v is not None or k == "due_date"}
    subject_rules = await _rules_for(db, subject)
    updates = await evaluations.update_evaluation(
        db, evaluation, subject_rules, changes, teacher.id
    )
    await _notify(db, subject, updates, subject_rules)
    return _read(evaluation, (await _graded_counts(db, subject.id)).get(evaluation.id, 0))


@router.delete(
    "/evaluations/{evaluation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar una evaluación y sus notas",
)
async def delete_evaluation(
    subject_id: int, evaluation_id: int, teacher: Teacher, db: DbSession
) -> None:
    """Borra también sus notas y recalcula la nota del lapso. Si era la última del lapso,
    este vuelve a cargarse directamente (queda sin notas)."""
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    await academic.ensure_open(db, subject.semester)
    evaluation = await evaluations.get_evaluation(db, subject.id, evaluation_id)
    subject_rules = await _rules_for(db, subject)
    updates = await evaluations.delete_evaluation(db, evaluation, subject_rules, teacher.id)
    await _notify(db, subject, updates, subject_rules)


async def _term_grades(db: DbSession, subject: Subject, term: int) -> TermEvaluationGrades:
    subject_rules = await _checked_rules(db, subject, term)
    plan, rows = await evaluations.term_sheet(db, subject.id, term, subject_rules)
    counts = await _graded_counts(db, subject.id)
    return TermEvaluationGrades(
        subject=SubjectRead.model_validate(subject),
        plan=_term_plan(term, plan),
        evaluations=[_read(e, counts.get(e.id, 0)) for e in plan],
        students=[
            StudentEvaluationGrades(
                student=StudentSummary.model_validate(row.student),
                grades=[EvaluationValue(evaluation_id=e, value=v) for e, v in row.values.items()],
                accumulated=row.accumulated,
                term_grade=row.term_grade,
            )
            for row in rows
        ],
    )


@router.get("/terms/{term}/evaluation-grades", summary="Notas de las evaluaciones de un lapso")
async def term_evaluation_grades(
    subject_id: int, term: TermPath, teacher: Teacher, db: DbSession
) -> TermEvaluationGrades:
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    return await _term_grades(db, subject, term)


@router.get(
    "/terms/{term}/evaluation-grades/export",
    summary="Descargar las notas de las evaluaciones de un lapso en Excel",
    response_class=Response,
    responses={200: {"content": {grade_export.XLSX: {}}, "description": "Archivo .xlsx"}},
)
async def export_term_evaluation_grades(
    subject_id: int, term: TermPath, teacher: Teacher, db: DbSession
) -> Response:
    """Una fila por estudiante con la nota de cada evaluación del lapso, lo acumulado y la
    nota del lapso (si ya se puede calcular). También en periodos cerrados."""
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    subject_rules = await _checked_rules(db, subject, term)
    plan, rows = await evaluations.term_sheet(db, subject.id, term, subject_rules)
    current = await rules.get_rules(db)
    content = grade_export.term_workbook(
        subject, subject_rules, term, plan, rows, current.document_label
    )
    name = grade_export.file_name(subject, subject_rules, term)
    return Response(
        content,
        media_type=grade_export.XLSX,
        headers={"Content-Disposition": grade_export.content_disposition(name)},
    )


@router.put("/terms/{term}/evaluation-grades", summary="Calificar evaluaciones de un lapso")
async def save_term_evaluation_grades(
    subject_id: int, term: TermPath, data: EvaluationGradesUpdate, teacher: Teacher, db: DbSession
) -> TermEvaluationGrades:
    """Envía solo las notas que cambian (`value: null` borra). Todo o nada: 404 si una
    evaluación no es de ese lapso o un estudiante no está matriculado, 422 si una nota no
    cumple la escala y 409 si el periodo está cerrado. La nota del lapso se recalcula y cada
    estudiante con notas nuevas recibe un aviso."""
    subject = await grades.get_teacher_subject(db, teacher.id, subject_id)
    await academic.ensure_open(db, subject.semester)
    subject_rules = await _checked_rules(db, subject, term)
    updates = await evaluations.save_evaluation_grades(
        db,
        subject.id,
        term,
        [(g.student_id, g.evaluation_id, g.value) for g in data.grades],
        updated_by_id=teacher.id,
        rules=subject_rules,
    )
    await _notify(db, subject, updates, subject_rules)
    return await _term_grades(db, subject, term)
