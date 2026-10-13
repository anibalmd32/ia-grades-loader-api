import io

import pytest
from openpyxl import Workbook

from app.services.excel import MAX_ROWS, InvalidSpreadsheetError, read_workbook, sheets_to_text


def xlsx(*sheets: tuple[str, list[list]], hidden: tuple[str, ...] = ()) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
        if title in hidden:
            ws.sheet_state = "hidden"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def test_lee_hojas_y_descarta_vacios():
    content = xlsx(
        (
            "Notas",
            [
                ["PLANILLA MATEMÁTICA I", None, None],
                [],
                ["Cédula", "Nombre", "Lapso 1", "Lapso 2", None],
                ["V-12.345.678", "Ana  Pérez", 15.0, 17.5, None],
            ],
        ),
        ("Vacía", []),
        (
            "Oculta",
            [["no", "debe", "leerse"]],
        ),
        hidden=("Oculta",),
    )
    sheets = read_workbook(content)
    assert [s.name for s in sheets] == ["Notas"]
    assert sheets[0].rows == [
        (1, ["PLANILLA MATEMÁTICA I"]),
        (3, ["Cédula", "Nombre", "Lapso 1", "Lapso 2"]),
        (4, ["V-12.345.678", "Ana Pérez", "15", "17.5"]),
    ]
    assert sheets_to_text(sheets).splitlines() == [
        "### Hoja: Notas",
        "Fila 1: PLANILLA MATEMÁTICA I",
        "Fila 3: Cédula | Nombre | Lapso 1 | Lapso 2",
        "Fila 4: V-12.345.678 | Ana Pérez | 15 | 17.5",
    ]


def test_archivo_no_excel():
    with pytest.raises(InvalidSpreadsheetError):
        read_workbook(b"esto no es un excel")


def test_excel_sin_datos():
    with pytest.raises(InvalidSpreadsheetError, match="no tiene datos"):
        read_workbook(xlsx(("Hoja1", [])))


def test_demasiadas_filas():
    rows = [[f"V-{i}", i % 20 + 1] for i in range(MAX_ROWS + 1)]
    with pytest.raises(InvalidSpreadsheetError, match="demasiadas filas"):
        read_workbook(xlsx(("Hoja1", rows)))
