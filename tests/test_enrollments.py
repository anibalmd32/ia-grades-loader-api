import pytest
from sqlalchemy import select

from app.models import Enrollment, Grade, Role
from tests.conftest import assign_section, create_section
from tests.factories import names


@pytest.fixture
async def coord(login_as):
    return await login_as(Role.COORDINATOR, email="coord@uni.edu")


@pytest.fixture
async def data(client, coord):
    semester = (await client.post("/semesters", json={"name": "2026-I"}, headers=coord)).json()
    subjects = {}
    for code in ["MAT-101", "FIS-101"]:
        response = await client.post(
            "/subjects",
            json={"semester_id": semester["id"], "code": code, "name": code},
            headers=coord,
        )
        subjects[code] = response.json()["id"]
    students = {}
    for cedula, name in [(1, "Ana"), (2, "Bea"), (3, "Carlos")]:
        response = await client.post(
            "/students",
            json={**names(name), "nationality": "V", "cedula": cedula},
            headers=coord,
        )
        students[name] = response.json()["user"]["id"]
    section = await create_section(client, coord, semester["id"])
    await assign_section(client, coord, section, list(students.values()))
    return {"subjects": subjects, "students": students, "semester_id": semester["id"]}


async def test_matricula_individual(client, coord, data):
    ana = data["students"]["Ana"]
    mat, fis = data["subjects"]["MAT-101"], data["subjects"]["FIS-101"]

    first = await client.post(
        f"/students/{ana}/subjects", json={"subject_ids": [mat]}, headers=coord
    )
    assert first.json() == {"added": 1, "already_enrolled": 0}

    second = await client.post(
        f"/students/{ana}/subjects", json={"subject_ids": [mat, fis]}, headers=coord
    )
    assert second.json() == {"added": 1, "already_enrolled": 1}

    listed = (await client.get(f"/students/{ana}/subjects", headers=coord)).json()
    assert [s["code"] for s in listed] == ["FIS-101", "MAT-101"]


async def test_matricula_en_lote_por_materia(client, coord, data):
    mat = data["subjects"]["MAT-101"]
    ids = list(data["students"].values())

    response = await client.post(
        f"/subjects/{mat}/students", json={"student_ids": ids}, headers=coord
    )
    assert response.json() == {"added": 3, "already_enrolled": 0}

    again = await client.post(
        f"/subjects/{mat}/students", json={"student_ids": ids[:2]}, headers=coord
    )
    assert again.json() == {"added": 0, "already_enrolled": 2}

    listed = (await client.get(f"/subjects/{mat}/students", headers=coord)).json()
    assert [s["first_name"] for s in listed] == ["Ana", "Bea", "Carlos"]


async def test_sin_seccion_no_se_matricula(client, coord, data):
    mat = data["subjects"]["MAT-101"]
    dani = await client.post(
        "/students", json={**names("Dani"), "nationality": "V", "cedula": 4}, headers=coord
    )
    dani_id = dani.json()["user"]["id"]
    ids = [data["students"]["Ana"], dani_id]

    response = await client.post(
        f"/subjects/{mat}/students", json={"student_ids": ids}, headers=coord
    )
    assert response.status_code == 409
    assert "Dani Test (2026-I)" in response.json()["detail"]
    # Todo o nada: Ana tampoco queda matriculada
    assert (await client.get(f"/subjects/{mat}/students", headers=coord)).json() == []

    # Con sección ya se puede
    section = await create_section(client, coord, data["semester_id"], "B")
    await assign_section(client, coord, section, [dani_id])
    ok = await client.post(f"/subjects/{mat}/students", json={"student_ids": ids}, headers=coord)
    assert ok.json() == {"added": 2, "already_enrolled": 0}


async def test_lote_valida_ids(client, coord, data):
    mat = data["subjects"]["MAT-101"]
    teacher = await client.post(
        "/teachers", json={**names("Pedro"), "email": "p@uni.edu"}, headers=coord
    )
    teacher_id = teacher.json()["user"]["id"]
    ana = data["students"]["Ana"]

    response = await client.post(
        f"/subjects/{mat}/students", json={"student_ids": [ana, teacher_id, 999]}, headers=coord
    )
    assert response.status_code == 404
    assert response.json()["detail"] == f"Estudiantes no encontrados: {teacher_id}, 999"
    # Si algo falla no se matricula a nadie
    assert (await client.get(f"/subjects/{mat}/students", headers=coord)).json() == []

    bad_subject = await client.post(
        f"/students/{ana}/subjects", json={"subject_ids": [999]}, headers=coord
    )
    assert bad_subject.status_code == 404

    empty = await client.post(f"/students/{ana}/subjects", json={"subject_ids": []}, headers=coord)
    assert empty.status_code == 422


async def test_retirar_matricula(client, coord, data):
    ana, mat = data["students"]["Ana"], data["subjects"]["MAT-101"]
    await client.post(f"/students/{ana}/subjects", json={"subject_ids": [mat]}, headers=coord)

    url = f"/students/{ana}/subjects/{mat}"
    assert (await client.delete(url, headers=coord)).status_code == 204
    assert (await client.delete(url, headers=coord)).status_code == 404


async def test_no_se_retira_matricula_con_notas(client, coord, data, session):
    ana, mat = data["students"]["Ana"], data["subjects"]["MAT-101"]
    await client.post(f"/students/{ana}/subjects", json={"subject_ids": [mat]}, headers=coord)
    enrollment = await session.scalar(select(Enrollment).where(Enrollment.student_id == ana))
    session.add(Grade(enrollment_id=enrollment.id, term=1, value=15))
    await session.commit()

    response = await client.delete(f"/students/{ana}/subjects/{mat}", headers=coord)
    assert response.status_code == 409

    # Tampoco se puede borrar la materia con estudiantes matriculados
    assert (await client.delete(f"/subjects/{mat}", headers=coord)).status_code == 409


async def test_profesor_no_matricula(client, login_as, data):
    teacher = await login_as(Role.TEACHER, email="t@uni.edu")
    ana, mat = data["students"]["Ana"], data["subjects"]["MAT-101"]
    response = await client.post(
        f"/students/{ana}/subjects", json={"subject_ids": [mat]}, headers=teacher
    )
    assert response.status_code == 403
