"""Plan de evaluación de cada lapso y sus notas (lo gestiona el profesor de la materia).

Un lapso puede llevar su nota cargada directamente o tener un plan de evaluaciones
(exámenes, talleres...) con su porcentaje. Si tiene plan, la nota del lapso se calcula:
suma de la nota de cada evaluación por su porcentaje, cuando el plan llega al 100 % y el
estudiante tiene todas calificadas. Mientras tanto el lapso queda sin nota (pendiente).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InvalidDataError, NotFoundError
from app.models import (
    ChangeSource,
    Enrollment,
    Evaluation,
    EvaluationGrade,
    Grade,
    GradeChange,
    Subject,
    User,
)
from app.services.rules import GradingRules, fmt

FULL_PLAN = Decimal(100)


@dataclass(frozen=True)
class GradeUpdate:
    """Nota que ha cambiado (para avisar al estudiante)."""

    student_id: int
    term: int
    evaluation_name: str | None  # None = nota del lapso
    value: Decimal | None  # None = se borró


async def computed_terms(db: AsyncSession, subject_id: int) -> set[int]:
    """Lapsos de la materia que tienen plan de evaluación (su nota se calcula)."""
    rows = await db.scalars(
        select(Evaluation.term).where(Evaluation.subject_id == subject_id).distinct()
    )
    return set(rows)


async def list_plan(db: AsyncSession, subject_id: int) -> list[tuple[Evaluation, int]]:
    """Evaluaciones de la materia (por lapso y en el orden en que se crearon) con el número
    de estudiantes ya calificados en cada una."""
    graded = (
        select(EvaluationGrade.evaluation_id, func.count().label("graded"))
        .group_by(EvaluationGrade.evaluation_id)
        .subquery()
    )
    rows = await db.execute(
        select(Evaluation, func.coalesce(graded.c.graded, 0))
        .outerjoin(graded, graded.c.evaluation_id == Evaluation.id)
        .where(Evaluation.subject_id == subject_id)
        .order_by(Evaluation.term, Evaluation.id)
    )
    return [(evaluation, count) for evaluation, count in rows]


async def _term_plan(db: AsyncSession, subject_id: int, term: int) -> list[Evaluation]:
    return list(
        await db.scalars(
            select(Evaluation)
            .where(Evaluation.subject_id == subject_id, Evaluation.term == term)
            .order_by(Evaluation.id)
        )
    )


async def get_evaluation(db: AsyncSession, subject_id: int, evaluation_id: int) -> Evaluation:
    evaluation = await db.get(Evaluation, evaluation_id)
    if evaluation is None or evaluation.subject_id != subject_id:
        raise NotFoundError("Evaluación no encontrada en esta materia")
    return evaluation


def _check_weights(plan: list[Evaluation], rules: GradingRules, term: int) -> None:
    total = sum((e.weight for e in plan), Decimal(0))
    if total > FULL_PLAN:
        raise InvalidDataError(
            f"Los porcentajes del {rules.term_name(term).lower()} suman {fmt(total)} %: "
            "no pueden pasar del 100 %"
        )


def _check_name(plan: list[Evaluation], name: str, exclude_id: int | None = None) -> None:
    if any(e.name.lower() == name.lower() and e.id != exclude_id for e in plan):
        raise ConflictError(f"Ya hay una evaluación «{name}» en ese lapso")


async def create_evaluation(
    db: AsyncSession,
    subject: Subject,
    rules: GradingRules,
    *,
    term: int,
    name: str,
    weight: Decimal,
    due_date: date | None,
) -> Evaluation:
    if problem := rules.term_error(term):
        raise InvalidDataError(problem)
    plan = await _term_plan(db, subject.id, term)
    _check_name(plan, name)
    if not plan:
        # El lapso pasa de nota directa a calculada: no puede tener notas cargadas a mano
        loaded = await db.scalar(
            select(func.count())
            .select_from(Grade)
            .join(Enrollment, Enrollment.id == Grade.enrollment_id)
            .where(Enrollment.subject_id == subject.id, Grade.term == term)
        )
        if loaded:
            raise ConflictError(
                f"El {rules.term_name(term).lower()} ya tiene {loaded} notas cargadas "
                "directamente. Bórralas antes de definir sus evaluaciones."
            )
    evaluation = Evaluation(
        subject_id=subject.id, term=term, name=name, weight=weight, due_date=due_date
    )
    _check_weights([*plan, evaluation], rules, term)
    db.add(evaluation)
    await db.commit()
    return evaluation


async def update_evaluation(
    db: AsyncSession, evaluation: Evaluation, rules: GradingRules, changes: dict, user_id: int
) -> list["GradeUpdate"]:
    """Cambia nombre, porcentaje o fecha; un porcentaje nuevo recalcula la nota del lapso."""
    plan = await _term_plan(db, evaluation.subject_id, evaluation.term)
    if "name" in changes:
        _check_name(plan, changes["name"], exclude_id=evaluation.id)
    for field, value in changes.items():
        setattr(evaluation, field, value)
    _check_weights(plan, rules, evaluation.term)
    await db.flush()
    updates = await sync_term_grades(db, evaluation.subject_id, evaluation.term, rules, user_id)
    await db.commit()
    return updates


async def delete_evaluation(
    db: AsyncSession, evaluation: Evaluation, rules: GradingRules, user_id: int
) -> list["GradeUpdate"]:
    """Borra la evaluación y sus notas y recalcula la nota del lapso (sin plan, el lapso
    queda sin nota y se vuelve a cargar directamente)."""
    subject_id, term = evaluation.subject_id, evaluation.term
    await db.execute(delete(EvaluationGrade).where(EvaluationGrade.evaluation_id == evaluation.id))
    await db.delete(evaluation)
    await db.flush()
    updates = await sync_term_grades(db, subject_id, term, rules, user_id)
    await db.commit()
    return updates


# --- Notas -------------------------------------------------------------------


@dataclass
class EvaluationRow:
    student: User
    enrollment_id: int
    values: dict[int, Decimal | None]  # evaluación -> nota
    accumulated: Decimal | None  # puntos ya obtenidos: suma de nota × porcentaje
    term_grade: Decimal | None  # nota del lapso (solo con el plan completo y todo calificado)


def term_grade(
    plan: list[Evaluation], values: dict[int, Decimal | None], rules: GradingRules
) -> Decimal | None:
    """Nota del lapso: solo con el plan al 100 % y todas las evaluaciones calificadas."""
    if not plan or sum((e.weight for e in plan), Decimal(0)) != FULL_PLAN:
        return None
    if any(values.get(e.id) is None for e in plan):
        return None
    return rules.quantize(sum((values[e.id] * e.weight for e in plan), Decimal(0)) / FULL_PLAN)


def accumulated(
    plan: list[Evaluation], values: dict[int, Decimal | None], rules: GradingRules
) -> Decimal | None:
    """Lo que lleva acumulado en el lapso con las evaluaciones ya calificadas."""
    graded = [(e.weight, values[e.id]) for e in plan if values.get(e.id) is not None]
    if not graded:
        return None
    return rules.quantize(sum((w * v for w, v in graded), Decimal(0)) / FULL_PLAN)


async def _values(db: AsyncSession, plan: list[Evaluation]) -> dict[int, dict[int, Decimal]]:
    """matrícula -> evaluación -> nota."""
    values: dict[int, dict[int, Decimal]] = defaultdict(dict)
    rows = await db.execute(
        select(
            EvaluationGrade.enrollment_id, EvaluationGrade.evaluation_id, EvaluationGrade.value
        ).where(EvaluationGrade.evaluation_id.in_([e.id for e in plan]))
    )
    for enrollment_id, evaluation_id, value in rows:
        values[enrollment_id][evaluation_id] = value
    return values


async def term_sheet(
    db: AsyncSession, subject_id: int, term: int, rules: GradingRules
) -> tuple[list[Evaluation], list[EvaluationRow]]:
    """Plan del lapso y la nota de cada estudiante en cada evaluación."""
    plan = await _term_plan(db, subject_id, term)
    enrollments = (
        await db.execute(
            select(Enrollment.id, User)
            .join(User, User.id == Enrollment.student_id)
            .where(Enrollment.subject_id == subject_id)
            .order_by(User.last_name, User.first_name)
        )
    ).all()
    values = await _values(db, plan)
    rows = []
    for enrollment_id, student in enrollments:
        own = {e.id: values[enrollment_id].get(e.id) for e in plan}
        rows.append(
            EvaluationRow(
                student=student,
                enrollment_id=enrollment_id,
                values=own,
                accumulated=accumulated(plan, own, rules),
                term_grade=term_grade(plan, own, rules),
            )
        )
    return plan, rows


async def sync_term_grades(
    db: AsyncSession, subject_id: int, term: int, rules: GradingRules, user_id: int
) -> list[GradeUpdate]:
    """Pone en `grades` la nota calculada del lapso de cada estudiante (o la quita si ya no
    se puede calcular) y registra cada cambio en el historial."""
    plan, rows = await term_sheet(db, subject_id, term, rules)
    existing = {
        g.enrollment_id: g
        for g in await db.scalars(
            select(Grade)
            .join(Enrollment, Enrollment.id == Grade.enrollment_id)
            .where(Enrollment.subject_id == subject_id, Grade.term == term)
        )
    }
    updates = []
    for row in rows:
        grade = existing.get(row.enrollment_id)
        old = None if grade is None else grade.value
        new = row.term_grade
        if old == new:
            continue
        if new is None:
            await db.execute(delete(Grade).where(Grade.id == grade.id))
        elif grade is None:
            db.add(
                Grade(enrollment_id=row.enrollment_id, term=term, value=new, updated_by_id=user_id)
            )
        else:
            grade.value = new
            grade.updated_by_id = user_id
        db.add(
            GradeChange(
                enrollment_id=row.enrollment_id,
                term=term,
                old_value=old,
                new_value=new,
                changed_by_id=user_id,
                source=ChangeSource.EVALUATIONS,
            )
        )
        updates.append(GradeUpdate(row.student.id, term, None, new))
    await db.flush()
    return updates


async def save_evaluation_grades(
    db: AsyncSession,
    subject_id: int,
    term: int,
    entries: list[tuple[int, int, Decimal | None]],
    updated_by_id: int,
    rules: GradingRules,
) -> list[GradeUpdate]:
    """Crea, modifica o borra (valor None) notas de (estudiante, evaluación, valor) de un
    lapso y recalcula su nota. Todo o nada, como la carga de notas de los lapsos."""
    plan = {e.id: e for e in await _term_plan(db, subject_id, term)}
    for student_id, evaluation_id, value in entries:
        if evaluation_id not in plan:
            raise NotFoundError(
                f"La evaluación {evaluation_id} no es del {rules.term_name(term).lower()} "
                "de esta materia"
            )
        if value is not None and (problem := rules.grade_error(value)):
            name = plan[evaluation_id].name
            raise InvalidDataError(f"{problem} (estudiante {student_id}, {name})")

    student_ids = {student_id for student_id, _, _ in entries}
    enrollment_by_student = dict(
        (
            await db.execute(
                select(Enrollment.student_id, Enrollment.id).where(
                    Enrollment.subject_id == subject_id, Enrollment.student_id.in_(student_ids)
                )
            )
        ).all()
    )
    if missing := sorted(student_ids - enrollment_by_student.keys()):
        raise NotFoundError(
            f"Estudiantes no matriculados en la materia: {', '.join(map(str, missing))}"
        )

    existing = {
        (g.enrollment_id, g.evaluation_id): g
        for g in await db.scalars(
            select(EvaluationGrade).where(
                EvaluationGrade.evaluation_id.in_(plan),
                EvaluationGrade.enrollment_id.in_(enrollment_by_student.values()),
            )
        )
    }
    updates = []
    for student_id, evaluation_id, value in entries:
        enrollment_id = enrollment_by_student[student_id]
        grade = existing.get((enrollment_id, evaluation_id))
        old = None if grade is None else grade.value
        if old == value:
            continue
        if value is None:
            await db.delete(grade)
        elif grade is None:
            db.add(
                EvaluationGrade(
                    evaluation_id=evaluation_id,
                    enrollment_id=enrollment_id,
                    value=value,
                    updated_by_id=updated_by_id,
                )
            )
        else:
            grade.value = value
            grade.updated_by_id = updated_by_id
        name = plan[evaluation_id].name
        db.add(
            GradeChange(
                enrollment_id=enrollment_id,
                term=term,
                evaluation_name=name,
                old_value=old,
                new_value=value,
                changed_by_id=updated_by_id,
                source=ChangeSource.MANUAL,
            )
        )
        updates.append(GradeUpdate(student_id, term, name, value))
    await db.flush()
    updates += await sync_term_grades(db, subject_id, term, rules, updated_by_id)
    await db.commit()
    return updates


async def student_evaluations(
    db: AsyncSession, enrollment_ids: list[int]
) -> dict[int, list[tuple[Evaluation, Decimal | None]]]:
    """Evaluaciones de las materias de cada matrícula con la nota del estudiante."""
    if not enrollment_ids:
        return {}
    rows = await db.execute(
        select(Enrollment.id, Evaluation, EvaluationGrade.value)
        .join(Evaluation, Evaluation.subject_id == Enrollment.subject_id)
        .outerjoin(
            EvaluationGrade,
            (EvaluationGrade.evaluation_id == Evaluation.id)
            & (EvaluationGrade.enrollment_id == Enrollment.id),
        )
        .where(Enrollment.id.in_(enrollment_ids))
        .order_by(Evaluation.term, Evaluation.id)
    )
    result: dict[int, list[tuple[Evaluation, Decimal | None]]] = defaultdict(list)
    for enrollment_id, evaluation, value in rows:
        result[enrollment_id].append((evaluation, value))
    return result
