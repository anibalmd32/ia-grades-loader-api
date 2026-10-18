"""Recuentos por fila en los listados de administración (materias, profesores, etc.)."""

import pytest

from app.models import Role


@pytest.fixture
async def coord(login_as):
    return await login_as(Role.COORDINATOR, email="coord2@uni.edu")


async def test_materias_por_semestre(client, campus, coord):
    semesters = (await client.get("/semesters", headers=coord)).json()
    assert {s["name"]: s["subjects_count"] for s in semesters} == {"2026-I": 2, "2026-II": 1}

    empty = (await client.post("/semesters", json={"name": "2027-I"}, headers=coord)).json()
    detail = (await client.get(f"/semesters/{empty['id']}", headers=coord)).json()
    assert detail["subjects_count"] == 0


async def test_profesores_y_estudiantes_por_materia(client, campus, coord):
    page = (await client.get("/subjects", headers=coord)).json()
    counts = {s["code"]: (s["teachers_count"], s["students_count"]) for s in page["items"]}
    assert counts == {"FIS-101": (1, 1), "MAT-101": (2, 2), "MAT-201": (0, 1)}

    mat = campus["subjects"]["MAT-101"]
    detail = (await client.get(f"/subjects/{mat['id']}", headers=coord)).json()
    assert (detail["teachers_count"], detail["students_count"]) == (2, 2)
    # El semestre anidado no trae recuento
    assert detail["semester"]["subjects_count"] is None


async def test_materias_por_profesor_y_estudiante(client, campus, coord):
    teachers = (await client.get("/teachers", headers=coord)).json()["items"]
    assert {t["full_name"]: t["subjects_count"] for t in teachers} == {
        "Laura Profe": 1,
        "Pedro Profe": 2,
    }
    students = (await client.get("/students", headers=coord)).json()["items"]
    assert {s["full_name"]: s["subjects_count"] for s in students} == {"Ana": 3, "Beto": 1}

    ana = campus["students"]["Ana"]["id"]
    detail = (await client.get(f"/students/{ana}", headers=coord)).json()
    assert detail["subjects_count"] == 3


async def test_coordinadores_y_respuestas_anidadas_sin_recuento(client, campus, login_as):
    admin = await login_as(Role.SUPER_ADMIN, email="admin@uni.edu")
    coordinators = (await client.get("/coordinators", headers=admin)).json()["items"]
    assert coordinators
    assert all(c["subjects_count"] is None for c in coordinators)

    # Fuera de los listados de administración los recuentos van en null
    ana = campus["students"]["Ana"]["headers"]
    subject = (await client.get("/student/subjects", headers=ana)).json()["subjects"][0]
    assert subject["subject"]["teachers_count"] is None
    assert subject["subject"]["students_count"] is None
