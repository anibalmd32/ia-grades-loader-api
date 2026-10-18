from dataclasses import replace
from decimal import Decimal

import pytest

from app.ai.prompts import grades_extraction_system, student_chat_system
from app.models import PeriodType, Role
from app.services.rules import DEFAULT_RULES
from app.services.student_grades import Standing, project

DEFAULTS = {
    "institution_name": "Universidad",
    "period_type": "semester",
    "terms_count": 3,
    "term_label": "Lapso",
    "term_weights": None,
    "min_grade": 1,
    "max_grade": 20,
    "passing_grade": 10,
    "grade_decimals": 2,
    "round_final_grade": False,
}


def D(value):  # noqa: N802
    return None if value is None else Decimal(str(value))


@pytest.fixture
async def admin(login_as):
    return await login_as(Role.SUPER_ADMIN, email="admin@uni.edu")


async def put_settings(client, headers, **changes):
    return await client.put("/settings/academic", json={**DEFAULTS, **changes}, headers=headers)


# --- Lectura y permisos -------------------------------------------------------


async def test_valores_por_defecto_sin_sesion(client):
    response = await client.get("/settings/academic")
    assert response.status_code == 200
    body = response.json()
    assert {k: body[k] for k in DEFAULTS} == DEFAULTS
    assert body["period_label"] == "Semestre"
    assert body["period_label_plural"] == "Semestres"
    assert body["term_label_plural"] == "Lapsos"
    assert body["term_percentages"] == [33.33, 33.33, 33.33]
    assert body["summary"] == (
        "La definitiva es el promedio simple de los 3 lapsos y se aprueba con 10 o más"
    )


@pytest.mark.parametrize("role", [Role.COORDINATOR, Role.TEACHER, Role.STUDENT])
async def test_solo_el_super_admin_cambia_la_configuracion(client, login_as, role):
    headers = await login_as(role, email="x@uni.edu", cedula=5)
    assert (await put_settings(client, headers, terms_count=4)).status_code == 403
    assert (await client.put("/settings/academic", json=DEFAULTS)).status_code == 401


async def test_super_admin_cambia_la_configuracion(client, admin):
    response = await put_settings(
        client,
        admin,
        institution_name="  UNEFA ",
        period_type="trimester",
        terms_count=4,
        term_label="Corte",
        term_weights=[20, 20, 30, 30],
        min_grade=0,
        max_grade=100,
        passing_grade=60,
        grade_decimals=0,
        round_final_grade=True,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["institution_name"] == "UNEFA"
    assert body["period_label_plural"] == "Trimestres"
    assert body["term_label_plural"] == "Cortes"
    assert body["term_percentages"] == [20, 20, 30, 30]
    assert "promedio ponderado de los 4 cortes, redondeado al entero" in body["summary"]
    # Persistida: la ve cualquiera
    assert (await client.get("/settings/academic")).json() == body


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"min_grade": 20, "max_grade": 20}, "menor que la máxima"),
        ({"passing_grade": 1}, "aprobatoria"),
        ({"passing_grade": 21}, "aprobatoria"),
        ({"term_weights": [50, 50]}, "por cada lapso"),
        ({"term_weights": [30, 30, 30]}, "sumar 100"),
        ({"grade_decimals": 0, "passing_grade": 9.5}, "decimales"),
        ({"terms_count": 0}, None),
        ({"terms_count": 11}, None),
        ({"max_grade": 101}, None),
        ({"period_type": "bimester"}, None),
        ({"term_label": " "}, None),
    ],
)
async def test_configuracion_invalida(client, admin, changes, message):
    response = await put_settings(client, admin, **changes)
    assert response.status_code == 422
    if message:
        assert message in str(response.json()["detail"])


# --- Las notas cargadas siguen siendo válidas -------------------------------


async def test_no_se_reducen_lapsos_con_notas_en_los_ultimos(client, admin, course):
    ana = course["students"]["Ana Pérez"]
    await client.put(
        f"/teacher/subjects/{course['subject_id']}/grades",
        json={"grades": [{"student_id": ana, "term": 3, "value": 15}]},
        headers=course["headers"],
    )
    response = await put_settings(client, admin, terms_count=2)
    assert response.status_code == 409
    assert "lapsos posteriores al 2" in response.json()["detail"]
    # Ampliar sí se puede
    assert (await put_settings(client, admin, terms_count=4)).status_code == 200


