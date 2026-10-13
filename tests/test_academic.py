import pytest

from app.models import Role


@pytest.fixture
async def coord(login_as):
    return await login_as(Role.COORDINATOR)


async def create_semester(client, headers, name="2026-I") -> dict:
    response = await client.post("/semesters", json={"name": name}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def create_subject(client, headers, semester_id, code="mat-101", name="Matemática I"):
    response = await client.post(
        "/subjects",
        json={"semester_id": semester_id, "code": code, "name": name},
        headers=headers,
    )
    return response


# --- Permisos ----------------------------------------------------------------


@pytest.mark.parametrize(("role", "expected"), [(Role.TEACHER, 403), (Role.STUDENT, 403)])
async def test_solo_coordinacion_gestiona(client, login_as, role, expected):
    headers = await login_as(role)
    assert (await client.get("/semesters", headers=headers)).status_code == expected
    assert (await client.get("/subjects", headers=headers)).status_code == expected


async def test_super_admin_tambien_gestiona(client, login_as):
    headers = await login_as(Role.SUPER_ADMIN)
    await create_semester(client, headers)


# --- Semestres ---------------------------------------------------------------


async def test_crud_semestre(client, coord):
    semester = await create_semester(client, coord, "  2026-I  ")
    assert semester["name"] == "2026-I"

    renamed = await client.patch(
        f"/semesters/{semester['id']}", json={"name": "2026-II"}, headers=coord
    )
    assert renamed.json()["name"] == "2026-II"

    listed = (await client.get("/semesters", headers=coord)).json()
    assert [s["name"] for s in listed] == ["2026-II"]

    assert (await client.delete(f"/semesters/{semester['id']}", headers=coord)).status_code == 204
    assert (await client.get(f"/semesters/{semester['id']}", headers=coord)).status_code == 404


async def test_semestre_duplicado(client, coord):
    await create_semester(client, coord, "2026-I")
    other = await create_semester(client, coord, "2026-II")
    dup = await client.post("/semesters", json={"name": "2026-I"}, headers=coord)
    assert dup.status_code == 409
    rename = await client.patch(f"/semesters/{other['id']}", json={"name": "2026-I"}, headers=coord)
    assert rename.status_code == 409


async def test_no_se_borra_semestre_con_materias(client, coord):
    semester = await create_semester(client, coord)
    await create_subject(client, coord, semester["id"])
    response = await client.delete(f"/semesters/{semester['id']}", headers=coord)
    assert response.status_code == 409


# --- Materias ----------------------------------------------------------------


async def test_crear_materia_normaliza_codigo(client, coord):
    semester = await create_semester(client, coord)
    response = await create_subject(client, coord, semester["id"], code=" mat-101 ")
    assert response.status_code == 201
    body = response.json()
    assert body["code"] == "MAT-101"
    assert body["semester"] == semester


async def test_materia_codigo_duplicado_y_semestre_inexistente(client, coord):
    semester = await create_semester(client, coord)
    await create_subject(client, coord, semester["id"], code="MAT-101")
    assert (await create_subject(client, coord, semester["id"], code="mat-101")).status_code == 409
    assert (await create_subject(client, coord, 999, code="FIS-101")).status_code == 404


async def test_listar_materias_filtra_y_pagina(client, coord):
    s1 = await create_semester(client, coord, "2026-I")
    s2 = await create_semester(client, coord, "2026-II")
    await create_subject(client, coord, s1["id"], "MAT-101", "Matemática I")
    await create_subject(client, coord, s1["id"], "FIS-101", "Física I")
    await create_subject(client, coord, s2["id"], "MAT-201", "Matemática II")

    by_semester = (await client.get(f"/subjects?semester_id={s1['id']}", headers=coord)).json()
    assert by_semester["total"] == 2
    assert [s["code"] for s in by_semester["items"]] == ["FIS-101", "MAT-101"]

    search = (await client.get("/subjects?q=matem", headers=coord)).json()
    assert search["total"] == 2

    page = (await client.get("/subjects?limit=1&offset=1", headers=coord)).json()
    assert page["total"] == 3
    assert [s["code"] for s in page["items"]] == ["MAT-101"]


async def test_modificar_materia(client, coord):
    s1 = await create_semester(client, coord, "2026-I")
    s2 = await create_semester(client, coord, "2026-II")
    subject = (await create_subject(client, coord, s1["id"])).json()

    response = await client.patch(
        f"/subjects/{subject['id']}",
        json={"semester_id": s2["id"], "name": "Cálculo I"},
        headers=coord,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Cálculo I"
    assert body["semester"]["name"] == "2026-II"
    assert body["code"] == "MAT-101"


async def test_eliminar_materia(client, coord):
    semester = await create_semester(client, coord)
    subject = (await create_subject(client, coord, semester["id"])).json()
    assert (await client.delete(f"/subjects/{subject['id']}", headers=coord)).status_code == 204
    assert (await client.get(f"/subjects/{subject['id']}", headers=coord)).status_code == 404
