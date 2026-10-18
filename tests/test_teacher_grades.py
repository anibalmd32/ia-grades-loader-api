import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.models import Grade, Role, User
from tests.factories import names


@pytest.fixture
async def coord(login_as):
    return await login_as(Role.COORDINATOR, email="coord@uni.edu")


@pytest.fixture
async def school(client, coord, session):
    """Dos materias, un profesor con MAT asignada y tres estudiantes matriculados en MAT."""
    semester = (await client.post("/semesters", json={"name": "2026-I"}, headers=coord)).json()
    subjects = {}
    for code in ["MAT-101", "FIS-101"]:
        response = await client.post(
            "/subjects",
            json={"semester_id": semester["id"], "code": code, "name": code},
            headers=coord,
        )
        subjects[code] = response.json()["id"]

    teacher = await client.post(
        "/teachers", json={**names("Pedro Profe"), "email": "p@uni.edu"}, headers=coord
    )
    teacher_id = teacher.json()["user"]["id"]
    await client.put(
        f"/teachers/{teacher_id}/subjects",
        json={"subject_ids": [subjects["MAT-101"]]},
        headers=coord,
    )

    students = {}
    for cedula, name in [(3, "Carlos"), (1, "Ana"), (2, "Bea")]:
        response = await client.post(
            "/students",
            json={**names(name), "nationality": "V", "cedula": cedula},
            headers=coord,
        )
        students[name] = response.json()["user"]["id"]
    await client.post(
        f"/subjects/{subjects['MAT-101']}/students",
        json={"student_ids": list(students.values())},
        headers=coord,
    )

    user = await session.get(User, teacher_id)
    token, _ = create_access_token(user.id, user.role.value, user.token_version)
    return {
        "teacher": {"Authorization": f"Bearer {token}"},
        "teacher_id": teacher_id,
        "subjects": subjects,
        "students": students,
    }


def grades_of(body: dict, name: str) -> list:
    row = next(s for s in body["students"] if s["student"]["first_name"] == name)
    return [g["value"] for g in row["grades"]], row["average"]


async def put_grades(client, school, entries, subject="MAT-101"):
    return await client.put(
        f"/teacher/subjects/{school['subjects'][subject]}/grades",
        json={"grades": entries},
        headers=school["teacher"],
    )


async def test_mis_materias(client, school):
    response = await client.get("/teacher/subjects", headers=school["teacher"])
    assert response.status_code == 200
    body = response.json()
    assert [(s["code"], s["students_count"]) for s in body] == [("MAT-101", 3)]
    assert body[0]["semester"]["name"] == "2026-I"
    assert body[0]["grades_count"] == {"1": 0, "2": 0, "3": 0}


async def test_mis_materias_cuenta_notas_por_lapso(client, school):
    ana, bea = school["students"]["Ana"], school["students"]["Bea"]
    response = await put_grades(
        client,
        school,
        [
            {"student_id": ana, "term": 1, "value": 15},
            {"student_id": bea, "term": 1, "value": 9.5},
            {"student_id": ana, "term": 2, "value": 18},
        ],
    )
    assert response.status_code == 200
    body = (await client.get("/teacher/subjects", headers=school["teacher"])).json()
    assert body[0]["grades_count"] == {"1": 2, "2": 1, "3": 0}

    # Borrar una nota la descuenta
    await put_grades(client, school, [{"student_id": ana, "term": 2, "value": None}])
    body = (await client.get("/teacher/subjects", headers=school["teacher"])).json()
    assert body[0]["grades_count"] == {"1": 2, "2": 0, "3": 0}


async def test_planilla_vacia(client, school):
    mat = school["subjects"]["MAT-101"]
    body = (await client.get(f"/teacher/subjects/{mat}/grades", headers=school["teacher"])).json()
    assert body["subject"]["code"] == "MAT-101"
    assert [s["student"]["first_name"] for s in body["students"]] == ["Ana", "Bea", "Carlos"]
    assert body["students"][0]["student"]["cedula_display"] == "V-1"
    assert grades_of(body, "Ana") == ([None, None, None], None)


