"""Secciones: un estudiante cursa cada periodo en una sola sección y cada materia tiene un
solo profesor por sección, así que nunca tiene dos profesores en la misma materia."""

import pytest

from app.models import Role
from tests.conftest import active_token, assign_section, assign_teacher, class_url, create_section
from tests.factories import names


@pytest.fixture
async def school(client, login_as, session):
    """2026-I con secciones A y B, MAT-101; Pedro la da en la A y Laura en la B. Ana en la
    A, Beto en la B, ambos matriculados."""
    coord = await login_as(Role.COORDINATOR, email="coord@uni.edu")

    async def post(url, payload):
        response = await client.post(url, json=payload, headers=coord)
        assert response.status_code in (200, 201), response.text
        return response.json()

    semester = await post("/semesters", {"name": "2026-I"})
    a = await create_section(client, coord, semester["id"], "a ")  # se normaliza a "A"
    b = await create_section(client, coord, semester["id"], "B")
    mat = await post("/subjects", {"semester_id": semester["id"], "code": "MAT-101", "name": "M"})
    teachers, ids = {}, {}
    for name, email, section in [("Pedro Profe", "p@uni.edu", a), ("Laura Profe", "l@uni.edu", b)]:
        teacher = await post("/teachers", {**names(name), "email": email})
        ids[name] = teacher["user"]["id"]
        await assign_teacher(client, coord, ids[name], [(mat["id"], section)])
        token = await active_token(session, ids[name])
        teachers[name] = {"Authorization": f"Bearer {token}"}
    students = {}
    for name, cedula, section in [("Ana", 1, a), ("Beto", 2, b)]:
        created = await post("/students", {**names(name), "nationality": "V", "cedula": cedula})
        students[name] = created["user"]["id"]
        await assign_section(client, coord, section, [students[name]])
        await post(f"/students/{students[name]}/subjects", {"subject_ids": [mat["id"]]})
    return {
        "coord": coord,
        "semester": semester,
        "sections": {"A": a, "B": b},
        "mat": mat,
        "teachers": teachers,
        "teacher_ids": ids,
        "students": students,
    }


async def test_crear_listar_renombrar_y_borrar_secciones(client, school):
    coord, semester = school["coord"], school["semester"]
    listed = (await client.get(f"/semesters/{semester['id']}/sections", headers=coord)).json()
    assert [(s["name"], s["students_count"]) for s in listed] == [("A", 1), ("B", 1)]

    repeated = await client.post(
        f"/semesters/{semester['id']}/sections", json={"name": "b"}, headers=coord
    )
    assert repeated.status_code == 409

    c = await create_section(client, coord, semester["id"], "C")
    renamed = await client.patch(f"/sections/{c}", json={"name": "D"}, headers=coord)
    assert renamed.json()["name"] == "D"
    assert (await client.delete(f"/sections/{c}", headers=coord)).status_code == 204

    # Con estudiantes o con profesores no se borra
    with_students = await client.delete(f"/sections/{school['sections']['A']}", headers=coord)
    assert with_students.status_code == 409
    empty_with_teacher = await create_section(client, coord, semester["id"], "E")
    await assign_teacher(
        client,
        coord,
        school["teacher_ids"]["Laura Profe"],
        [(school["mat"]["id"], school["sections"]["B"]), (school["mat"]["id"], empty_with_teacher)],
    )
    response = await client.delete(f"/sections/{empty_with_teacher}", headers=coord)
    assert response.status_code == 409
    assert "profesores" in response.json()["detail"]


async def test_un_solo_profesor_por_materia_y_seccion(client, school):
    coord, mat = school["coord"], school["mat"]
    # Laura no puede dar MAT-101 en la sección A: ya la da Pedro
    response = await client.put(
        f"/teachers/{school['teacher_ids']['Laura Profe']}/subjects",
        json={"assignments": [{"subject_id": mat["id"], "section_id": school["sections"]["A"]}]},
        headers=coord,
    )
    assert response.status_code == 409
    assert "Pedro Profe" in response.json()["detail"]

    teachers = (await client.get(f"/subjects/{mat['id']}/teachers", headers=coord)).json()
    assert [(t["section"]["name"], t["teacher"]["full_name"]) for t in teachers] == [
        ("A", "Pedro Profe"),
        ("B", "Laura Profe"),
    ]
    detail = (await client.get(f"/subjects/{mat['id']}", headers=coord)).json()
    assert detail["teachers_count"] == 2


async def test_la_seccion_debe_ser_del_periodo_de_la_materia(client, school):
    coord = school["coord"]
    other = (await client.post("/semesters", json={"name": "2026-II"}, headers=coord)).json()
    section = await create_section(client, coord, other["id"])
    response = await client.put(
        f"/teachers/{school['teacher_ids']['Pedro Profe']}/subjects",
        json={"assignments": [{"subject_id": school["mat"]["id"], "section_id": section}]},
        headers=coord,
    )
    assert response.status_code == 422
    missing = await client.put(
        f"/teachers/{school['teacher_ids']['Pedro Profe']}/subjects",
        json={"assignments": [{"subject_id": school["mat"]["id"], "section_id": 999}]},
        headers=coord,
    )
    assert missing.status_code == 404


