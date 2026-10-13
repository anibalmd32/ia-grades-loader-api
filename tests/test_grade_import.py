import io
from decimal import Decimal

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.ai.client import LLMNotConfiguredError, get_llm
from app.core.config import get_settings
from app.core.security import create_access_token
from app.main import app as fastapi_app
from app.models import Grade, Role, User
from app.services.grade_import import (
    Extraction,
    normalize_name,
    parse_cedula,
    parse_term,
    parse_value,
)

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
    assert parse_value(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"), [(2, 2), ("2", 2), ("Lapso 3", 3), (4, None), ("1 y 2", None)]
)
def test_parse_term(raw, expected):
    assert parse_term(raw) == expected


def test_normalize_name():
    assert normalize_name("  José   PÉREZ-Gómez ") == "jose perez gomez"


def test_extraction_tolera_tipos():
    parsed = Extraction.model_validate(
        {"grades": [{"cedula": 123, "term": "Lapso 1", "value": "15", "row": "4"}]}
    )
    assert parsed.grades[0].row == 4


# --- Endpoint ----------------------------------------------------------------


class FakeLLM:
    def __init__(self, payload: dict | Exception):
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    async def complete_json(self, system, user, schema):
        self.calls.append((system, user))
        if isinstance(self.payload, Exception):
            raise self.payload
        return schema.model_validate(self.payload)


@pytest.fixture
def use_llm():
    def _use(payload) -> FakeLLM:
        fake = FakeLLM(payload)
        fastapi_app.dependency_overrides[get_llm] = lambda: fake
        return fake

    yield _use
    fastapi_app.dependency_overrides.pop(get_llm, None)


def xlsx(rows: list[list]) -> bytes:
    wb = Workbook()
    for row in rows:
        wb.active.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


SAMPLE = xlsx([["Cédula", "Nombre", "L1", "L2"], ["V-1", "Ana Pérez", 15, 12]])


@pytest.fixture
async def course(client, login_as, session):
    coord = await login_as(Role.COORDINATOR, email="coord@uni.edu")
    semester = (await client.post("/semesters", json={"name": "2026-I"}, headers=coord)).json()
    subject = await client.post(
        "/subjects",
        json={"semester_id": semester["id"], "code": "MAT-101", "name": "Matemática I"},
        headers=coord,
    )
    subject_id = subject.json()["id"]
    teacher = await client.post(
        "/teachers", json={"full_name": "Pedro", "email": "p@uni.edu"}, headers=coord
    )
    teacher_id = teacher.json()["user"]["id"]
    await client.put(
        f"/teachers/{teacher_id}/subjects", json={"subject_ids": [subject_id]}, headers=coord
    )
    students = {}
    for nationality, cedula, name in [
        ("V", 1, "Ana Pérez"),
        ("V", 2, "Bea Gómez"),
        ("E", 2, "Bob Smith"),
        ("V", 3, "Carlos Ruiz"),
    ]:
        response = await client.post(
            "/students",
            json={"full_name": name, "nationality": nationality, "cedula": cedula},
            headers=coord,
        )
        students[name] = response.json()["user"]["id"]
    await client.post(
        f"/subjects/{subject_id}/students",
        json={"student_ids": list(students.values())},
        headers=coord,
    )
    user = await session.get(User, teacher_id)
    token, _ = create_access_token(user.id, user.role.value, user.token_version)
    return {
        "subject_id": subject_id,
        "students": students,
        "headers": {"Authorization": f"Bearer {token}"},
    }


async def upload(client, course, content=SAMPLE, default_term=None, headers=None):
    data = {"default_term": str(default_term)} if default_term else {}
    return await client.post(
        f"/teacher/subjects/{course['subject_id']}/grades/import",
        files={"file": ("notas.xlsx", content)},
        data=data,
        headers=headers or course["headers"],
    )


def entry(**fields) -> dict:
    return {"sheet": "Sheet", "row": 2, **fields}


async def test_previsualizacion_completa(client, course, use_llm, session):
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

    response = await upload(client, course)
    assert response.status_code == 200, response.text
    body = response.json()
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


async def test_notas_con_decimales(client, course, use_llm):
    use_llm(
        {
            "grades": [
                entry(row=2, cedula="V-1", term=1, value="15,75"),
                entry(row=3, cedula="V-3", term=1, value=12.3333),
            ]
        }
    )
    rows = (await upload(client, course)).json()["rows"]
    assert (rows[0]["status"], rows[0]["value"]) == ("ok", 15.75)
    assert (rows[1]["status"], rows[1]["value"]) == ("warning", 12.33)
    assert "redondeada a 12.33" in rows[1]["issues"][0]


async def test_lapso_por_defecto(client, course, use_llm):
    fake = use_llm({"grades": [entry(cedula="V-1", term=None, value=15)]})
    body = (await upload(client, course, default_term=3)).json()
    assert body["rows"][0]["term"] == 3
    assert body["rows"][0]["status"] == "ok"
    assert "lapso 3" in fake.calls[0][1]


async def test_duplicados_en_el_archivo(client, course, use_llm):
    use_llm(
        {
            "grades": [
                entry(row=2, cedula="V-1", term=1, value=15),
                entry(row=9, cedula="1", term=1, value=16),
            ]
        }
    )
    rows = (await upload(client, course)).json()["rows"]
    assert [r["status"] for r in rows] == ["error", "error"]
    assert "fila 2, fila 9" in rows[0]["issues"][0]


async def test_confirmar_con_el_endpoint_de_notas(client, course, use_llm, session):
    use_llm(
        {"grades": [entry(cedula="V-1", term=1, value=15), entry(cedula="V-3", term=1, value=9)]}
    )
    rows = (await upload(client, course)).json()["rows"]
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


async def test_archivo_invalido(client, course, use_llm):
    fake = use_llm({"grades": []})
    response = await upload(client, course, content=b"no soy un excel")
    assert response.status_code == 400
    assert fake.calls == []


async def test_archivo_demasiado_grande(client, course, use_llm, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_size_mb", 0)
    use_llm({"grades": []})
    assert (await upload(client, course)).status_code == 413


async def test_ia_no_configurada(client, course, use_llm):
    use_llm(LLMNotConfiguredError())
    assert (await upload(client, course)).status_code == 503


async def test_materia_ajena(client, course, use_llm, login_as):
    use_llm({"grades": []})
    other = await login_as(Role.TEACHER, email="otro@uni.edu")
    assert (await upload(client, course, headers=other)).status_code == 404
