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
    pedro = campus["teachers"]["Pedro Profe"]
    entries = [
        {"student_id": ana["id"], "term": 1, "value": 16},
        {"student_id": ana["id"], "term": 2, "value": None},
        {"student_id": ana["id"], "term": 3, "value": 12.5},
    ]
    await client.put(f"{campus['urls']['MAT-101']}/grades", json={"grades": entries}, headers=pedro)
    latest = (await notifications(client, ana["headers"]))["items"][0]
    assert latest["title"] == "Nuevas notas en Mate I"
    assert latest["body"] == "Lapso 1: 16 · Lapso 3: 12.5"

    only_delete = [{"student_id": ana["id"], "term": 3, "value": None}]
    await client.put(
        f"{campus['urls']['MAT-101']}/grades", json={"grades": only_delete}, headers=pedro
    )
    assert (await notifications(client, ana["headers"]))["total"] == 4


async def test_avisa_de_las_evaluaciones(client, campus):
    ana = campus["students"]["Ana"]
    pedro = campus["teachers"]["Pedro Profe"]
    response = await client.post(
        f"{campus['urls']['FIS-101']}/evaluations",
        json={"term": 2, "name": "Examen", "weight": 100},
        headers=pedro,
    )
    examen = response.json()["id"]
    await client.put(
        f"{campus['urls']['FIS-101']}/terms/2/evaluation-grades",
        json={"grades": [{"student_id": ana["id"], "evaluation_id": examen, "value": 14}]},
        headers=pedro,
    )
    latest = (await notifications(client, ana["headers"]))["items"][0]
    assert latest["body"] == "Examen (Lapso 2): 14 · Lapso 2: 14"

    # Cambiar el porcentaje deja el lapso sin nota: no hay aviso nuevo
    total = (await notifications(client, ana["headers"]))["total"]
    await client.patch(
        f"{campus['urls']['FIS-101']}/evaluations/{examen}",
        json={"weight": 50},
        headers=pedro,
    )
    assert (await notifications(client, ana["headers"]))["total"] == total
    # Volver al 100 % recalcula y avisa de la nota del lapso
    await client.patch(
        f"{campus['urls']['FIS-101']}/evaluations/{examen}",
        json={"weight": 100},
        headers=pedro,
    )
    assert (await notifications(client, ana["headers"]))["items"][0]["body"] == "Lapso 2: 14"
    await client.delete(f"{campus['urls']['FIS-101']}/evaluations/{examen}", headers=pedro)
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


# --- Tiempo real (SSE) -------------------------------------------------------------


def sse_events(body: str) -> list[tuple[str | None, str | None]]:
    """(evento, datos) de cada bloque de un text/event-stream (sin los ping)."""
    events = []
    for block in body.strip().split("\n\n"):
        event = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = line[6:]
        if event:
            events.append((event, data))
    return events


async def open_stream(client, headers):
    """Abre la conexión en segundo plano (el cliente de pruebas recibe el cuerpo al cerrarse)."""
    import asyncio

    return asyncio.create_task(client.get("/student/notifications/stream", headers=headers))


async def wait_connected(user_id, count=1):
    import asyncio

    from app.services.notification_stream import broker

    for _ in range(200):
        if broker.connections(user_id) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("el estudiante no llegó a conectarse")


async def test_el_aviso_llega_en_tiempo_real_solo_a_su_estudiante(client, campus, monkeypatch):
    import json

    from app.api.v1.routes import student as student_routes

    monkeypatch.setattr(student_routes, "STREAM_MAX_SECONDS", 0.6)
    monkeypatch.setattr(student_routes, "STREAM_PING_SECONDS", 0.2)
    ana, beto = campus["students"]["Ana"], campus["students"]["Beto"]
    ana_stream = await open_stream(client, ana["headers"])
    beto_stream = await open_stream(client, beto["headers"])
    await wait_connected(ana["id"])
    await wait_connected(beto["id"])

    await client.put(
        f"{campus['urls']['MAT-101']}/grades",
        json={"grades": [{"student_id": ana["id"], "term": 3, "value": 17}]},
        headers=campus["teachers"]["Pedro Profe"],
    )

    ana_response = await ana_stream
    assert ana_response.status_code == 200
    assert ana_response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(ana_response.text)
    assert [e for e, _ in events] == ["ready", "notification"]
    notice = json.loads(events[1][1])
    assert notice["title"] == "Nueva nota en Mate I"
    assert notice["body"] == "Lapso 3: 17"
    assert notice["read_at"] is None
    assert ": ping" in ana_response.text

    # Beto estaba conectado a la vez, pero la nota no era suya
    beto_events = sse_events((await beto_stream).text)
    assert [e for e, _ in beto_events] == ["ready"]

    from app.services.notification_stream import broker

    assert broker.connections(ana["id"]) == 0  # la conexión se libera al cerrarse


async def test_el_stream_exige_sesion_de_estudiante(client, login_as):
    assert (await client.get("/student/notifications/stream")).status_code == 401
    teacher = await login_as(Role.TEACHER)
    response = await client.get("/student/notifications/stream", headers=teacher)
    assert response.status_code == 403


def test_una_cola_llena_no_bloquea_al_resto():
    import asyncio

    from app.models import Notification
    from app.services.notification_stream import QUEUE_SIZE, NotificationBroker

    broker = NotificationBroker()
    from datetime import datetime

    notice = Notification(
        id=1, user_id=7, title="t", body="b", subject_id=None, created_at=datetime(2026, 1, 1)
    )
    with broker.subscribe(7) as full, broker.subscribe(7) as other:
        for _ in range(QUEUE_SIZE):
            full.put_nowait({})
        broker.publish([notice])
        assert full.qsize() == QUEUE_SIZE
        assert other.qsize() == 1
        broker.publish([Notification(id=2, user_id=8, title="t", body="b")])  # nadie conectado
    assert broker.connections(7) == 0
    assert isinstance(other, asyncio.Queue)
    assert other.get_nowait()["created_at"].startswith("2026-01-01")
