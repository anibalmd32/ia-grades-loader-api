import pytest

from app.models import Role


@pytest.fixture
async def coord(login_as):
    return await login_as(Role.COORDINATOR, email="coord@uni.edu")


@pytest.fixture
async def setup_data(client, coord):
    semester = (await client.post("/semesters", json={"name": "2026-I"}, headers=coord)).json()
    subjects = []
    for code in ["MAT-101", "FIS-101", "QUI-101"]:
        response = await client.post(
            "/subjects",
            json={"semester_id": semester["id"], "code": code, "name": code},
            headers=coord,
        )
        subjects.append(response.json()["id"])
    teacher = await client.post(
        "/teachers", json={"full_name": "Pedro Profe", "email": "p@uni.edu"}, headers=coord
    )
    return {"teacher": teacher.json()["user"]["id"], "subjects": subjects}


async def put_subjects(client, coord, teacher_id, subject_ids):
    return await client.put(
        f"/teachers/{teacher_id}/subjects", json={"subject_ids": subject_ids}, headers=coord
    )


async def test_asignar_y_reemplazar_materias(client, coord, setup_data):
    teacher, (mat, fis, qui) = setup_data["teacher"], setup_data["subjects"]

    first = await put_subjects(client, coord, teacher, [mat, fis, mat])
    assert [s["code"] for s in first.json()] == ["FIS-101", "MAT-101"]

    second = await put_subjects(client, coord, teacher, [fis, qui])
    assert [s["code"] for s in second.json()] == ["FIS-101", "QUI-101"]

    listed = await client.get(f"/teachers/{teacher}/subjects", headers=coord)
    assert listed.json() == second.json()

    teachers_of_fis = (await client.get(f"/subjects/{fis}/teachers", headers=coord)).json()
    assert [t["full_name"] for t in teachers_of_fis] == ["Pedro Profe"]
    assert (await client.get(f"/subjects/{mat}/teachers", headers=coord)).json() == []

    cleared = await put_subjects(client, coord, teacher, [])
    assert cleared.json() == []


async def test_materias_inexistentes(client, coord, setup_data):
    response = await put_subjects(client, coord, setup_data["teacher"], [999, 998])
    assert response.status_code == 404
    assert response.json()["detail"] == "Materias no encontradas: 998, 999"


async def test_solo_a_profesores(client, coord, setup_data):
    student = await client.post(
        "/students", json={"full_name": "Ana", "nationality": "V", "cedula": 1}, headers=coord
    )
    student_id = student.json()["user"]["id"]
    response = await put_subjects(client, coord, student_id, setup_data["subjects"])
    assert response.status_code == 404


async def test_profesor_no_puede_asignarse_materias(client, login_as, setup_data):
    teacher_headers = await login_as(Role.TEACHER, email="otro@uni.edu")
    response = await put_subjects(
        client, teacher_headers, setup_data["teacher"], setup_data["subjects"]
    )
    assert response.status_code == 403
