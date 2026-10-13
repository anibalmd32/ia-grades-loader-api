"""Carga masiva de notas desde Excel: la IA extrae, el servidor valida y cruza.

La IA solo lee la planilla. A qué estudiante corresponde cada nota lo decide este módulo
comparando con los matriculados de la materia, de modo que el modelo no puede asignar notas
a quien no corresponde. Nada se guarda aquí: el profesor revisa la previsualización y la
confirma con el endpoint normal de carga de notas.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import LLMClient
from app.ai.prompts import GRADES_EXTRACTION_SYSTEM, grades_extraction_user
from app.models import Subject, User
from app.models.enrollment import MAX_GRADE, MIN_GRADE, TERMS_PER_SEMESTER
from app.services import excel, grades

Status = Literal["ok", "warning", "error"]

_CEDULA_RE = re.compile(r"^\s*([VE])?[\s\-.]*([\d.\s]+)\s*$", re.IGNORECASE)


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
    value: int | None = None
    current_value: int | None = None
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


def parse_term(raw: int | str | None) -> int | None:
    """Acepta 2, "2" o "Lapso 2"; solo si es un lapso válido."""
    if raw is None:
        return None
    digits = re.findall(r"\d+", str(raw))
    if len(digits) != 1:
        return None
    term = int(digits[0])
    return term if 1 <= term <= TERMS_PER_SEMESTER else None


def parse_value(raw: float | int | str | None) -> int | None:
    """Solo acepta enteros del 1 al 20 (también "15", "15,0" o 15.0)."""
    if raw is None:
        return None
    try:
        number = float(str(raw).strip().replace(",", "."))
    except ValueError:
        return None
    if not number.is_integer() or not MIN_GRADE <= number <= MAX_GRADE:
        return None
    return int(number)


class _Roster:
    def __init__(self, rows: list[grades.StudentRow]):
        self.rows = rows
        self.by_number: dict[int, list[User]] = {}
        self.by_name: dict[str, list[User]] = {}
        for row in rows:
            student = row.student
            if student.cedula is not None:
                self.by_number.setdefault(student.cedula, []).append(student)
            self.by_name.setdefault(normalize_name(student.full_name), []).append(student)

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
                if letter is None or s.nationality.value == letter
            ]
            if len(candidates) == 1:
                row.student = candidates[0]
                return
            if len(candidates) > 1:
                row.flag("error", f"La cédula {number} es ambigua: indica si es V o E")
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


def _validate(row: PreviewRow, roster: _Roster) -> None:
    roster.match(row)

    row.term = parse_term(row.extracted.term)
    if row.term is None:
        row.flag("error", "No se pudo determinar el lapso (1 a 3)")

    row.value = parse_value(row.extracted.value)
    if row.value is None:
        row.flag(
            "error",
            f"Nota inválida «{row.extracted.value}»: debe ser un entero del {MIN_GRADE} al "
            f"{MAX_GRADE}",
        )

    if row.extracted.note:
        row.issues.append(f"Observación de la IA: {row.extracted.note}")

    if row.student and row.term:
        row.current_value = roster.current_value(row.student, row.term)


def _flag_duplicates(rows: list[PreviewRow]) -> None:
    seen: dict[tuple[int, int], list[PreviewRow]] = {}
    for row in rows:
        if row.student and row.term:
            seen.setdefault((row.student.id, row.term), []).append(row)
    for dupes in seen.values():
        if len(dupes) > 1:
            where = ", ".join(f"fila {r.extracted.row}" for r in dupes)
            for row in dupes:
                row.flag("error", f"Nota repetida para el mismo estudiante y lapso ({where})")


async def preview_import(
    db: AsyncSession,
    llm: LLMClient,
    subject: Subject,
    content: bytes,
    default_term: int | None,
) -> Preview:
    sheets = excel.read_workbook(content)
    extraction = await llm.complete_json(
        GRADES_EXTRACTION_SYSTEM,
        grades_extraction_user(
            f"{subject.code} - {subject.name}", default_term, excel.sheets_to_text(sheets)
        ),
        Extraction,
    )

    roster = _Roster(await grades.subject_roster(db, subject.id))
    rows = []
    for extracted in extraction.grades:
        if extracted.term is None and default_term:
            extracted.term = default_term
        row = PreviewRow(extracted=extracted)
        _validate(row, roster)
        rows.append(row)
    _flag_duplicates(rows)

    found = {row.student.id for row in rows if row.student}
    missing = [r.student for r in roster.rows if r.student.id not in found]
    return Preview(rows=rows, warnings=extraction.warnings, students_without_grades=missing)
