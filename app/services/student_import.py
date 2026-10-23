"""Alta masiva de estudiantes desde un Excel (sin IA: columnas reconocidas por su encabezado).

Flujo en dos pasos con el mismo archivo:
1. dry_run: se valida cada fila y se devuelve qué pasaría (nuevo, ya existe o error).
2. confirmación: se crean los estudiantes nuevos (con contraseña generada, que solo se
   muestra en esa respuesta), se les asigna la sección indicada en el periodo de sus materias
   y se matriculan. Las filas con errores se omiten; los que ya existían solo se matriculan.

Para matricular hace falta sección en el periodo de la materia: la columna «Sección» la
asigna (si el estudiante ya tiene sección en ese periodo puede omitirse).
"""

import asyncio
import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import generate_password, hash_password
from app.models import Enrollment, Role, Section, StudentSection, Subject, User
from app.services import excel
from app.services import rules as rules_service
from app.services.grade_import import parse_cedula
from app.services.rules import GradingRules

Status = Literal["new", "existing", "error"]

# Encabezados reconocidos (normalizados: minúsculas, sin acentos ni signos)
HEADERS: dict[str, set[str]] = {
    "first_name": {"nombre", "nombres", "primer nombre"},
    "last_name": {"apellido", "apellidos"},
    "full_name": {
        "nombre completo",
        "nombre y apellido",
        "nombres y apellidos",
        "apellidos y nombres",
        "estudiante",
        "alumno",
    },
    "nationality": {"tipo", "nacionalidad", "tipo de documento", "tipo documento", "v e"},
    "cedula": {
        "cedula",
        "ci",
        "c i",
        "documento",
        "cedula de identidad",
        "numero de cedula",
        "n de cedula",
        "dni",
        "pasaporte",
        "numero de documento",
        "documento de identidad",
    },
    "subjects": {"materias", "materia", "codigos", "codigo", "materias codigos"},
    "section": {"seccion", "secc", "sec"},
}
TEMPLATE_HEADERS = ["Nombre", "Apellido", "Tipo", "Cédula", "Materias", "Sección"]


class InvalidStudentFileError(AppError):
    status_code = 400


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


@dataclass
class StudentRow:
    row: int
    first_name: str = ""
    last_name: str = ""
    nationality: str | None = None
    cedula: int | None = None
    subject_codes: list[str] = field(default_factory=list)
    section: str | None = None
    # periodo -> sección que hay que asignarle (no tenía sección en ese periodo)
    new_sections: dict[int, int] = field(default_factory=dict)
    status: Status = "new"
    issues: list[str] = field(default_factory=list)
    user: User | None = None
    generated_password: str | None = None

    def error(self, issue: str) -> None:
        self.status = "error"
        self.issues.append(issue)


def _find_columns(sheet: excel.Sheet) -> tuple[int, dict[str, int]]:
    """Fila de encabezados (entre las 10 primeras) y la columna de cada dato."""
    for index, (_, cells) in enumerate(sheet.rows[:10]):
        columns: dict[str, int] = {}
        for position, cell in enumerate(cells):
            name = _normalize(cell)
            for key, options in HEADERS.items():
                if name in options and key not in columns:
                    columns[key] = position
        has_name = "full_name" in columns or {"first_name", "last_name"} <= columns.keys()
        if has_name and "cedula" in columns:
            return index, columns
    raise InvalidStudentFileError(
        "No se encontraron los encabezados. La primera fila debe tener al menos las columnas "
        "Nombre, Apellido y Cédula (descarga la plantilla para ver el formato)."
    )


