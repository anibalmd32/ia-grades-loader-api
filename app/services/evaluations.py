"""Plan de evaluación de cada lapso y sus notas (lo gestiona el profesor de la materia en
su sección: cada sección tiene su propio plan).

Un lapso puede llevar su nota cargada directamente o tener un plan de evaluaciones
(exámenes, talleres...) con su porcentaje. Si tiene plan, la nota del lapso se calcula:
suma de la nota de cada evaluación por su porcentaje, cuando el plan llega al 100 % y el
estudiante tiene todas calificadas. Mientras tanto el lapso queda sin nota (pendiente).

Un estudiante que no se presentó a una evaluación queda como inasistente: cuenta con la nota
mínima de la escala. Si es inasistente en todas las del lapso, el lapso también lo es.
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
    StudentSection,
    Subject,
    User,
)
from app.services.rules import GradingRules, fmt
from app.services.sections import in_section

FULL_PLAN = Decimal(100)


@dataclass(frozen=True)
class GradeUpdate:
    """Nota que ha cambiado (para avisar al estudiante)."""

    student_id: int
    term: int
    evaluation_name: str | None  # None = nota del lapso
    value: Decimal | None  # None = se borró
    absent: bool = False  # inasistente (value es la nota mínima)


def _of(subject_id: int, section_id: int):
    return (Evaluation.subject_id == subject_id) & (Evaluation.section_id == section_id)


async def computed_terms(db: AsyncSession, subject_id: int, section_id: int) -> set[int]:
    """Lapsos de la materia en la sección que tienen plan de evaluación (su nota se calcula)."""
    rows = await db.scalars(select(Evaluation.term).where(_of(subject_id, section_id)).distinct())
    return set(rows)


async def list_plan(
    db: AsyncSession, subject_id: int, section_id: int
) -> list[tuple[Evaluation, int]]:
    """Evaluaciones de la materia en la sección (por lapso y en el orden en que se crearon)
    con el número de estudiantes ya calificados en cada una."""
    graded = (
        select(EvaluationGrade.evaluation_id, func.count().label("graded"))
        .group_by(EvaluationGrade.evaluation_id)
        .subquery()
    )
    rows = await db.execute(
        select(Evaluation, func.coalesce(graded.c.graded, 0))
        .outerjoin(graded, graded.c.evaluation_id == Evaluation.id)
        .where(_of(subject_id, section_id))
        .order_by(Evaluation.term, Evaluation.id)
    )
    return [(evaluation, count) for evaluation, count in rows]


async def _term_plan(
    db: AsyncSession, subject_id: int, section_id: int, term: int
) -> list[Evaluation]:
    return list(
        await db.scalars(
            select(Evaluation)
            .where(_of(subject_id, section_id), Evaluation.term == term)
            .order_by(Evaluation.id)
        )
    )


async def get_evaluation(
    db: AsyncSession, subject_id: int, section_id: int, evaluation_id: int
) -> Evaluation:
    evaluation = await db.get(Evaluation, evaluation_id)
    if evaluation is None or (evaluation.subject_id, evaluation.section_id) != (
        subject_id,
        section_id,
    ):
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
    section_id: int,
    rules: GradingRules,
    *,
    term: int,
    name: str,
    weight: Decimal,
    due_date: date | None,
) -> Evaluation:
    if problem := rules.term_error(term):
        raise InvalidDataError(problem)
    plan = await _term_plan(db, subject.id, section_id, term)
    _check_name(plan, name)
    if not plan:
        # El lapso pasa de nota directa a calculada: no puede tener notas cargadas a mano
        loaded = await db.scalar(
            select(func.count())
            .select_from(Grade)
            .join(Enrollment, Enrollment.id == Grade.enrollment_id)
            .where(Enrollment.subject_id == subject.id, Grade.term == term, in_section(section_id))
        )
        if loaded:
            raise ConflictError(
                f"El {rules.term_name(term).lower()} ya tiene {loaded} notas cargadas "
                "directamente. Bórralas antes de definir sus evaluaciones."
            )
    evaluation = Evaluation(
        subject_id=subject.id,
        section_id=section_id,
        term=term,
        name=name,
        weight=weight,
        due_date=due_date,
    )
    _check_weights([*plan, evaluation], rules, term)
    db.add(evaluation)
    await db.commit()
    return evaluation


async def update_evaluation(
    db: AsyncSession, evaluation: Evaluation, rules: GradingRules, changes: dict, user_id: int
) -> list["GradeUpdate"]:
    """Cambia nombre, porcentaje o fecha; un porcentaje nuevo recalcula la nota del lapso."""
    plan = await _term_plan(db, evaluation.subject_id, evaluation.section_id, evaluation.term)
    if "name" in changes:
        _check_name(plan, changes["name"], exclude_id=evaluation.id)
    for field, value in changes.items():
        setattr(evaluation, field, value)
    _check_weights(plan, rules, evaluation.term)
    await db.flush()
    updates = await sync_term_grades(
        db, evaluation.subject_id, evaluation.section_id, evaluation.term, rules, user_id
    )
    await db.commit()
    return updates


async def delete_evaluation(
    db: AsyncSession, evaluation: Evaluation, rules: GradingRules, user_id: int
) -> list["GradeUpdate"]:
    """Borra la evaluación y sus notas y recalcula la nota del lapso (sin plan, el lapso
    queda sin nota y se vuelve a cargar directamente)."""
    subject_id, section_id, term = evaluation.subject_id, evaluation.section_id, evaluation.term
    await db.execute(delete(EvaluationGrade).where(EvaluationGrade.evaluation_id == evaluation.id))
    await db.delete(evaluation)
    await db.flush()
    updates = await sync_term_grades(db, subject_id, section_id, term, rules, user_id)
    await db.commit()
    return updates


# --- Notas -------------------------------------------------------------------


@dataclass
class EvaluationRow:
    student: User
    enrollment_id: int
    values: dict[int, Decimal | None]  # evaluación -> nota
    absent: set[int]  # evaluaciones en las que es inasistente (su nota es la mínima)
    accumulated: Decimal | None  # puntos ya obtenidos: suma de nota × porcentaje
    term_grade: Decimal | None  # nota del lapso (solo con el plan completo y todo calificado)

    @property
    def term_absent(self) -> bool:
        """Inasistente en todas las evaluaciones del lapso (y el lapso ya tiene nota)."""
        return self.term_grade is not None and self.absent.issuperset(self.values)


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


async def _values(
    db: AsyncSession, plan: list[Evaluation]
) -> dict[int, dict[int, tuple[Decimal, bool]]]:
    """matrícula -> evaluación -> (nota, inasistente)."""
    values: dict[int, dict[int, tuple[Decimal, bool]]] = defaultdict(dict)
    rows = await db.execute(
        select(
            EvaluationGrade.enrollment_id,
            EvaluationGrade.evaluation_id,
            EvaluationGrade.value,
            EvaluationGrade.absent,
        ).where(EvaluationGrade.evaluation_id.in_([e.id for e in plan]))
    )
    for enrollment_id, evaluation_id, value, absent in rows:
        values[enrollment_id][evaluation_id] = (value, absent)
    return values


async def term_sheet(
    db: AsyncSession, subject_id: int, section_id: int, term: int, rules: GradingRules
) -> tuple[list[Evaluation], list[EvaluationRow]]:
    """Plan del lapso en la sección y la nota de cada estudiante de la sección en cada
    evaluación."""
    plan = await _term_plan(db, subject_id, section_id, term)
    enrollments = (
        await db.execute(
            select(Enrollment.id, User)
            .join(User, User.id == Enrollment.student_id)
            .where(Enrollment.subject_id == subject_id, in_section(section_id))
            .order_by(User.last_name, User.first_name)
        )
    ).all()
    values = await _values(db, plan)
    rows = []
    for enrollment_id, student in enrollments:
        graded = values[enrollment_id]
        own = {e.id: graded[e.id][0] if e.id in graded else None for e in plan}
        rows.append(
            EvaluationRow(
                student=student,
                enrollment_id=enrollment_id,
                values=own,
                absent={e for e, (_, absent) in graded.items() if absent},
                accumulated=accumulated(plan, own, rules),
                term_grade=term_grade(plan, own, rules),
            )
        )
    return plan, rows


async def sync_term_grades(
    db: AsyncSession,
    subject_id: int,
    section_id: int,
    term: int,
    rules: GradingRules,
    user_id: int,
) -> list[GradeUpdate]:
    """Pone en `grades` la nota calculada del lapso de cada estudiante de la sección (o la
    quita si ya no se puede calcular) y registra cada cambio en el historial."""
    plan, rows = await term_sheet(db, subject_id, section_id, term, rules)
    existing = {
        g.enrollment_id: g
        for g in await db.scalars(
            select(Grade).where(
                Grade.enrollment_id.in_([r.enrollment_id for r in rows]), Grade.term == term
            )
        )
    }
    updates = []
    for row in rows:
        grade = existing.get(row.enrollment_id)
        old = None if grade is None else grade.value
        old_absent = grade is not None and grade.absent
        new, new_absent = row.term_grade, row.term_absent
        if (old, old_absent) == (new, new_absent):
            continue
        if new is None:
            await db.execute(delete(Grade).where(Grade.id == grade.id))
        elif grade is None:
            db.add(
                Grade(
                    enrollment_id=row.enrollment_id,
                    term=term,
                    value=new,
                    absent=new_absent,
                    updated_by_id=user_id,
                )
            )
        else:
            grade.value = new
            grade.absent = new_absent
            grade.updated_by_id = user_id
        db.add(
            GradeChange(
                enrollment_id=row.enrollment_id,
                term=term,
                old_value=old,
                new_value=new,
                old_absent=old_absent,
                new_absent=new_absent,
                changed_by_id=user_id,
                source=ChangeSource.EVALUATIONS,
            )
        )
        updates.append(GradeUpdate(row.student.id, term, None, new, new_absent))
    await db.flush()
    return updates


# (estudiante, evaluación, nota, inasistente)
EvaluationEntry = tuple[int, int, Decimal | None, bool]


async def apply_evaluation_grades(
    db: AsyncSession,
    subject_id: int,
    section_id: int,
    term: int,
    entries: list[EvaluationEntry],
    updated_by_id: int,
    rules: GradingRules,
) -> list[GradeUpdate]:
    """Crea, modifica o borra (valor None) notas de evaluaciones de un lapso y recalcula su
    nota, sin confirmar la transacción. Todo o nada, como la carga de notas de los lapsos.
    Un inasistente guarda la nota mínima de la escala."""
    plan = {e.id: e for e in await _term_plan(db, subject_id, section_id, term)}
    for student_id, evaluation_id, value, _ in entries:
        if evaluation_id not in plan:
            raise NotFoundError(
                f"La evaluación {evaluation_id} no es del {rules.term_name(term).lower()} "
                "de esta materia"
            )
        if value is not None and (problem := rules.grade_error(value)):
            name = plan[evaluation_id].name
            raise InvalidDataError(f"{problem} (estudiante {student_id}, {name})")

    student_ids = {entry[0] for entry in entries}
    enrollment_by_student = dict(
        (
            await db.execute(
                select(Enrollment.student_id, Enrollment.id).where(
                    Enrollment.subject_id == subject_id,
                    Enrollment.student_id.in_(student_ids),
                    in_section(section_id),
                )
            )
        ).all()
    )
    if missing := sorted(student_ids - enrollment_by_student.keys()):
        raise NotFoundError(
            "Estudiantes no matriculados en la materia en esta sección: "
            + ", ".join(map(str, missing))
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
    for student_id, evaluation_id, value, absent in entries:
        if absent:
            value = rules.min_grade
        enrollment_id = enrollment_by_student[student_id]
        grade = existing.get((enrollment_id, evaluation_id))
        old = None if grade is None else grade.value
        old_absent = grade is not None and grade.absent
        if (old, old_absent) == (value, absent):
            continue
        if value is None:
            await db.delete(grade)
        elif grade is None:
            db.add(
                EvaluationGrade(
                    evaluation_id=evaluation_id,
                    enrollment_id=enrollment_id,
                    value=value,
                    absent=absent,
                    updated_by_id=updated_by_id,
                )
            )
        else:
            grade.value = value
            grade.absent = absent
            grade.updated_by_id = updated_by_id
        name = plan[evaluation_id].name
        db.add(
            GradeChange(
                enrollment_id=enrollment_id,
                term=term,
                evaluation_name=name,
                old_value=old,
                new_value=value,
                old_absent=old_absent,
                new_absent=absent,
                changed_by_id=updated_by_id,
                source=ChangeSource.MANUAL,
            )
        )
        updates.append(GradeUpdate(student_id, term, name, value, absent))
    await db.flush()
    updates += await sync_term_grades(db, subject_id, section_id, term, rules, updated_by_id)
    return updates


async def save_evaluation_grades(
    db: AsyncSession,
    subject_id: int,
    section_id: int,
    term: int,
    entries: list[EvaluationEntry],
    updated_by_id: int,
    rules: GradingRules,
) -> list[GradeUpdate]:
    """apply_evaluation_grades y confirma."""
    updates = await apply_evaluation_grades(
        db, subject_id, section_id, term, entries, updated_by_id, rules
    )
    await db.commit()
    return updates


async def mark_term_absent(
    db: AsyncSession,
    subject_id: int,
    section_id: int,
    term: int,
    student_ids: list[int],
    updated_by_id: int,
    rules: GradingRules,
) -> list[GradeUpdate]:
    """Inasistente en todo un lapso con plan de evaluación: lo es en cada evaluación del
    lapso (así, con el plan al 100 %, el lapso queda inasistente). Sin confirmar."""
    plan = await _term_plan(db, subject_id, section_id, term)
    entries = [(student_id, e.id, None, True) for student_id in student_ids for e in plan]
    return await apply_evaluation_grades(
        db, subject_id, section_id, term, entries, updated_by_id, rules
    )


async def student_evaluations(
    db: AsyncSession, enrollment_ids: list[int]
) -> dict[int, list[tuple[Evaluation, Decimal | None, bool]]]:
    """Evaluaciones de las materias de cada matrícula (las de la sección del estudiante)
    con su nota y si fue inasistente."""
    if not enrollment_ids:
        return {}
    rows = await db.execute(
        select(Enrollment.id, Evaluation, EvaluationGrade.value, EvaluationGrade.absent)
        .join(Subject, Subject.id == Enrollment.subject_id)
        .join(
            StudentSection,
            (StudentSection.student_id == Enrollment.student_id)
            & (StudentSection.semester_id == Subject.semester_id),
        )
        .join(
            Evaluation,
            (Evaluation.subject_id == Enrollment.subject_id)
            & (Evaluation.section_id == StudentSection.section_id),
        )
        .outerjoin(
            EvaluationGrade,
            (EvaluationGrade.evaluation_id == Evaluation.id)
            & (EvaluationGrade.enrollment_id == Enrollment.id),
        )
        .where(Enrollment.id.in_(enrollment_ids))
        .order_by(Evaluation.term, Evaluation.id)
    )
    result: dict[int, list[tuple[Evaluation, Decimal | None, bool]]] = defaultdict(list)
    for enrollment_id, evaluation, value, absent in rows:
        result[enrollment_id].append((evaluation, value, bool(absent)))
    return result
