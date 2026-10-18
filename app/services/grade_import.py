"""Carga masiva de notas desde Excel: la IA extrae, el servidor valida y cruza.

La IA solo lee la planilla. A qué estudiante corresponde cada nota lo decide este módulo
comparando con los matriculados de la materia, de modo que el modelo no puede asignar notas
a quien no corresponde. Nada se guarda aquí: el profesor revisa la previsualización y la
confirma con el endpoint normal de carga de notas.
"""

import asyncio
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import LLMClient
from app.ai.prompts import grades_extraction_system, grades_extraction_user
from app.models import Subject, User
from app.schemas.academic import SubjectRead
from app.schemas.grades import ImportPreview, ImportRow, ImportSummary, StudentSummary
from app.services import excel, grades
from app.services import rules as rules_service
from app.services.rules import GradingRules

Status = Literal["ok", "warning", "error"]

_CEDULA_RE = re.compile(r"^\s*([A-Z]{1,3})?[\s\-.]*([\d.\s]+)\s*$", re.IGNORECASE)


# --- Respuesta esperada del modelo -------------------------------------------


class ExtractedGrade(BaseModel):
    sheet: str | None = None
    row: int | None = None
    cedula: str | int | None = None
    student_name: str | None = None
    term: int | str | None = None
    value: float | int | str | None = None
    note: str | None = None


class Extraction(BaseModel):
    grades: list[ExtractedGrade]
    warnings: list[str] = []


# --- Resultado de la previsualización ----------------------------------------


@dataclass
class PreviewRow:
    extracted: ExtractedGrade
    student: User | None = None
    term: int | None = None
    value: Decimal | None = None
    current_value: Decimal | None = None
    issues: list[str] = field(default_factory=list)
    status: Status = "ok"

    def flag(self, status: Status, issue: str) -> None:
        self.issues.append(issue)
        if status == "error" or self.status == "ok":
            self.status = status


@dataclass
class Preview:
    rows: list[PreviewRow]
    warnings: list[str]
    students_without_grades: list[User]


# --- Normalización y cruce ---------------------------------------------------


def normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def parse_cedula(raw: str | int | None) -> tuple[str | None, int] | None:
    if raw is None:
        return None
    match = _CEDULA_RE.match(str(raw))
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(2))
    if not digits:
        return None
    letter = match.group(1).upper() if match.group(1) else None
    return letter, int(digits)


def parse_term(raw: int | str | None, rules: GradingRules) -> int | None:
    """Acepta 2, "2" o "Lapso 2"; solo si es un lapso válido."""
    if raw is None:
        return None
    digits = re.findall(r"\d+", str(raw))
    if len(digits) != 1:
        return None
    term = int(digits[0])
    return term if rules.term_error(term) is None else None


def parse_value(raw: float | int | str | None, rules: GradingRules) -> tuple[Decimal | None, bool]:
    """Convierte la nota a Decimal (acepta 15, "15,5", 15.75...).

    Devuelve (nota, redondeada): si trae más decimales de los admitidos se redondea
    (la mitad hacia arriba) y se avisa. Fuera de la escala o no numérica devuelve None.
    """
    if raw is None:
        return None, False
    try:
        number = Decimal(str(raw).strip().replace(",", "."))
    except InvalidOperation:
        return None, False
    if not number.is_finite():
        return None, False
    rounded = rules.quantize(number)
    if not rules.min_grade <= rounded <= rules.max_grade:
        return None, False
    return rounded, rounded != number


