import pytest

from app.core.security import create_access_token
from app.models import Role, User


@pytest.fixture
async def campus(client, login_as, session):
    """Dos semestres, tres materias, dos profesores; Ana cursa tres materias, Beto una."""
    coord = await login_as(Role.COORDINATOR, email="coord@uni.edu")

    async def post(url, payload):
        response = await client.post(url, json=payload, headers=coord)
        assert response.status_code in (200, 201), response.text
        return response.json()

    s1 = await post("/semesters", {"name": "2026-I"})
    s2 = await post("/semesters", {"name": "2026-II"})
    mat = await post("/subjects", {"semester_id": s1["id"], "code": "MAT-101", "name": "Mate I"})
    fis = await post("/subjects", {"semester_id": s1["id"], "code": "FIS-101", "name": "Física"})
    mat2 = await post("/subjects", {"semester_id": s2["id"], "code": "MAT-201", "name": "Mate II"})

    teachers = {}
    for name, email, subjects in [
        ("Pedro Profe", "p@uni.edu", [mat["id"], fis["id"]]),
        ("Laura Profe", "l@uni.edu", [mat["id"]]),
    ]:
        teacher = await post("/teachers", {"full_name": name, "email": email})
        teacher_id = teacher["user"]["id"]
        await client.put(
            f"/teachers/{teacher_id}/subjects", json={"subject_ids": subjects}, headers=coord
        )
        user = await session.get(User, teacher_id)
        token, _ = create_access_token(user.id, user.role.value, user.token_version)
        teachers[name] = {"Authorization": f"Bearer {token}"}

    students = {}
    for name, cedula, subjects in [
        ("Ana", 1, [mat["id"], fis["id"], mat2["id"]]),
        ("Beto", 2, [mat["id"]]),
    ]:
        created = await post("/students", {"full_name": name, "nationality": "V", "cedula": cedula})
        student_id = created["user"]["id"]
        await post(f"/students/{student_id}/subjects", {"subject_ids": subjects})
        user = await session.get(User, student_id)
        token, _ = create_access_token(user.id, user.role.value, user.token_version)
        students[name] = {"id": student_id, "headers": {"Authorization": f"Bearer {token}"}}

    async def grade(teacher, subject, student, term, value):
        entry = {"student_id": students[student]["id"], "term": term, "value": value}
        response = await client.put(
            f"/teacher/subjects/{subject['id']}/grades",
            json={"grades": [entry]},
            headers=teachers[teacher],
        )
        assert response.status_code == 200, response.text

    await grade("Pedro Profe", mat, "Ana", 1, 15)
    await grade("Pedro Profe", mat, "Ana", 2, 18.5)
    await grade("Pedro Profe", fis, "Ana", 1, 10)
    await grade("Pedro Profe", mat, "Beto", 1, 20)
    return {"students": students, "subjects": {"MAT-101": mat, "FIS-101": fis, "MAT-201": mat2}}


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
