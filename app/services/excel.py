"""Lectura de planillas Excel (.xlsx) y conversión a texto para la IA."""

import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime

from openpyxl import load_workbook

from app.core.errors import AppError

# Límite de filas con contenido: acota el tamaño del prompt (y el coste) de la IA
MAX_ROWS = 400
MAX_CELL_CHARS = 80


class InvalidSpreadsheetError(AppError):
    status_code = 400


@dataclass
class Sheet:
    name: str
    rows: list[tuple[int, list[str]]]  # (número de fila en Excel, celdas como texto)


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, datetime | date):
        value = value.isoformat()
    text = " ".join(str(value).split())
    return text[:MAX_CELL_CHARS]


def read_workbook(content: bytes) -> list[Sheet]:
    """Lee todas las hojas visibles. Las filas y columnas vacías se descartan."""
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, ValueError, OSError) as exc:
        raise InvalidSpreadsheetError(
            "El archivo no es un Excel válido (.xlsx). Si es .xls, guárdalo como .xlsx"
        ) from exc

    sheets: list[Sheet] = []
    total_rows = 0
    try:
        for ws in workbook.worksheets:
            if ws.sheet_state != "visible":
                continue
            rows = []
            for number, values in enumerate(ws.iter_rows(values_only=True), start=1):
                cells = [_cell_text(v) for v in values]
                while cells and not cells[-1]:
                    cells.pop()
                if any(cells):
                    rows.append((number, cells))
            if rows:
                sheets.append(Sheet(ws.title, rows))
                total_rows += len(rows)
            if total_rows > MAX_ROWS:
                raise InvalidSpreadsheetError(
                    f"El archivo tiene demasiadas filas (máximo {MAX_ROWS} con contenido)"
                )
    finally:
        workbook.close()

    if not sheets:
        raise InvalidSpreadsheetError("El archivo no tiene datos")
    return sheets


def sheets_to_text(sheets: list[Sheet]) -> str:
    """Representación compacta: una línea por fila con su número real de Excel."""
    parts = []
    for sheet in sheets:
        parts.append(f"### Hoja: {sheet.name}")
        parts.extend(f"Fila {n}: " + " | ".join(cells) for n, cells in sheet.rows)
    return "\n".join(parts)