class _Roster:
    def __init__(self, rows: list[grades.StudentRow]):
        self.rows = rows
        self.by_number: dict[int, list[User]] = {}
        self.by_name: dict[str, list[User]] = {}
        for row in rows:
            student = row.student
            if student.cedula is not None:
                self.by_number.setdefault(student.cedula, []).append(student)
            # En las planillas el nombre puede venir como "Nombre Apellido" o "Apellido Nombre"
            for name in (
                f"{student.first_name} {student.last_name}",
                f"{student.last_name} {student.first_name}",
            ):
                students = self.by_name.setdefault(normalize_name(name), [])
                if student not in students:
                    students.append(student)

    def current_value(self, student: User, term: int) -> int | None:
        row = next(r for r in self.rows if r.student.id == student.id)
        return row.grades.get(term)

    def match(self, row: PreviewRow) -> None:
        cedula = parse_cedula(row.extracted.cedula)
        if cedula:
            letter, number = cedula
            candidates = [
                s
                for s in self.by_number.get(number, [])
                if letter is None or s.nationality == letter
            ]
            if len(candidates) == 1:
                row.student = candidates[0]
                return
            if len(candidates) > 1:
                types = " o ".join(sorted({s.nationality for s in candidates}))
                row.flag("error", f"La cédula {number} es ambigua: indica si es {types}")
                return
            row.flag("error", f"La cédula {row.extracted.cedula} no está matriculada en la materia")
            return

        if row.extracted.student_name:
            name = normalize_name(row.extracted.student_name)
            exact = self.by_name.get(name, [])
            tokens = set(name.split())
            partial = [
                s
                for key, students in self.by_name.items()
                if tokens and (tokens <= set(key.split()) or set(key.split()) <= tokens)
                for s in students
            ]
            candidates = exact or partial
            if len(candidates) == 1:
                row.student = candidates[0]
                row.flag("warning", "Identificado solo por el nombre: verifica que sea correcto")
                return
            if len(candidates) > 1:
                row.flag("error", f"El nombre «{row.extracted.student_name}» coincide con varios")
                return
            row.flag("error", f"«{row.extracted.student_name}» no está matriculado en la materia")
            return

        row.flag("error", "No se pudo identificar al estudiante (falta cédula y nombre)")


def _validate(row: PreviewRow, roster: _Roster, rules: GradingRules) -> None:
    roster.match(row)

    row.term = parse_term(row.extracted.term, rules)
    if row.term is None:
        row.flag(
            "error",
            f"No se pudo determinar el {rules.term_label.lower()} (1 a {rules.terms_count})",
        )

    row.value, rounded = parse_value(row.extracted.value, rules)
    if row.value is None:
        row.flag(
            "error",
            f"Nota inválida «{row.extracted.value}»: debe ser un número {rules.scale_text}",
        )
    elif rounded:
        row.flag(
            "warning",
            f"Nota «{row.extracted.value}» redondeada a {row.value} "
            f"({rules.grade_decimals} decimales como máximo)",
        )

    if row.extracted.note:
        row.issues.append(f"Observación de la IA: {row.extracted.note}")

    if row.student and row.term:
        row.current_value = roster.current_value(row.student, row.term)


def _flag_duplicates(rows: list[PreviewRow], rules: GradingRules) -> None:
    seen: dict[tuple[int, int], list[PreviewRow]] = {}
    for row in rows:
        if row.student and row.term:
            seen.setdefault((row.student.id, row.term), []).append(row)
    for dupes in seen.values():
        if len(dupes) > 1:
            where = ", ".join(f"fila {r.extracted.row}" for r in dupes)
            for row in dupes:
                row.flag(
                    "error",
                    f"Nota repetida para el mismo estudiante y {rules.term_label.lower()} "
                    f"({where})",
                )


async def preview_import(
    db: AsyncSession,
    llm: LLMClient,
    subject: Subject,
    content: bytes,
    default_term: int | None,
) -> Preview:
    # Leer el Excel consume CPU: en un hilo aparte para no frenar el event loop
    sheets = await asyncio.to_thread(excel.read_workbook, content)
    rules = rules_service.for_semester(await rules_service.get_rules(db), subject.semester)
    extraction = await llm.complete_json(
        grades_extraction_system(rules),
        grades_extraction_user(
            f"{subject.code} - {subject.name}", default_term, excel.sheets_to_text(sheets), rules
        ),
        Extraction,
    )

    roster = _Roster(await grades.subject_roster(db, subject.id, rules))
    rows = []
    for extracted in extraction.grades:
        if extracted.term is None and default_term:
            extracted.term = default_term
        row = PreviewRow(extracted=extracted)
        _validate(row, roster, rules)
        rows.append(row)
    _flag_duplicates(rows, rules)

    found = {row.student.id for row in rows if row.student}
    missing = [r.student for r in roster.rows if r.student.id not in found]
    return Preview(rows=rows, warnings=extraction.warnings, students_without_grades=missing)


def to_schema(subject: Subject, preview: Preview) -> ImportPreview:
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