async def test_cargar_corregir_y_borrar_notas(client, school, session):
    ana, bea = school["students"]["Ana"], school["students"]["Bea"]

    first = await put_grades(
        client,
        school,
        [
            {"student_id": ana, "term": 1, "value": 15},
            {"student_id": ana, "term": 2, "value": 18},
            {"student_id": bea, "term": 1, "value": 10},
        ],
    )
    assert first.status_code == 200
    assert grades_of(first.json(), "Ana") == ([15, 18, None], 16.5)
    assert grades_of(first.json(), "Bea") == ([10, None, None], 10.0)

    second = await put_grades(
        client,
        school,
        [
            {"student_id": ana, "term": 2, "value": 20},
            {"student_id": bea, "term": 1, "value": None},
        ],
    )
    assert grades_of(second.json(), "Ana") == ([15, 20, None], 17.5)
    assert grades_of(second.json(), "Bea") == ([None, None, None], None)

    stored = (await session.scalars(select(Grade))).all()
    assert len(stored) == 2
    assert {g.updated_by_id for g in stored} == {school["teacher_id"]}


@pytest.mark.parametrize(
    "entry",
    [
        {"term": 1, "value": 0},
        {"term": 1, "value": 21},
        {"term": 0, "value": 10},
        {"term": 4, "value": 10},
        {"term": 1, "value": 12.555},
        {"term": 1, "value": 20.01},
    ],
)
async def test_validacion_de_rangos(client, school, entry):
    entries = [{"student_id": school["students"]["Ana"], **entry}]
    response = await put_grades(client, school, entries)
    assert response.status_code == 422


async def test_notas_con_decimales_y_promedio(client, school):
    ana = school["students"]["Ana"]
    response = await put_grades(
        client,
        school,
        [
            {"student_id": ana, "term": 1, "value": 15.75},
            {"student_id": ana, "term": 2, "value": "12.5"},
            {"student_id": ana, "term": 3, "value": 18},
        ],
    )
    assert response.status_code == 200
    # (15.75 + 12.5 + 18) / 3 = 15.41666... -> 15.42
    assert grades_of(response.json(), "Ana") == ([15.75, 12.5, 18], 15.42)


async def test_notas_repetidas_en_la_peticion(client, school):
    ana = school["students"]["Ana"]
    response = await put_grades(
        client,
        school,
        [{"student_id": ana, "term": 1, "value": 10}, {"student_id": ana, "term": 1, "value": 12}],
    )
    assert response.status_code == 422


async def test_estudiante_no_matriculado_no_guarda_nada(client, school, coord, session):
    outsider = await client.post(
        "/students", json={**names("Zoe"), "nationality": "E", "cedula": 9}, headers=coord
    )
    outsider_id = outsider.json()["user"]["id"]
    response = await put_grades(
        client,
        school,
        [
            {"student_id": school["students"]["Ana"], "term": 1, "value": 15},
            {"student_id": outsider_id, "term": 1, "value": 15},
        ],
    )
    assert response.status_code == 404
    assert str(outsider_id) in response.json()["detail"]
    assert (await session.scalars(select(Grade))).all() == []


async def test_materia_no_asignada(client, school):
    fis = school["subjects"]["FIS-101"]
    listed = await client.get(f"/teacher/subjects/{fis}/grades", headers=school["teacher"])
    assert listed.status_code == 404
    entries = [{"student_id": school["students"]["Ana"], "term": 1, "value": 15}]
    saved = await put_grades(client, school, entries, "FIS-101")
    assert saved.status_code == 404


@pytest.mark.parametrize("role", [Role.COORDINATOR, Role.STUDENT, Role.SUPER_ADMIN])
async def test_solo_profesores(client, login_as, role):
    headers = await login_as(role, **({} if role == Role.STUDENT else {"email": "x@uni.edu"}))
    assert (await client.get("/teacher/subjects", headers=headers)).status_code == 403