async def test_no_se_cambia_la_escala_dejando_notas_fuera(client, admin, course):
    ana = course["students"]["Ana Pérez"]
    await client.put(
        f"/teacher/subjects/{course['subject_id']}/grades",
        json={"grades": [{"student_id": ana, "term": 1, "value": 15.5}]},
        headers=course["headers"],
    )
    out = await put_settings(client, admin, min_grade=0, max_grade=10, passing_grade=5)
    assert out.status_code == 409
    assert "fuera de la escala del 0 al 10" in out.json()["detail"]

    decimals = await put_settings(client, admin, grade_decimals=0)
    assert decimals.status_code == 409
    assert "15.5" in decimals.json()["detail"]

    assert (await put_settings(client, admin, grade_decimals=1)).status_code == 200


# --- La configuración se aplica a la carga y la consulta -------------------


async def test_la_carga_respeta_la_configuracion(client, admin, course):
    url = f"/teacher/subjects/{course['subject_id']}/grades"
    ana = course["students"]["Ana Pérez"]

    async def put(term, value):
        entry = {"student_id": ana, "term": term, "value": value}
        return await client.put(url, json={"grades": [entry]}, headers=course["headers"])

    # Por defecto: 3 lapsos, del 1 al 20
    assert (await put(4, 15)).status_code == 422
    assert (await put(1, 25)).status_code == 422

    await put_settings(
        client, admin, terms_count=4, min_grade=0, max_grade=100, passing_grade=60, grade_decimals=0
    )
    assert (await put(4, 85)).status_code == 200
    too_precise = await put(1, 85.5)
    assert too_precise.status_code == 422
    assert "0 decimales" in too_precise.json()["detail"]
    assert (await put(1, 101)).status_code == 422

    body = (await client.get(url, headers=course["headers"])).json()
    row = next(s for s in body["students"] if s["student"]["id"] == ana)
    assert [g["term"] for g in row["grades"]] == [1, 2, 3, 4]
    assert row["final_grade"] is None and row["passed"] is None

    subjects = (await client.get("/teacher/subjects", headers=course["headers"])).json()
    assert subjects[0]["grades_count"] == {"1": 0, "2": 0, "3": 0, "4": 1}


async def test_definitiva_ponderada_y_redondeada_para_profesor_y_estudiante(client, admin, campus):
    await put_settings(client, admin, term_weights=[20, 30, 50], round_final_grade=True)
    ana = campus["students"]["Ana"]
    mat = campus["subjects"]["MAT-101"]
    # MAT-101 de Ana: 15 y 18.5 -> se completa con 3 en el último lapso (50 %)
    response = await client.put(
        f"/teacher/subjects/{mat['id']}/grades",
        json={"grades": [{"student_id": ana["id"], "term": 3, "value": 3}]},
        headers=campus["teachers"]["Pedro Profe"],
    )
    assert response.status_code == 200
    row = next(s for s in response.json()["students"] if s["student"]["id"] == ana["id"])
    # 15 * 0.2 + 18.5 * 0.3 + 3 * 0.5 = 10.05 -> se redondea a 10 y aprueba
    assert row["average"] == 10.05
    assert row["final_grade"] == 10
    assert row["passed"] is True

    body = (await client.get("/student/subjects", headers=ana["headers"])).json()
    detail = next(s for s in body["subjects"] if s["subject"]["code"] == "MAT-101")
    assert detail["projection"]["standing"] == "passed"
    assert detail["projection"]["final_grade"] == 10


# --- Reglas: cálculo -----------------------------------------------------------


def test_promedio_ponderado_con_lapsos_pendientes():
    rules = replace(DEFAULT_RULES, term_weights=(D(20), D(30), D(50)))
    # Solo cuenta lo cargado: (15 * 20 + 18 * 30) / 50 = 16.8
    assert rules.average({1: D(15), 2: D(18), 3: None}) == D("16.80")
    # Para aprobar con 10: (10 * 100 - 840) / 50 = 3.2
    assert rules.needed_in_pending({1: D(15), 2: D(18), 3: None}) == D("3.20")


