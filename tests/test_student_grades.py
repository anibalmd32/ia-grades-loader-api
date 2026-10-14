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