async def test_sin_seccion_no_se_matricula(client, school):
    coord = school["coord"]
    created = await client.post(
        "/students", json={**names("Carla Díaz"), "nationality": "V", "cedula": 3}, headers=coord
    )
    carla = created.json()["user"]["id"]
    response = await client.post(
        f"/students/{carla}/subjects", json={"subject_ids": [school["mat"]["id"]]}, headers=coord
    )
    assert response.status_code == 409
    assert "Carla Díaz (2026-I)" in response.json()["detail"]

    await assign_section(client, coord, school["sections"]["A"], [carla])
    response = await client.post(
        f"/students/{carla}/subjects", json={"subject_ids": [school["mat"]["id"]]}, headers=coord
    )
    assert response.json() == {"added": 1, "already_enrolled": 0}
    sections = (await client.get(f"/students/{carla}/sections", headers=coord)).json()
    assert [(s["semester"]["name"], s["name"]) for s in sections] == [("2026-I", "A")]


async def test_cambiar_de_seccion_solo_sin_notas(client, school):
    coord, sections = school["coord"], school["sections"]
    ana, beto = school["students"]["Ana"], school["students"]["Beto"]
    # Ana pasa a la B (sin notas todavía) y Beto ya estaba
    response = await client.post(
        f"/sections/{sections['B']}/students", json={"student_ids": [ana, beto]}, headers=coord
    )
    assert response.json() == {"added": 0, "moved": 1, "already_in_section": 1}
    students = (await client.get(f"/sections/{sections['B']}/students", headers=coord)).json()
    assert sorted(s["first_name"] for s in students) == ["Ana", "Beto"]

    # Con notas en el periodo ya no se cambia
    laura = school["teachers"]["Laura Profe"]
    graded = await client.put(
        f"{class_url(school['mat']['id'], sections['B'])}/grades",
        json={"grades": [{"student_id": ana, "term": 1, "value": 15}]},
        headers=laura,
    )
    assert graded.status_code == 200, graded.text
    response = await client.post(
        f"/sections/{sections['A']}/students", json={"student_ids": [ana]}, headers=coord
    )
    assert response.status_code == 409
    assert "Ana" in response.json()["detail"]

    # Tampoco se quita de la sección mientras esté matriculado en el periodo
    removed = await client.delete(f"/sections/{sections['B']}/students/{beto}", headers=coord)
    assert removed.status_code == 409
    await client.delete(f"/students/{beto}/subjects/{school['mat']['id']}", headers=coord)
    removed = await client.delete(f"/sections/{sections['B']}/students/{beto}", headers=coord)
    assert removed.status_code == 204


async def test_cada_profesor_ve_solo_su_seccion(client, school):
    mat, sections = school["mat"], school["sections"]
    pedro, laura = school["teachers"]["Pedro Profe"], school["teachers"]["Laura Profe"]

    mine = (await client.get("/teacher/subjects", headers=pedro)).json()
    assert [(s["code"], s["section"]["name"], s["students_count"]) for s in mine] == [
        ("MAT-101", "A", 1)
    ]
    sheet = await client.get(f"{class_url(mat['id'], sections['A'])}/grades", headers=pedro)
    assert [s["student"]["first_name"] for s in sheet.json()["students"]] == ["Ana"]
    assert sheet.json()["section"]["name"] == "A"

    # La sección de Laura no es suya
    other = await client.get(f"{class_url(mat['id'], sections['B'])}/grades", headers=pedro)
    assert other.status_code == 404
    # Ni puede poner notas a un estudiante de otra sección
    response = await client.put(
        f"{class_url(mat['id'], sections['B'])}/grades",
        json={"grades": [{"student_id": school["students"]["Ana"], "term": 1, "value": 15}]},
        headers=laura,
    )
    assert response.status_code == 404
    assert "en esta sección" in response.json()["detail"]


async def test_el_estudiante_tiene_un_solo_profesor_por_materia(client, school, session):
    token = await active_token(session, school["students"]["Ana"])
    body = (
        await client.get("/student/subjects", headers={"Authorization": f"Bearer {token}"})
    ).json()
    (subject,) = body["subjects"]
    assert (subject["section"], subject["teacher"]) == ("A", "Pedro Profe")
    assert "teachers" not in subject


async def test_cada_seccion_tiene_su_plan_de_evaluacion(client, school, session):
    mat, sections = school["mat"], school["sections"]
    pedro, laura = school["teachers"]["Pedro Profe"], school["teachers"]["Laura Profe"]
    created = await client.post(
        f"{class_url(mat['id'], sections['A'])}/evaluations",
        json={"term": 1, "name": "Parcial", "weight": 100},
        headers=pedro,
    )
    assert created.status_code == 201
    plan_b = (
        await client.get(f"{class_url(mat['id'], sections['B'])}/evaluations", headers=laura)
    ).json()
    assert plan_b["evaluations"] == []
    # Laura puede cargar a mano el lapso 1 de su sección: el plan de Pedro no le afecta
    beto = school["students"]["Beto"]
    response = await client.put(
        f"{class_url(mat['id'], sections['B'])}/grades",
        json={"grades": [{"student_id": beto, "term": 1, "value": 12}]},
        headers=laura,
    )
    assert response.status_code == 200
    assert response.json()["computed_terms"] == []

    token = await active_token(session, beto)
    body = (
        await client.get("/student/subjects", headers={"Authorization": f"Bearer {token}"})
    ).json()
    assert body["subjects"][0]["evaluations"] == []