def _split_full_name(text: str) -> tuple[str, str]:
    """ "Apellido, Nombre" o "Nombre Apellido" (mitad y mitad)."""
    if "," in text:
        last, _, first = text.partition(",")
        return first.strip(), last.strip()
    words = text.split()
    half = max(1, len(words) // 2)
    return " ".join(words[:half]), " ".join(words[half:])


def parse(content: bytes, rules: GradingRules) -> list[StudentRow]:
    sheets = excel.read_workbook(content)
    sheet = sheets[0]
    header_index, columns = _find_columns(sheet)

    def cell(cells: list[str], key: str) -> str:
        position = columns.get(key)
        return cells[position].strip() if position is not None and position < len(cells) else ""

    rows = []
    for number, cells in sheet.rows[header_index + 1 :]:
        row = StudentRow(row=number)
        if "full_name" in columns and not cell(cells, "first_name"):
            row.first_name, row.last_name = _split_full_name(cell(cells, "full_name"))
        else:
            row.first_name, row.last_name = cell(cells, "first_name"), cell(cells, "last_name")

        raw_cedula = cell(cells, "cedula")
        parsed = parse_cedula(raw_cedula)
        if parsed is None or not 0 < parsed[1] <= 999_999_999:
            row.error(f"Cédula inválida «{raw_cedula}»" if raw_cedula else "Falta la cédula")
        else:
            letter, row.cedula = parsed
            raw_type = cell(cells, "nationality").upper() or letter or rules.document_codes[0]
            row.nationality = raw_type
            if letter and raw_type != letter:
                row.error(f"El tipo «{raw_type}» no coincide con la cédula «{raw_cedula}»")
            elif problem := rules.document_type_error(raw_type):
                row.error(problem)

        if not row.first_name:
            row.error("Falta el nombre")
        if not row.last_name:
            row.error("Falta el apellido")
        if len(row.first_name) > 100 or len(row.last_name) > 100:
            row.error("Nombre o apellido demasiado largo (máximo 100 caracteres)")
        row.subject_codes = sorted(
            {c.upper() for c in re.split(r"[,;/\s]+", cell(cells, "subjects")) if c}
        )
        row.section = cell(cells, "section").upper() or None
        rows.append(row)
    if not rows:
        raise InvalidStudentFileError("El archivo no tiene estudiantes debajo de los encabezados")
    return rows


async def _validate(db: AsyncSession, rows: list[StudentRow]) -> dict[str, Subject]:
    seen: dict[tuple[str, int], int] = {}
    for row in rows:
        if row.cedula is None or row.nationality is None:
            continue
        key = (row.nationality, row.cedula)
        if key in seen:
            row.error(f"Cédula repetida en el archivo (fila {seen[key]})")
        else:
            seen[key] = row.row

    existing = {
        (u.nationality, u.cedula): u
        for u in await db.scalars(
            select(User).where(tuple_(User.nationality, User.cedula).in_(list(seen)))
        )
    }
    codes = {code for row in rows for code in row.subject_codes}
    subjects = {s.code: s for s in await db.scalars(select(Subject).where(Subject.code.in_(codes)))}
    for row in rows:
        user = existing.get((row.nationality, row.cedula))
        if user is not None and row.status != "error":
            if user.role != Role.STUDENT:
                row.error("Esa cédula pertenece a una cuenta que no es de estudiante")
            else:
                row.status = "existing"
                row.user = user
                row.issues.append("Ya existe: no se crea, solo se matricula en las materias")
        for code in row.subject_codes:
            subject = subjects.get(code)
            if subject is None:
                row.error(f"La materia {code} no existe")
            elif subject.semester.is_closed:
                row.error(f"La materia {code} es de un periodo cerrado")
    await _validate_sections(db, rows, subjects)
    return subjects


async def _validate_sections(
    db: AsyncSession, rows: list[StudentRow], subjects: dict[str, Subject]
) -> None:
    """Cada estudiante necesita sección en el periodo de sus materias: la que ya tiene o la
    de la columna «Sección» (que debe existir en ese periodo)."""
    semesters = {s.semester_id for s in subjects.values()}
    sections = {
        (s.semester_id, s.name): s
        for s in await db.scalars(select(Section).where(Section.semester_id.in_(semesters)))
    }
    user_ids = [row.user.id for row in rows if row.user is not None]
    current = {
        (a.student_id, a.semester_id): a.section_id
        for a in await db.scalars(
            select(StudentSection).where(StudentSection.student_id.in_(user_ids))
        )
    }
    names = {s.id: s.name for s in sections.values()}
    for row in rows:
        if row.status == "error":
            continue
        for semester in sorted(
            {subjects[c].semester for c in row.subject_codes}, key=lambda s: s.name
        ):
            has = None if row.user is None else current.get((row.user.id, semester.id))
            wanted = None if row.section is None else sections.get((semester.id, row.section))
            if row.section is not None and wanted is None:
                row.error(f"La sección {row.section} no existe en {semester.name}")
            elif has is not None and wanted is not None and has != wanted.id:
                row.error(f"Ya está en la sección {names[has]} de {semester.name}")
            elif has is None and wanted is None:
                row.error(
                    f"Falta la sección (columna Sección) para matricularlo en {semester.name}"
                )
            elif has is None:
                row.new_sections[semester.id] = wanted.id


async def import_students(db: AsyncSession, content: bytes, *, dry_run: bool) -> list[StudentRow]:
    rows = await asyncio.to_thread(parse, content, await rules_service.get_rules(db))
    subjects = await _validate(db, rows)
    if dry_run:
        return rows

    for row in rows:
        if row.status != "new":
            continue
        row.generated_password = generate_password()
        row.user = User(
            role=Role.STUDENT,
            first_name=row.first_name,
            last_name=row.last_name,
            nationality=row.nationality,
            cedula=row.cedula,
            password_hash=hash_password(row.generated_password),
            must_change_password=True,
        )
        db.add(row.user)
    await db.flush()

    db.add_all(
        StudentSection(student_id=row.user.id, semester_id=semester_id, section_id=section_id)
        for row in rows
        if row.status != "error"
        for semester_id, section_id in row.new_sections.items()
    )
    wanted = {
        (row.user.id, subjects[code].id)
        for row in rows
        if row.status != "error"
        for code in row.subject_codes
    }
    if wanted:
        already = set(
            (
                await db.execute(
                    select(Enrollment.student_id, Enrollment.subject_id).where(
                        tuple_(Enrollment.student_id, Enrollment.subject_id).in_(wanted)
                    )
                )
            ).all()
        )
        db.add_all(Enrollment(student_id=s, subject_id=m) for s, m in wanted - already)
    await db.commit()
    return rows


def template() -> bytes:
    """Plantilla .xlsx con los encabezados y dos filas de ejemplo."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Estudiantes"
    sheet.append(TEMPLATE_HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.append(["Ana María", "Pérez Gómez", "V", "12345678", "MAT-101, FIS-101", "A"])
    sheet.append(["John", "Smith", "E", "81234567", "MAT-101", "B"])
    for column, width in zip("ABCDEF", (18, 18, 8, 14, 28, 10), strict=True):
        sheet.column_dimensions[column].width = width
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
