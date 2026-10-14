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

from app.core.config import get_settings
from app.models.enrollment import MAX_GRADE, MIN_GRADE, TERMS_PER_SEMESTER
from app.services import student_grades
from app.services.student_grades import SubjectGrades


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _subject_dict(item: SubjectGrades) -> dict[str, Any]:
    return {
        "codigo": item.subject.code,
        "materia": item.subject.name,
        "semestre": item.subject.semester.name,
        "profesores": item.teachers,
        "notas_por_lapso": {f"lapso_{term}": _number(value) for term, value in item.grades.items()},
        "promedio": _number(item.average),
    }


def _scale() -> dict[str, Any]:
    return {
        "nota_minima": MIN_GRADE,
        "nota_maxima": MAX_GRADE,
        "nota_aprobatoria": float(get_settings().passing_grade),
        "lapsos_por_semestre": TERMS_PER_SEMESTER,
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
    subjects = await student_grades.student_subjects(db, student_id)
    return {
        "escala": _scale(),
        "materias": [_subject_dict(s) for s in subjects],
        "promedio_general": _number(student_grades.overall_average(subjects)),
    }


async def notas_de_materia(db: AsyncSession, student_id: int, args: SubjectArgs) -> dict[str, Any]:
    subjects = await student_grades.student_subjects(db, student_id)
    query = _normalize(args.materia)
    matches = [
        s
        for s in subjects
        if query in (_normalize(s.subject.code), _normalize(s.subject.name))
        or query in _normalize(s.subject.name)
        or query in _normalize(s.subject.code)
    ]
    if len(matches) == 1:
        return {"escala": _scale(), **_subject_dict(matches[0])}
    return {
        "error": "No se encontró una única materia con ese nombre"
        if not matches
        else "Hay varias materias que coinciden; pide al estudiante que precise",
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
            description="Devuelve todas las materias del estudiante con su nota de cada "
            "lapso (null si aún no se ha cargado), el promedio de cada materia, el promedio "
            "general y la escala de notas (incluida la nota aprobatoria).",
            args=NoArgs,
            handler=resumen_de_notas,
        ),
        StudentTool(
            name="notas_de_materia",
            description="Devuelve las notas por lapso, el promedio y los profesores de una "
            "materia concreta del estudiante, buscándola por nombre o código.",
            args=SubjectArgs,
            handler=notas_de_materia,
        ),
    )
}
