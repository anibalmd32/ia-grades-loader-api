from app.models import Role


async def notifications(client, headers, **params):
    response = await client.get("/student/notifications", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def test_avisa_al_estudiante_de_sus_notas_nuevas(client, campus):
    # La fixture cargó a Ana 15 y 18.5 en MAT-101 (dos cargas) y 10 en FIS-101
    ana = campus["students"]["Ana"]
    body = await notifications(client, ana["headers"])
    assert body["unread"] == body["total"] == 3
    latest = body["items"][0]
    assert latest["title"] == "Nueva nota en Física"
    assert latest["body"] == "Lapso 1: 10"
    assert latest["subject_id"] == campus["subjects"]["FIS-101"]["id"]
    assert latest["read_at"] is None
    assert [n["id"] for n in body["items"]] == sorted(
        (n["id"] for n in body["items"]), reverse=True
    )

    # Beto solo ve los suyos
    beto = await notifications(client, campus["students"]["Beto"]["headers"])
    assert [n["body"] for n in beto["items"]] == ["Lapso 1: 20"]


async def test_varias_notas_en_un_aviso_y_sin_aviso_al_borrar(client, campus):
    ana = campus["students"]["Ana"]
    mat = campus["subjects"]["MAT-101"]["id"]
    pedro = campus["teachers"]["Pedro Profe"]
    entries = [
        {"student_id": ana["id"], "term": 1, "value": 16},
        {"student_id": ana["id"], "term": 2, "value": None},
        {"student_id": ana["id"], "term": 3, "value": 12.5},
    ]
    await client.put(f"/teacher/subjects/{mat}/grades", json={"grades": entries}, headers=pedro)
    latest = (await notifications(client, ana["headers"]))["items"][0]
    assert latest["title"] == "Nuevas notas en Mate I"
    assert latest["body"] == "Lapso 1: 16 · Lapso 3: 12.5"

    only_delete = [{"student_id": ana["id"], "term": 3, "value": None}]
    await client.put(f"/teacher/subjects/{mat}/grades", json={"grades": only_delete}, headers=pedro)
    assert (await notifications(client, ana["headers"]))["total"] == 4


async def test_avisa_de_las_evaluaciones(client, campus):
    ana = campus["students"]["Ana"]
    pedro = campus["teachers"]["Pedro Profe"]
    fis = campus["subjects"]["FIS-101"]["id"]
    response = await client.post(
        f"/teacher/subjects/{fis}/evaluations",
        json={"term": 2, "name": "Examen", "weight": 100},
        headers=pedro,
    )
    examen = response.json()["id"]
    await client.put(
        f"/teacher/subjects/{fis}/terms/2/evaluation-grades",
        json={"grades": [{"student_id": ana["id"], "evaluation_id": examen, "value": 14}]},
        headers=pedro,
    )
    latest = (await notifications(client, ana["headers"]))["items"][0]
    assert latest["body"] == "Examen (Lapso 2): 14 · Lapso 2: 14"

    # Cambiar el porcentaje deja el lapso sin nota: no hay aviso nuevo
    total = (await notifications(client, ana["headers"]))["total"]
    await client.patch(
        f"/teacher/subjects/{fis}/evaluations/{examen}",
        json={"weight": 50},
        headers=pedro,
    )
    assert (await notifications(client, ana["headers"]))["total"] == total
    # Volver al 100 % recalcula y avisa de la nota del lapso
    await client.patch(
        f"/teacher/subjects/{fis}/evaluations/{examen}",
        json={"weight": 100},
        headers=pedro,
    )
    assert (await notifications(client, ana["headers"]))["items"][0]["body"] == "Lapso 2: 14"
    await client.delete(f"/teacher/subjects/{fis}/evaluations/{examen}", headers=pedro)
    assert (await notifications(client, ana["headers"]))["total"] == total + 1


async def test_marcar_como_leidos(client, campus):
    ana = campus["students"]["Ana"]
    items = (await notifications(client, ana["headers"]))["items"]
    response = await client.post(
        "/student/notifications/read", json={"ids": [items[0]["id"]]}, headers=ana["headers"]
    )
    assert response.json() == {"unread": 2}
    unread = await notifications(client, ana["headers"], unread_only=True)
    assert unread["total"] == 2
    assert items[0]["id"] not in [n["id"] for n in unread["items"]]

    # Los de otro estudiante no se tocan
    beto = campus["students"]["Beto"]
    beto_ids = [n["id"] for n in (await notifications(client, beto["headers"]))["items"]]
    await client.post("/student/notifications/read", json={"ids": beto_ids}, headers=ana["headers"])
    assert (await notifications(client, beto["headers"]))["unread"] == 1

    response = await client.post("/student/notifications/read", json={}, headers=ana["headers"])
    assert response.json() == {"unread": 0}
    page = await notifications(client, ana["headers"], limit=1, offset=1)
    assert (page["total"], len(page["items"]), page["unread"]) == (3, 1, 0)
    assert page["items"][0]["read_at"] is not None


async def test_solo_estudiantes(client, login_as):
    teacher = await login_as(Role.TEACHER)
    assert (await client.get("/student/notifications", headers=teacher)).status_code == 403
    assert (await client.get("/student/notifications")).status_code == 401
