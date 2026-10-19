from decimal import Decimal

import pytest
from sqlalchemy import select

from app.ai.client import LLMNotConfiguredError
from app.core.config import get_settings
from app.models import Grade, Role
from app.services.grade_import import (
    Extraction,
    normalize_name,
    parse_cedula,
    parse_term,
    parse_value,
)
from app.services.import_jobs import claim_next
from app.services.rules import DEFAULT_RULES
from tests.import_helpers import FakeLLM, entry, process_queue, upload

# --- Parsers -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("V-12.345.678", ("V", 12345678)),
        ("e 1234", ("E", 1234)),
        ("12345678", (None, 12345678)),
        (12345678, (None, 12345678)),
        ("Ana", None),
        (None, None),
    ],
)
def test_parse_cedula(raw, expected):
    assert parse_cedula(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (15, (Decimal("15"), False)),
        ("15", (Decimal("15"), False)),
        (15.0, (Decimal("15"), False)),
        ("15,5", (Decimal("15.5"), False)),
        (15.75, (Decimal("15.75"), False)),
        (15.555, (Decimal("15.56"), True)),
        ("15.3333", (Decimal("15.33"), True)),
        (20.004, (Decimal("20.00"), True)),
        (20.005, (None, False)),
        (0.5, (None, False)),
        (21, (None, False)),
        ("NP", (None, False)),
        ("nan", (None, False)),
        (None, (None, False)),
    ],
)
def test_parse_value(raw, expected):
    assert parse_value(raw, DEFAULT_RULES) == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [(2, 2), ("2", 2), ("Lapso 3", 3), (4, None), ("1 y 2", None)]
)
def test_parse_term(raw, expected):
    assert parse_term(raw, DEFAULT_RULES) == expected


def test_normalize_name():
    assert normalize_name("  José   PÉREZ-Gómez ") == "jose perez gomez"


def test_extraction_tolera_tipos():
    parsed = Extraction.model_validate(
        {"grades": [{"cedula": 123, "term": "Lapso 1", "value": "15", "row": "4"}]}
    )
    assert parsed.grades[0].row == 4


# --- Endpoint + cola ---------------------------------------------------------


@pytest.fixture
def use_llm():
    return FakeLLM


async def run_import(client, course, sessionmaker, llm, **upload_args) -> dict:
    """Sube el archivo, deja que el worker procese la cola y devuelve el trabajo."""
    response = await upload(client, course, **upload_args)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "pending"
    await process_queue(sessionmaker, llm)
    job = await client.get(
        f"/teacher/import-jobs/{response.json()['id']}", headers=course["headers"]
    )
    return job.json()


async def preview_rows(client, course, sessionmaker, llm, **upload_args) -> list[dict]:
    job = await run_import(client, course, sessionmaker, llm, **upload_args)
    assert job["status"] == "done", job
    return job["result"]["rows"]


async def test_previsualizacion_completa(client, course, use_llm, session, sessionmaker):
    # Ana ya tiene un 10 en el lapso 1: debe mostrarse como nota actual
    ana = course["students"]["Ana Pérez"]
    await client.put(
        f"/teacher/subjects/{course['subject_id']}/grades",
        json={"grades": [{"student_id": ana, "term": 1, "value": 10}]},
        headers=course["headers"],
    )
    fake = use_llm(
        {
            "grades": [
                entry(cedula="V-1", student_name="Ana Pérez", term=1, value=15),
                entry(row=3, student_name="carlos ruiz", term="Lapso 2", value="12"),
                entry(row=4, cedula="2", student_name="B", term=1, value=14),
                entry(row=5, cedula="V-99", student_name="Nadie", term=1, value=11),
                entry(row=6, cedula="V-1", term=2, value="quince"),
                entry(row=7, cedula="V-2", term=None, value=13, note="sin columna de lapso"),
            ],
            "warnings": ["La fila 8 parece un total y se ignoró"],
        }
    )

    job = await run_import(client, course, sessionmaker, fake)
    assert job["status"] == "done", job
    body = job["result"]
    rows = body["rows"]

    assert rows[0]["status"] == "ok"
    assert rows[0]["student"]["full_name"] == "Ana Pérez"
    assert (rows[0]["term"], rows[0]["value"], rows[0]["current_value"]) == (1, 15, 10)

    assert rows[1]["status"] == "warning"
    assert rows[1]["student"]["full_name"] == "Carlos Ruiz"
    assert (rows[1]["term"], rows[1]["value"]) == (2, 12)

    assert rows[2]["status"] == "error"
    assert "ambigua" in rows[2]["issues"][0]

    assert rows[3]["status"] == "error"
    assert "no está matriculada" in rows[3]["issues"][0]

    assert rows[4]["status"] == "error"
    assert rows[4]["value"] is None
    assert "quince" in rows[4]["issues"][0]

    assert rows[5]["status"] == "error"
    assert any("lapso" in i for i in rows[5]["issues"])
    assert any("sin columna de lapso" in i for i in rows[5]["issues"])

    assert body["summary"] == {"ok": 1, "warning": 1, "error": 4}
    assert body["warnings"] == ["La fila 8 parece un total y se ignoró"]
    assert [s["full_name"] for s in body["students_without_grades"]] == ["Bob Smith"]

    # El prompt lleva la planilla con números de fila, pero no la lista de matriculados
    _, user_prompt = fake.calls[0]
    assert "Fila 2: V-1 | Ana Pérez | 15 | 12" in user_prompt
    assert "Bob Smith" not in user_prompt
    assert "MAT-101" in user_prompt

    # Previsualizar no guarda nada
    stored = (await session.scalars(select(Grade))).all()
    assert [(g.term, g.value) for g in stored] == [(1, 10)]


