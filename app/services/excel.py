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

# Defensa ante archivos manipulados. Un .xlsx es un zip: uno pequeño puede descomprimirse en
# gigas (zip bomb) o declarar millones de filas y columnas vacías que openpyxl recorre igual.
# Solo se leen las primeras filas y columnas de cada hoja, y el zip se revisa antes de abrirlo.
MAX_SCAN_ROWS = 5000
MAX_SCAN_COLUMNS = 60
MAX_UNCOMPRESSED_BYTES = 60 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_ZIP_ENTRIES = 2000

_INVALID_FILE = "El archivo no es un Excel válido (.xlsx). Si es .xls, guárdalo como .xlsx"


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


def _check_zip(content: bytes) -> None:
    """Rechaza zips que no son un .xlsx o que al descomprimirse ocuparían demasiado."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise InvalidSpreadsheetError(_INVALID_FILE) from exc
    if len(entries) > MAX_ZIP_ENTRIES:
        raise InvalidSpreadsheetError(_INVALID_FILE)
    total = 0
    for entry in entries:
        total += entry.file_size
        ratio = entry.file_size / max(entry.compress_size, 1)
        if total > MAX_UNCOMPRESSED_BYTES or (
            entry.file_size > 1024 * 1024 and ratio > MAX_COMPRESSION_RATIO
        ):
            raise InvalidSpreadsheetError(
                "El archivo es demasiado grande una vez descomprimido. Copia solo los datos "
                "a un libro nuevo y vuelve a intentarlo."
            )


def read_workbook(content: bytes) -> list[Sheet]:
    """Lee todas las hojas visibles. Las filas y columnas vacías se descartan."""
    _check_zip(content)
    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl lanza de todo ante un archivo dañado
        raise InvalidSpreadsheetError(_INVALID_FILE) from exc

    sheets: list[Sheet] = []
    total_rows = 0
    try:
        for ws in workbook.worksheets:
            if ws.sheet_state != "visible":
                continue
            rows = []
            scan = ws.iter_rows(values_only=True, max_row=MAX_SCAN_ROWS, max_col=MAX_SCAN_COLUMNS)
            for number, values in enumerate(scan, start=1):
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
    except InvalidSpreadsheetError:
        raise
    except Exception as exc:  # XML dañado dentro del zip
        raise InvalidSpreadsheetError(_INVALID_FILE) from exc
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
