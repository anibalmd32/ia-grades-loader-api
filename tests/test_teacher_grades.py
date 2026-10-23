import pytest
from sqlalchemy import select

from app.models import Grade, Role
from tests.conftest import active_token, assign_section, assign_teacher, class_url, create_section
from tests.factories import names


@pytest.fixture
async def coord(login_as):
    return await login_as(Role.COORDINATOR, email="coord@uni.edu")


@pytest.fixture
async def school(client, coord, session):
    """Dos materias, un profesor con MAT asignada en la sección A y tres estudiantes de esa
    sección matriculados en MAT."""
    semester = (await client.post("/semesters", json={"name": "2026-I"}, headers=coord)).json()
    section_id = await create_section(client, coord, semester["id"])
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
    await assign_teacher(client, coord, teacher_id, [(subjects["MAT-101"], section_id)])

    students = {}
    for cedula, name in [(3, "Carlos"), (1, "Ana"), (2, "Bea")]:
        response = await client.post(
            "/students",
            json={**names(name), "nationality": "V", "cedula": cedula},
            headers=coord,
        )
        students[name] = response.json()["user"]["id"]
    await assign_section(client, coord, section_id, list(students.values()))
    await client.post(
        f"/subjects/{subjects['MAT-101']}/students",
        json={"student_ids": list(students.values())},
        headers=coord,
    )

    token = await active_token(session, teacher_id)
    return {
        "teacher": {"Authorization": f"Bearer {token}"},
        "teacher_id": teacher_id,
        "subjects": subjects,
        "section_id": section_id,
        "students": students,
    }


def url(school, subject="MAT-101") -> str:
    return class_url(school["subjects"][subject], school["section_id"])


def grades_of(body: dict, name: str) -> list:
    row = next(s for s in body["students"] if s["student"]["first_name"] == name)
    return [g["value"] for g in row["grades"]], row["average"]


async def put_grades(client, school, entries, subject="MAT-101"):
    return await client.put(
        f"{url(school, subject)}/grades",
        json={"grades": entries},
        headers=school["teacher"],
    )


async def test_mis_materias(client, school):
    response = await client.get("/teacher/subjects", headers=school["teacher"])
    assert response.status_code == 200
    body = response.json()
    assert [(s["code"], s["section"]["name"], s["students_count"]) for s in body] == [
        ("MAT-101", "A", 3)
    ]
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
    body = (await client.get(f"{url(school)}/grades", headers=school["teacher"])).json()
    assert body["subject"]["code"] == "MAT-101"
    assert body["section"]["name"] == "A"
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
    listed = await client.get(f"{url(school, 'FIS-101')}/grades", headers=school["teacher"])
    assert listed.status_code == 404
    entries = [{"student_id": school["students"]["Ana"], "term": 1, "value": 15}]
    saved = await put_grades(client, school, entries, "FIS-101")
    assert saved.status_code == 404


async def test_marcar_inasistente_en_un_lapso(client, school, session):
    ana = school["students"]["Ana"]
    response = await put_grades(
        client,
        school,
        [
            {"student_id": ana, "term": 1, "absent": True},
            {"student_id": ana, "term": 2, "value": 19},
        ],
    )
    assert response.status_code == 200
    row = next(s for s in response.json()["students"] if s["student"]["first_name"] == "Ana")
    # La inasistencia cuenta con la nota mínima (1): (1 + 19) / 2 = 10
    assert [(g["value"], g["absent"]) for g in row["grades"]] == [
        (1, True),
        (19, False),
        (None, False),
    ]
    assert row["average"] == 10
    stored = await session.scalar(select(Grade).where(Grade.term == 1))
    assert stored.absent is True

    # Corregirla con una nota quita la inasistencia
    response = await put_grades(client, school, [{"student_id": ana, "term": 1, "value": 12}])
    row = next(s for s in response.json()["students"] if s["student"]["first_name"] == "Ana")
    assert (row["grades"][0]["value"], row["grades"][0]["absent"]) == (12, False)


async def test_inasistente_no_lleva_nota(client, school):
    entries = [{"student_id": school["students"]["Ana"], "term": 1, "value": 10, "absent": True}]
    assert (await put_grades(client, school, entries)).status_code == 422


async def test_otra_seccion_no_es_del_profesor(client, school, coord):
    semester_id = (await client.get("/semesters", headers=coord)).json()[0]["id"]
    other = await create_section(client, coord, semester_id, "B")
    response = await client.get(
        f"{class_url(school['subjects']['MAT-101'], other)}/grades", headers=school["teacher"]
    )
    assert response.status_code == 404


@pytest.mark.parametrize("role", [Role.COORDINATOR, Role.STUDENT, Role.SUPER_ADMIN])
async def test_solo_profesores(client, login_as, role):
    headers = await login_as(role, **({} if role == Role.STUDENT else {"email": "x@uni.edu"}))
    assert (await client.get("/teacher/subjects", headers=headers)).status_code == 403