async def test_notas_con_decimales(client, course, use_llm, sessionmaker):
    fake = use_llm(
        {
            "grades": [
                entry(row=2, cedula="V-1", term=1, value="15,75"),
                entry(row=3, cedula="V-3", term=1, value=12.3333),
            ]
        }
    )
    rows = await preview_rows(client, course, sessionmaker, fake)
    assert (rows[0]["status"], rows[0]["value"]) == ("ok", 15.75)
    assert (rows[1]["status"], rows[1]["value"]) == ("warning", 12.33)
    assert "redondeada a 12.33" in rows[1]["issues"][0]


async def test_lapso_por_defecto(client, course, use_llm, sessionmaker):
    fake = use_llm({"grades": [entry(cedula="V-1", term=None, value=15)]})
    rows = await preview_rows(client, course, sessionmaker, fake, default_term=3)
    assert rows[0]["term"] == 3
    assert rows[0]["status"] == "ok"
    assert "lapso 3" in fake.calls[0][1]


async def test_duplicados_en_el_archivo(client, course, use_llm, sessionmaker):
    fake = use_llm(
        {
            "grades": [
                entry(row=2, cedula="V-1", term=1, value=15),
                entry(row=9, cedula="1", term=1, value=16),
            ]
        }
    )
    rows = await preview_rows(client, course, sessionmaker, fake)
    assert [r["status"] for r in rows] == ["error", "error"]
    assert "fila 2, fila 9" in rows[0]["issues"][0]


async def test_confirmar_con_el_endpoint_de_notas(client, course, use_llm, session, sessionmaker):
    fake = use_llm(
        {"grades": [entry(cedula="V-1", term=1, value=15), entry(cedula="V-3", term=1, value=9)]}
    )
    rows = await preview_rows(client, course, sessionmaker, fake)
    accepted = [
        {"student_id": r["student"]["id"], "term": r["term"], "value": r["value"]}
        for r in rows
        if r["status"] != "error"
    ]
    saved = await client.put(
        f"/teacher/subjects/{course['subject_id']}/grades",
        json={"grades": accepted},
        headers=course["headers"],
    )
    assert saved.status_code == 200
    assert len((await session.scalars(select(Grade))).all()) == 2


async def test_archivo_invalido_se_rechaza_sin_encolar(client, course, sessionmaker):
    response = await upload(client, course, content=b"no soy un excel")
    assert response.status_code == 400
    async with sessionmaker() as db:
        assert await claim_next(db) is None


async def test_archivo_demasiado_grande(client, course, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_size_mb", 0)
    assert (await upload(client, course)).status_code == 413


async def test_excel_corrupto_falla_en_el_worker(client, course, use_llm, sessionmaker):
    fake = use_llm({"grades": []})
    job = await run_import(client, course, sessionmaker, fake, content=b"PK no es un zip")
    assert job["status"] == "failed"
    assert "no es un Excel válido" in job["error"]
    assert fake.calls == []


async def test_ia_no_configurada(client, course, use_llm, sessionmaker):
    job = await run_import(client, course, sessionmaker, use_llm(LLMNotConfiguredError()))
    assert job["status"] == "failed"
    assert "LLM_API_KEY" in job["error"]


async def test_error_inesperado(client, course, use_llm, sessionmaker):
    job = await run_import(client, course, sessionmaker, use_llm(RuntimeError("boom")))
    assert job["status"] == "failed"
    assert job["error"] == "Error inesperado procesando el archivo"


async def test_materia_ajena(client, course, login_as):
    other = await login_as(Role.TEACHER, email="otro@uni.edu")
    assert (await upload(client, course, headers=other)).status_code == 404


def test_identifica_por_nombre_en_cualquier_orden():
    """En las planillas el nombre puede venir como "Apellido Nombre"."""
    from app.models import Nationality, Role, User
    from app.services.grade_import import ExtractedGrade, PreviewRow, _Roster
    from app.services.grades import student_row

    ana = User(
        id=1,
        role=Role.STUDENT,
        first_name="Ana María",
        last_name="Pérez",
        nationality=Nationality.VENEZOLANO,
        cedula=1,
    )
    roster = _Roster([student_row(ana, {1: None, 2: None, 3: None}, DEFAULT_RULES)])
    for name in ("Pérez Ana María", "ana maria perez"):
        row = PreviewRow(extracted=ExtractedGrade(student_name=name))
        roster.match(row)
        assert row.student is ana, name


async def test_no_importa_lapsos_con_plan_de_evaluacion(client, course, use_llm, sessionmaker):
    await client.post(
        f"/teacher/subjects/{course['subject_id']}/evaluations",
        json={"term": 2, "name": "Examen", "weight": 100},
        headers=course["headers"],
    )
    fake = use_llm(
        {
            "grades": [
                entry(cedula="V-1", term=1, value=15),
                entry(row=3, cedula="V-2", term=2, value=12),
            ]
        }
    )
    rows = await preview_rows(client, course, sessionmaker, fake)
    assert [r["status"] for r in rows] == ["ok", "error"]
    assert "plan de evaluación" in rows[1]["issues"][0]
