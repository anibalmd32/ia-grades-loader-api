from app.models import Role
from tests.conftest import class_url


async def put(client, campus, entries, subject="MAT-101", teacher="Pedro Profe", **extra):
    response = await client.put(
        f"{campus['urls'][subject]}/grades",
        json={"grades": entries, **extra},
        headers=campus["teachers"][teacher],
    )
    assert response.status_code == 200, response.text


async def test_registra_altas_correcciones_y_borrados(client, campus):
    ana = campus["students"]["Ana"]["id"]
    mat = campus["subjects"]["MAT-101"]["id"]
    # La fixture ya cargó 15 y 18.5 (Ana) y 20 (Beto) en MAT-101
    await put(client, campus, [{"student_id": ana, "term": 1, "value": 16}])
    await put(client, campus, [{"student_id": ana, "term": 2, "value": None}])
    # Sin cambios reales no se registra nada
    await put(client, campus, [{"student_id": ana, "term": 1, "value": 16}])

    teacher = campus["teachers"]["Pedro Profe"]
    history_url = f"{campus['urls']['MAT-101']}/grade-history"
    response = await client.get(history_url, headers=teacher)
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    latest, correction = body["items"][:2]
    assert (latest["term"], latest["old_value"], latest["new_value"]) == (2, 18.5, None)
    assert (correction["old_value"], correction["new_value"]) == (15, 16)
    assert correction["changed_by"] == "Pedro Profe"
    assert correction["source"] == "manual"
    assert correction["student"]["first_name"] == "Ana"

    only_beto = await client.get(
        history_url,
        params={"student_id": campus["students"]["Beto"]["id"]},
        headers=teacher,
    )
    assert [c["new_value"] for c in only_beto.json()["items"]] == [20]

    # Laura da MAT-101 en la sección B: no ve los cambios de los estudiantes de la A
    laura = campus["teachers"]["Laura Profe"]
    other = class_url(mat, campus["sections"]["2026-I B"])
    response = await client.get(f"{other}/grade-history", headers=laura)
    assert response.status_code == 200
    assert response.json()["total"] == 0


async def test_cambios_desde_importacion(client, campus, session):
    from app.models import ImportJob, JobStatus

    pedro = campus["teachers"]["Pedro Profe"]
    teacher_id = (await client.get("/auth/me", headers=pedro)).json()["id"]
    mat = campus["subjects"]["MAT-101"]["id"]
    job = ImportJob(
        teacher_id=teacher_id,
        subject_id=mat,
        section_id=campus["sections"]["2026-I"],
        status=JobStatus.DONE,
        file_name="notas.xlsx",
        result={},
    )
    session.add(job)
    await session.commit()
    ana = campus["students"]["Ana"]["id"]
    await put(client, campus, [{"student_id": ana, "term": 3, "value": 12}], import_job_id=job.id)

    url = f"{campus['urls']['MAT-101']}/grade-history"
    body = (await client.get(url, headers=pedro)).json()
    assert body["items"][0]["source"] == "import"
    assert body["items"][0]["import_job_id"] == job.id


async def test_permisos(client, campus, login_as):
    mat = campus["subjects"]["MAT-101"]["id"]
    # Laura no da Física
    laura = campus["teachers"]["Laura Profe"]
    assert (
        await client.get(f"{campus['urls']['FIS-101']}/grade-history", headers=laura)
    ).status_code == 404
    student = campus["students"]["Ana"]["headers"]
    assert (
        await client.get(f"{campus['urls']['MAT-101']}/grade-history", headers=student)
    ).status_code == 403

    coord = await login_as(Role.COORDINATOR, email="coordina@uni.edu")
    response = await client.get(f"/subjects/{mat}/grade-history?limit=2", headers=coord)
    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert len(response.json()["items"]) == 2
    assert (await client.get(f"/subjects/{mat}/grade-history", headers=laura)).status_code == 403
