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
        f"{campus['urls']['MAT-101']}/grades",
        json={"grades": [{"student_id": ana, "term": 3, "value": 12}]},
        headers=teacher,
    )
    assert grades.status_code == 409
    assert "El semestre 2026-I está cerrado" in grades.json()["detail"]

    upload = await client.post(
        f"{campus['urls']['MAT-101']}/grades/import",
        files={"file": ("n.xlsx", b"PK...", "application/octet-stream")},
        headers=teacher,
    )
    assert upload.status_code == 409

    # Se pueden consultar, no modificar
    assert (
        await client.get(f"{campus['urls']['MAT-101']}/grades", headers=teacher)
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
        f"{campus['urls']['MAT-101']}/grades",
        json={"grades": [{"student_id": ana, "term": 3, "value": 12}]},
        headers=teacher,
    )
    assert grades.status_code == 200


# --- Reglas congeladas ------------------------------------------------------------

NEW_RULES = {
    "institution_name": "Universidad",
    "period_type": "semester",
    "terms_count": 4,
    "term_label": "Corte",
    "term_weights": None,
    "min_grade": 0,
    "max_grade": 100,
    "passing_grade": 60,
    "grade_decimals": 0,
    "round_final_grade": False,
    "document_label": "Cédula",
    "document_types": [{"code": "V", "name": "Venezolano"}, {"code": "E", "name": "Extranjero"}],
    "assistant_enabled": True,
    "telegram_enabled": True,
}


async def test_cerrar_congela_las_reglas_y_cambiar_la_configuracion_no_lo_afecta(
    client, coord, campus, login_as
):
    admin = await login_as(Role.SUPER_ADMIN, email="admin@uni.edu")
    mat = campus["subjects"]["MAT-101"]
    teacher = campus["teachers"]["Pedro Profe"]
    ana = campus["students"]["Ana"]

    # Con notas 1-20 cargadas en 2026-I no se puede pasar a 0-100... salvo que se cierre
    assert (
        await client.put("/settings/academic", json=NEW_RULES, headers=admin)
    ).status_code == 409
    closed = (await client.post(f"/semesters/{mat['semester']['id']}/close", headers=coord)).json()
    assert closed["rules"]["terms_count"] == 3
    assert closed["rules"]["summary"].startswith(
        "La definitiva es el promedio simple de los 3 lapsos"
    )
    changed = await client.put("/settings/academic", json=NEW_RULES, headers=admin)
    assert changed.status_code == 200, changed.text

    # El periodo cerrado se sigue viendo con sus reglas; el abierto, con las nuevas
    old = (await client.get(f"{campus['urls']['MAT-101']}/grades", headers=teacher)).json()
    assert [g["term"] for g in old["students"][0]["grades"]] == [1, 2, 3]
    student = (await client.get("/student/subjects", headers=ana["headers"])).json()
    terms = {s["subject"]["code"]: len(s["grades"]) for s in student["subjects"]}
    assert terms == {"FIS-101": 3, "MAT-101": 3, "MAT-201": 4}
    mat101 = next(s for s in student["subjects"] if s["subject"]["code"] == "MAT-101")
    assert mat101["subject"]["semester"]["rules"]["max_grade"] == 20
    mat201 = next(s for s in student["subjects"] if s["subject"]["code"] == "MAT-201")
    assert mat201["subject"]["semester"]["rules"] is None
    # MAT-101: 15 y 18.5 de 20 -> sigue asegurada con la escala vieja
    assert mat101["projection"]["standing"] == "secured"

    # Reabierto conserva sus reglas: valida con 1-20 y 3 lapsos
    await client.post(f"/semesters/{mat['semester']['id']}/reopen", headers=coord)
    url = f"{campus['urls']['MAT-101']}/grades"
    entry = {"student_id": ana["id"], "term": 3, "value": 50}
    assert (await client.put(url, json={"grades": [entry]}, headers=teacher)).status_code == 422
    entry = {"student_id": ana["id"], "term": 4, "value": 15}
    assert (await client.put(url, json={"grades": [entry]}, headers=teacher)).status_code == 422
    entry = {"student_id": ana["id"], "term": 3, "value": 15.5}
    assert (await client.put(url, json={"grades": [entry]}, headers=teacher)).status_code == 200

    subjects = (await client.get("/teacher/subjects", headers=teacher)).json()
    assert {s["code"]: len(s["grades_count"]) for s in subjects} == {"FIS-101": 3, "MAT-101": 3}