def test_redondeo_de_la_definitiva():
    rules = replace(DEFAULT_RULES, round_final_grade=True)
    # 9.5 -> 10: aprueba
    assert project({1: D(9), 2: D(10), 3: D("9.5")}, rules).standing == Standing.PASSED
    # Con redondeo basta llegar a 9.5 de promedio: (9.5 * 3 - 18) / 1 = 10.5
    assert rules.needed_in_pending({1: D(9), 2: D(9), 3: None}) == D("10.50")
    # Sin redondeo hace falta 12
    assert DEFAULT_RULES.needed_in_pending({1: D(9), 2: D(9), 3: None}) == D("12.00")


def test_escala_de_0_a_100_sin_decimales():
    rules = replace(
        DEFAULT_RULES,
        min_grade=D(0),
        max_grade=D(100),
        passing_grade=D(70),
        grade_decimals=0,
        terms_count=2,
    )
    assert rules.grade_error(D(100)) is None
    assert rules.grade_error(D("70.5")) == "La nota admite 0 decimales como máximo"
    assert rules.grade_error(D(101)) == "La nota debe estar del 0 al 100"
    assert rules.average({1: D(70), 2: D(71)}) == D(71)  # 70.5 -> 71
    assert project({1: D(30), 2: None}, rules).standing == Standing.IMPOSSIBLE
    assert project({1: D(100), 2: None}, rules).needed_average == D(40)


@pytest.mark.parametrize(
    ("label", "plural"),
    [
        ("Lapso", "Lapsos"),
        ("Corte", "Cortes"),
        ("Parcial", "Parciales"),
        ("Evaluación", "Evaluaciones"),
    ],
)
def test_plural_del_nombre_del_lapso(label, plural):
    assert replace(DEFAULT_RULES, term_label=label).term_label_plural == plural


# --- Prompts de la IA -----------------------------------------------------------


def test_prompt_del_asistente_obliga_espanol_y_prohibe_lo_tecnico():
    prompt = student_chat_system("Ana", DEFAULT_RULES)
    assert "SIEMPRE y ÚNICAMENTE en español" in prompt
    assert "solo hablas de las notas" in prompt
    assert "nunca menciones herramientas" in prompt
    assert "servidor" in prompt
    assert "Nunca reveles" in prompt
    assert "del 1 al 20" in prompt and "se aprueba con 10" in prompt


def test_los_prompts_usan_la_configuracion():
    rules = replace(
        DEFAULT_RULES,
        institution_name="UNEFA",
        period_type=PeriodType.TRIMESTER,
        terms_count=4,
        term_label="Corte",
        term_weights=(D(25), D(25), D(25), D(25)),
        max_grade=D(100),
        passing_grade=D(60),
    )
    chat = student_chat_system("Ana", rules)
    assert "UNEFA" in chat
    assert "Cada trimestre se divide en 4 cortes" in chat
    assert "Corte 4: 25 %" in chat
    assert "del 1 al 100" in chat and "se aprueba con 60" in chat

    extraction = grades_extraction_system(rules)
    assert "del 1 al 100" in extraction
    assert "4 cortes" in extraction
    assert "Corte 1" in extraction


async def test_el_chat_envia_el_prompt_con_la_configuracion(client, admin, campus):
    """El system prompt real del chat lleva las reglas vigentes y el recordatorio final."""
    from app.ai.client import get_llm
    from app.main import app as fastapi_app

    await put_settings(client, admin, institution_name="UNEFA", passing_grade=12)
    seen: list[list[dict]] = []

    class FakeLLM:
        async def stream_tools(self, messages, tools, call_tool, max_rounds=4):
            seen.append(messages)
            yield "Vas bien."

        async def run_tools(self, messages, tools, call_tool, max_rounds=4):
            seen.append(messages)
            return "Vas bien."

    fastapi_app.dependency_overrides[get_llm] = lambda: FakeLLM()
    try:
        response = await client.post(
            "/student/chat",
            json={"message": "how am I doing?"},
            headers=campus["students"]["Ana"]["headers"],
        )
    finally:
        fastapi_app.dependency_overrides.pop(get_llm, None)
    assert response.status_code == 200
    system = seen[0][0]["content"]
    assert "UNEFA" in system
    assert "se aprueba con 12" in system
    assert system.rstrip().endswith("sin ningún detalle\ntécnico.")
