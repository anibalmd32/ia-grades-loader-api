import pytest

from app.models import Role
from tests.conftest import create_section
from tests.factories import names


@pytest.fixture
async def coord(login_as):
    return await login_as(Role.COORDINATOR, email="coord@uni.edu")


@pytest.fixture
async def setup_data(client, coord):
    semester = (await client.post("/semesters", json={"name": "2026-I"}, headers=coord)).json()
    other = (await client.post("/semesters", json={"name": "2026-II"}, headers=coord)).json()
    subjects = []
    for code in ["MAT-101", "FIS-101", "QUI-101"]:
        response = await client.post(
            "/subjects",
            json={"semester_id": semester["id"], "code": code, "name": code},
            headers=coord,
        )
        subjects.append(response.json()["id"])
    teacher = await client.post(
        "/teachers", json={**names("Pedro Profe"), "email": "p@uni.edu"}, headers=coord
    )
    return {
        "teacher": teacher.json()["user"]["id"],
        "subjects": subjects,
        "a": await create_section(client, coord, semester["id"], "A"),
        "b": await create_section(client, coord, semester["id"], "B"),
        "other": await create_section(client, coord, other["id"], "A"),
    }


async def put_subjects(client, coord, teacher_id, pairs):
    return await client.put(
        f"/teachers/{teacher_id}/subjects",
        json={"assignments": [{"subject_id": s, "section_id": sec} for s, sec in pairs]},
        headers=coord,
    )


def pairs_of(body):
    return [(a["subject"]["code"], a["section"]["name"]) for a in body]


async def test_asignar_y_reemplazar_materias(client, coord, setup_data):
    teacher, (mat, fis, qui) = setup_data["teacher"], setup_data["subjects"]
    a, b = setup_data["a"], setup_data["b"]

    first = await put_subjects(client, coord, teacher, [(mat, a), (fis, a), (mat, a), (mat, b)])
    assert pairs_of(first.json()) == [("FIS-101", "A"), ("MAT-101", "A"), ("MAT-101", "B")]

    second = await put_subjects(client, coord, teacher, [(fis, a), (qui, b)])
    assert pairs_of(second.json()) == [("FIS-101", "A"), ("QUI-101", "B")]

    listed = await client.get(f"/teachers/{teacher}/subjects", headers=coord)
    assert listed.json() == second.json()

    teachers_of_fis = (await client.get(f"/subjects/{fis}/teachers", headers=coord)).json()
    assert [(t["section"]["name"], t["teacher"]["full_name"]) for t in teachers_of_fis] == [
        ("A", "Pedro Profe")
    ]
    assert (await client.get(f"/subjects/{mat}/teachers", headers=coord)).json() == []

    cleared = await put_subjects(client, coord, teacher, [])
    assert cleared.json() == []


async def test_un_solo_profesor_por_materia_y_seccion(client, coord, setup_data):
    mat, a, b = setup_data["subjects"][0], setup_data["a"], setup_data["b"]
    assert (await put_subjects(client, coord, setup_data["teacher"], [(mat, a)])).status_code == 200
    other = await client.post(
        "/teachers", json={**names("Laura Profe"), "email": "l@uni.edu"}, headers=coord
    )
    laura = other.json()["user"]["id"]

    response = await put_subjects(client, coord, laura, [(mat, a)])
    assert response.status_code == 409
    assert "Pedro Profe" in response.json()["detail"]
    # En otra sección sí puede darla
    ok = await put_subjects(client, coord, laura, [(mat, b)])
    assert pairs_of(ok.json()) == [("MAT-101", "B")]


async def test_seccion_de_otro_periodo(client, coord, setup_data):
    mat = setup_data["subjects"][0]
    response = await put_subjects(
        client, coord, setup_data["teacher"], [(mat, setup_data["other"])]
    )
    assert response.status_code == 422


async def test_materias_inexistentes(client, coord, setup_data):
    a = setup_data["a"]
    response = await put_subjects(client, coord, setup_data["teacher"], [(999, a), (998, a)])
    assert response.status_code == 404
    assert response.json()["detail"] == "Materias no encontradas: 998, 999"

    mat = setup_data["subjects"][0]
    response = await put_subjects(client, coord, setup_data["teacher"], [(mat, 999)])
    assert response.status_code == 404
    assert response.json()["detail"] == "Secciones no encontradas: 999"


async def test_solo_a_profesores(client, coord, setup_data):
    student = await client.post(
        "/students", json={**names("Ana"), "nationality": "V", "cedula": 1}, headers=coord
    )
    student_id = student.json()["user"]["id"]
    pairs = [(s, setup_data["a"]) for s in setup_data["subjects"]]
    response = await put_subjects(client, coord, student_id, pairs)
    assert response.status_code == 404


async def test_profesor_no_puede_asignarse_materias(client, login_as, setup_data):
    teacher_headers = await login_as(Role.TEACHER, email="otro@uni.edu")
    pairs = [(s, setup_data["a"]) for s in setup_data["subjects"]]
    response = await put_subjects(client, teacher_headers, setup_data["teacher"], pairs)
    assert response.status_code == 403
