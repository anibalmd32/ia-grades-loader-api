import pytest

from app.models import Role


@pytest.fixture
async def coord(login_as):
    return await login_as(Role.COORDINATOR, email="coordina@uni.edu")


async def semester_of(client, coord, campus):
    return campus["subjects"]["MAT-101"]["semester"]["id"]


async def test_cerrar_y_reabrir(client, coord, campus):
    semester_id = await semester_of(client, coord, campus)
    closed = await client.post(f"/semesters/{semester_id}/close", headers=coord)
    assert closed.status_code == 200
    assert closed.json()["is_closed"] is True
    assert closed.json()["closed_at"].endswith("Z") or "+00:00" in closed.json()["closed_at"]

    listed = (await client.get("/semesters", headers=coord)).json()
    assert {s["name"]: s["is_closed"] for s in listed} == {"2026-I": True, "2026-II": False}

    reopened = await client.post(f"/semesters/{semester_id}/reopen", headers=coord)
    assert reopened.json()["is_closed"] is False
    assert reopened.json()["closed_at"] is None


async def test_solo_coordinacion_cierra(client, campus):
    teacher = campus["teachers"]["Pedro Profe"]
    semester_id = campus["subjects"]["MAT-101"]["semester"]["id"]
    assert (
        await client.post(f"/semesters/{semester_id}/close", headers=teacher)
    ).status_code == 403


async def test_periodo_cerrado_bloquea_notas_importaciones_y_matriculas(client, coord, campus):
    mat = campus["subjects"]["MAT-101"]
    ana = campus["students"]["Ana"]["id"]
    teacher = campus["teachers"]["Pedro Profe"]
    await client.post(f"/semesters/{mat['semester']['id']}/close", headers=coord)

    grades = await client.put(
        f"/teacher/subjects/{mat['id']}/grades",
        json={"grades": [{"student_id": ana, "term": 3, "value": 12}]},
        headers=teacher,
    )
    assert grades.status_code == 409
    assert "El semestre 2026-I está cerrado" in grades.json()["detail"]

    upload = await client.post(
        f"/teacher/subjects/{mat['id']}/grades/import",
        files={"file": ("n.xlsx", b"PK...", "application/octet-stream")},
        headers=teacher,
    )
    assert upload.status_code == 409

    # Se pueden consultar, no modificar
    assert (
        await client.get(f"/teacher/subjects/{mat['id']}/grades", headers=teacher)
    ).status_code == 200
    subjects = (await client.get("/teacher/subjects", headers=teacher)).json()
    assert next(s for s in subjects if s["id"] == mat["id"])["semester"]["is_closed"] is True

    beto = campus["students"]["Beto"]["id"]
    enroll = await client.post(
        f"/students/{beto}/subjects",
        json={"subject_ids": [campus["subjects"]["FIS-101"]["id"]]},
        headers=coord,
    )
    assert enroll.status_code == 409
    unenroll = await client.delete(f"/students/{beto}/subjects/{mat['id']}", headers=coord)
    assert unenroll.status_code == 409

    new_subject = await client.post(
        "/subjects",
        json={"semester_id": mat["semester"]["id"], "code": "QUI-101", "name": "Química"},
        headers=coord,
    )
    assert new_subject.status_code == 409

    # Reabierto vuelve a permitirlo
    await client.post(f"/semesters/{mat['semester']['id']}/reopen", headers=coord)
    grades = await client.put(
        f"/teacher/subjects/{mat['id']}/grades",
        json={"grades": [{"student_id": ana, "term": 3, "value": 12}]},
        headers=teacher,
    )
    assert grades.status_code == 200
