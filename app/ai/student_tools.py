"""Herramientas de consulta de notas para la IA (chat de la API y servidor MCP).

Regla de seguridad: el id del estudiante NUNCA es un argumento de la herramienta. Lo pone
el servidor a partir del usuario autenticado, así que el modelo no puede consultar notas
de otra persona aunque se lo pidan.
"""

import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import rules as rules_service
from app.services import student_grades
from app.services.rules import GradingRules, fmt
from app.services.student_grades import Standing, SubjectGrades


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _standing_text(standing: Standing, rules: GradingRules) -> str:
    terms = rules.term_label_plural.lower()
    return {
        Standing.PASSED: f"aprobada (todos los {terms} tienen nota)",
        Standing.FAILED: f"reprobada (todos los {terms} tienen nota)",
        Standing.SECURED: "aprobada asegurada: aprueba aunque saque la nota mínima en lo que falta",
        Standing.IN_PROGRESS: f"en curso: depende de los {terms} que faltan",
        Standing.IMPOSSIBLE: f"ya no puede aprobar: ni con {fmt(rules.max_grade)} en lo que "
        "falta llega a la nota aprobatoria",
    }[standing]


def _situation(item: SubjectGrades) -> dict[str, Any]:
    p = item.projection
    term = item.rules.term_label.lower()
    return {
        "estado": _standing_text(p.standing, item.rules),
        "definitiva": _number(p.final_grade),
        f"{item.rules.term_label_plural.lower()}_sin_nota": p.pending_terms,
        f"nota_minima_necesaria_en_cada_{term}_sin_nota": _number(p.needed_average),
    }


def _grade(value: Decimal | None, absent: bool) -> float | str | None:
    """La nota, o "inasistente" (no se presentó; cuenta con la nota mínima)."""
    return "inasistente" if absent else _number(value)


def _evaluations(item: SubjectGrades) -> dict[str, Any]:
    """Plan de evaluación por lapso, con lo acumulado (calculado aquí, no por el modelo)."""
    rules = item.rules
    by_term: dict[int, list] = {}
    for evaluation, value, absent in item.evaluations:
        by_term.setdefault(evaluation.term, []).append((evaluation, value, absent))
    result = {}
    for term, plan in by_term.items():
        graded = [(e.weight, v) for e, v, _ in plan if v is not None]
        accumulated = (
            rules.quantize(sum((w * v for w, v in graded), Decimal(0)) / 100) if graded else None
        )
        result[rules.term_name(term)] = {
            "evaluaciones": [
                {
                    "nombre": e.name,
                    "porcentaje": _number(e.weight),
                    "fecha": None if e.due_date is None else e.due_date.isoformat(),
                    "nota": _grade(v, absent),
                }
                for e, v, absent in plan
            ],
            "porcentaje_planificado": _number(sum((e.weight for e, _, _ in plan), Decimal(0))),
            "puntos_acumulados": _number(accumulated),
        }
    return result


def _subject_dict(item: SubjectGrades) -> dict[str, Any]:
    rules = item.rules
    return {
        "codigo": item.subject.code,
        "materia": item.subject.name,
        rules.period_label.lower(): item.subject.semester.name,
        "seccion": None if item.section is None else item.section.name,
        # Un estudiante tiene un solo profesor por materia: el de su sección
        "profesor": item.teacher,
        "notas": {
            rules.term_name(term): _grade(value, term in item.absent)
            for term, value in item.grades.items()
        },
        "promedio_actual": _number(item.average),
        # Solo los lapsos con plan de evaluación (su nota sale de las evaluaciones)
        "planes_de_evaluacion": _evaluations(item),
        # Calculada por el servidor: el modelo debe usar este dato, no hacer la cuenta
        "situacion": _situation(item),
        # Un periodo cerrado conserva las reglas con que se evaluó
        "regla_de_la_definitiva": rules.final_grade_text,
        "escala_de_la_materia": f"{rules.scale_text}, se aprueba con {fmt(rules.passing_grade)}",
    }


def _scale(rules: GradingRules) -> dict[str, Any]:
    return {
        "institucion": rules.institution_name,
        "nota_minima": _number(rules.min_grade),
        "nota_maxima": _number(rules.max_grade),
        "nota_aprobatoria": _number(rules.passing_grade),
        f"{rules.term_label_plural.lower()}_por_{rules.period_label.lower()}": rules.terms_count,
        "pesos": rules.weights_text,
        "regla_de_la_definitiva": rules.final_grade_text,
    }


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


# --- Herramientas ------------------------------------------------------------


class NoArgs(BaseModel):
    pass


class SubjectArgs(BaseModel):
    materia: str = Field(
        description="Nombre o código de la materia tal como lo menciona el estudiante "
        "(p. ej. 'matemática', 'MAT-101')"
    )


async def resumen_de_notas(db: AsyncSession, student_id: int, _: NoArgs) -> dict[str, Any]:
    rules = await rules_service.get_rules(db)
    subjects = await student_grades.student_subjects(db, student_id, rules)
    return {
        "escala": _scale(rules),
        "materias": [_subject_dict(s) for s in subjects],
        "promedio_general": _number(student_grades.overall_average(subjects)),
    }


async def notas_de_materia(db: AsyncSession, student_id: int, args: SubjectArgs) -> dict[str, Any]:
    rules = await rules_service.get_rules(db)
    subjects = await student_grades.student_subjects(db, student_id, rules)
    query = _normalize(args.materia)
    matches = [
        s
        for s in subjects
        if query in (_normalize(s.subject.code), _normalize(s.subject.name))
        or query in _normalize(s.subject.name)
        or query in _normalize(s.subject.code)
    ]
    if len(matches) == 1:
        return {"escala": _scale(rules), **_subject_dict(matches[0])}
    return {
        "aviso": "No cursa ninguna materia con ese nombre; muéstrale las que cursa"
        if not matches
        else "Varias materias coinciden; pregúntale a cuál se refiere",
        "materias_del_estudiante": [
            {"codigo": s.subject.code, "materia": s.subject.name} for s in (matches or subjects)
        ],
    }


@dataclass(frozen=True)
class StudentTool:
    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[[AsyncSession, int, Any], Awaitable[dict[str, Any]]]

    def openai_schema(self) -> dict[str, Any]:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": schema,
            },
        }

    async def run(self, db: AsyncSession, student_id: int, raw_args: dict[str, Any]) -> dict:
        return await self.handler(db, student_id, self.args.model_validate(raw_args or {}))


TOOLS: dict[str, StudentTool] = {
    tool.name: tool
    for tool in (
        StudentTool(
            name="resumen_de_notas",
            description="Devuelve todas las materias del estudiante con su sección, su "
            "profesor, su nota de cada lapso (null si aún no se ha cargado; 'inasistente' si no "
            "se presentó), el plan de evaluación de los lapsos que lo "
            "tienen (cada evaluación con su porcentaje, fecha y nota, y los puntos acumulados), "
            "el promedio actual de cada materia, su "
            "situación (aprobada, reprobada, en curso, asegurada o imposible y la nota mínima "
            "necesaria en los lapsos sin nota), el promedio general y las reglas de evaluación.",
            args=NoArgs,
            handler=resumen_de_notas,
        ),
        StudentTool(
            name="notas_de_materia",
            description="Devuelve las notas por lapso, el plan de evaluación (evaluaciones con "
            "porcentaje, fecha y nota), el promedio actual, la situación, la sección y el "
            "profesor de una materia concreta del estudiante, buscándola por nombre o código.",
            args=SubjectArgs,
            handler=notas_de_materia,
        ),
    )
}
