import pytest

from app.models import Role


async def test_mis_materias(client, campus):
    ana = campus["students"]["Ana"]
    response = await client.get("/student/subjects", headers=ana["headers"])
    assert response.status_code == 200
    body = response.json()

    # Ordenadas por semestre y código
    assert [s["subject"]["code"] for s in body["subjects"]] == ["FIS-101", "MAT-101", "MAT-201"]
    fis, mat, mat2 = body["subjects"]

    assert [g["value"] for g in mat["grades"]] == [15, 18.5, None]
    assert mat["average"] == 16.75
    assert mat["teachers"] == ["Laura Profe", "Pedro Profe"]
    assert mat["subject"]["semester"]["name"] == "2026-I"

    assert [g["value"] for g in fis["grades"]] == [10, None, None]
    assert fis["average"] == 10

    assert [g["value"] for g in mat2["grades"]] == [None, None, None]
    assert mat2["average"] is None
    assert mat2["teachers"] == []

    # Promedio general: (16.75 + 10) / 2 = 13.375 -> 13.38 (la materia sin notas no cuenta)
    assert body["average"] == 13.38


async def test_solo_ve_sus_propias_notas(client, campus):
    beto = campus["students"]["Beto"]
    body = (await client.get("/student/subjects", headers=beto["headers"])).json()
    assert [s["subject"]["code"] for s in body["subjects"]] == ["MAT-101"]
    assert [g["value"] for g in body["subjects"][0]["grades"]] == [20, None, None]


async def test_detalle_de_materia(client, campus):
    ana = campus["students"]["Ana"]
    mat = campus["subjects"]["MAT-101"]
    response = await client.get(f"/student/subjects/{mat['id']}", headers=ana["headers"])
    assert response.status_code == 200
    assert response.json()["average"] == 16.75


async def test_detalle_de_materia_no_matriculada(client, campus):
    beto = campus["students"]["Beto"]
    fis = campus["subjects"]["FIS-101"]
    response = await client.get(f"/student/subjects/{fis['id']}", headers=beto["headers"])
    assert response.status_code == 404
    assert (await client.get("/student/subjects/999", headers=beto["headers"])).status_code == 404


async def test_estudiante_sin_materias(client, login_as):
    headers = await login_as(Role.STUDENT, cedula=77)
    body = (await client.get("/student/subjects", headers=headers)).json()
    assert body == {"subjects": [], "average": None}


@pytest.mark.parametrize("role", [Role.TEACHER, Role.COORDINATOR, Role.SUPER_ADMIN])
async def test_solo_estudiantes(client, login_as, role):
    headers = await login_as(role, email="x@uni.edu")
    assert (await client.get("/student/subjects", headers=headers)).status_code == 403


# --- Proyección: definitiva = promedio simple, se aprueba con 10 --------------


def D(value):  # noqa: N802
    from decimal import Decimal

    return None if value is None else Decimal(str(value))


@pytest.mark.parametrize(
    ("grades", "standing", "final", "needed"),
    [
        # Falta un lapso: (30 - 17.5) / 1 = 12.5
        ((8, 9.5, None), "in_progress", None, "12.50"),
        # Faltan dos: (30 - 9) / 2 = 10.5
        ((9, None, None), "in_progress", None, "10.50"),
        # Sin notas: necesita 10 de promedio
        ((None, None, None), "in_progress", None, "10.00"),
        # Redondeo hacia arriba: (30 - 19.99) / 1 = 10.01; (30 - 20.005) no aplica (2 dec.)
        ((10, 9.99, None), "in_progress", None, "10.01"),
        # Aprueba aunque saque 1: (30 - 29) / 1 = 1
        ((15, 14, None), "secured", None, None),
        # Ni con 20 aprueba: (30 - 2) / 1 = 28
        ((1, 1, None), "impossible", None, None),
        # Cerrada
        ((10, 10, 10), "passed", "10.00", None),
        ((9, 10, 10), "failed", "9.67", None),
    ],
)
def test_proyeccion(grades, standing, final, needed):
    from app.services.student_grades import project

    result = project({t: D(v) for t, v in enumerate(grades, start=1)})
    assert result.standing == standing
    assert result.final_grade == D(final)
    assert result.needed_average == D(needed)
    assert result.pending_terms == [t for t, v in enumerate(grades, start=1) if v is None]


def test_proyeccion_respeta_la_nota_aprobatoria_configurada(monkeypatch):
    from app.core.config import get_settings
    from app.services.student_grades import project

    monkeypatch.setattr(get_settings(), "passing_grade", 12)
    assert project({1: D(12), 2: D(12), 3: None}).needed_average == D("12.00")


async def test_la_api_devuelve_la_proyeccion(client, campus):
    ana = campus["students"]["Ana"]
    body = (await client.get("/student/subjects", headers=ana["headers"])).json()
    fis = next(s for s in body["subjects"] if s["subject"]["code"] == "FIS-101")
    # Física: 10 en el lapso 1 -> faltan 2 lapsos con promedio (30 - 10) / 2 = 10
    assert fis["projection"] == {
        "standing": "in_progress",
        "final_grade": None,
        "pending_terms": [2, 3],
        "needed_average": 10.0,
    }
    mat = next(s for s in body["subjects"] if s["subject"]["code"] == "MAT-101")
    assert mat["projection"]["standing"] == "secured"  # 15 + 18.5 ya pasan de 30 - 20
