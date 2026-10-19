from app.models import Role


def url(course, path=""):
    return f"/teacher/subjects/{course['subject_id']}{path}"


async def add(client, course, term=1, name="Parcial 1", weight=50, **extra):
    response = await client.post(
        url(course, "/evaluations"),
        json={"term": term, "name": name, "weight": weight, **extra},
        headers=course["headers"],
    )
    assert response.status_code == 201, response.text
    return response.json()


async def grade(client, course, term, entries, status=200):
    response = await client.put(
        url(course, f"/terms/{term}/evaluation-grades"),
        json={"grades": entries},
        headers=course["headers"],
    )
    assert response.status_code == status, response.text
    return response.json()


def entry(course, student, evaluation, value):
    return {
        "student_id": course["students"][student],
        "evaluation_id": evaluation["id"],
        "value": value,
    }


def by_name(sheet, first_name):
    return next(s for s in sheet["students"] if s["student"]["first_name"] == first_name)


async def gradebook(client, course):
    return (await client.get(url(course, "/grades"), headers=course["headers"])).json()


async def test_plan_de_evaluacion_por_lapso(client, course):
    first = await add(client, course, name="Parcial 1", weight=40, due_date="2026-11-02")
    await add(client, course, name="Taller", weight=60)
    await add(client, course, term=2, name="Exposición", weight=30)

    plan = (await client.get(url(course, "/evaluations"), headers=course["headers"])).json()
    assert [(e["term"], e["name"], e["weight"]) for e in plan["evaluations"]] == [
        (1, "Parcial 1", 40),
        (1, "Taller", 60),
        (2, "Exposición", 30),
    ]
    assert plan["evaluations"][0]["due_date"] == "2026-11-02"
    assert [(t["term"], t["weights_total"], t["complete"]) for t in plan["terms"]] == [
        (1, 100, True),
        (2, 30, False),
        (3, 0, False),
    ]
    assert first["graded_count"] == 0
    assert (await gradebook(client, course))["computed_terms"] == [1, 2]


async def test_los_porcentajes_no_pasan_del_100(client, course):
    await add(client, course, weight=70)
    response = await client.post(
        url(course, "/evaluations"),
        json={"term": 1, "name": "Otra", "weight": 40},
        headers=course["headers"],
    )
    assert response.status_code == 422
    assert "110 %" in response.json()["detail"]

    repeated = await client.post(
        url(course, "/evaluations"),
        json={"term": 1, "name": "parcial 1", "weight": 10},
        headers=course["headers"],
    )
    assert repeated.status_code == 409

    out_of_terms = await client.post(
        url(course, "/evaluations"),
        json={"term": 4, "name": "Extra", "weight": 10},
        headers=course["headers"],
    )
    assert out_of_terms.status_code == 422


async def test_la_nota_del_lapso_se_calcula_con_las_evaluaciones(client, course):
    parcial = await add(client, course, name="Parcial", weight=40)
    taller = await add(client, course, name="Taller", weight=60)

    sheet = await grade(client, course, 1, [entry(course, "Ana Pérez", parcial, 15)])
    ana = by_name(sheet, "Ana")
    assert ana["grades"] == [
        {"evaluation_id": parcial["id"], "value": 15},
        {"evaluation_id": taller["id"], "value": None},
    ]
    assert (ana["accumulated"], ana["term_grade"]) == (6, None)
    assert sheet["evaluations"][0]["graded_count"] == 1

    sheet = await grade(client, course, 1, [entry(course, "Ana Pérez", taller, 18.5)])
    # 15 × 40 % + 18.5 × 60 % = 6 + 11.1
    assert by_name(sheet, "Ana")["term_grade"] == 17.1
    book = await gradebook(client, course)
    ana_row = next(s for s in book["students"] if s["student"]["first_name"] == "Ana")
    assert ana_row["grades"][0] == {"term": 1, "value": 17.1}

    # Cambiar el porcentaje recalcula; si el plan deja de sumar 100 el lapso queda pendiente
    response = await client.patch(
        url(course, f"/evaluations/{taller['id']}"),
        json={"weight": 50},
        headers=course["headers"],
    )
    assert response.status_code == 200
    assert response.json()["graded_count"] == 1
    book = await gradebook(client, course)
    ana_row = next(s for s in book["students"] if s["student"]["first_name"] == "Ana")
    assert ana_row["grades"][0]["value"] is None

    # Borrar una nota de evaluación también deja el lapso sin nota
    await client.patch(
        url(course, f"/evaluations/{taller['id']}"),
        json={"weight": 60, "name": "Taller final", "due_date": None},
        headers=course["headers"],
    )
    sheet = await grade(client, course, 1, [entry(course, "Ana Pérez", parcial, None)])
    assert by_name(sheet, "Ana")["term_grade"] is None
    assert sheet["evaluations"][1]["name"] == "Taller final"


async def test_historial_de_evaluaciones_y_nota_calculada(client, course):
    examen = await add(client, course, name="Examen", weight=100)
    await grade(client, course, 1, [entry(course, "Ana Pérez", examen, 12)])
    await grade(client, course, 1, [entry(course, "Ana Pérez", examen, 14)])

    history = (await client.get(url(course, "/grade-history"), headers=course["headers"])).json()[
        "items"
    ]
    assert [
        (c["evaluation_name"], c["source"], c["old_value"], c["new_value"]) for c in history
    ] == [
        (None, "evaluations", 12, 14),
        ("Examen", "manual", 12, 14),
        (None, "evaluations", None, 12),
        ("Examen", "manual", None, 12),
    ]


async def test_no_se_carga_a_mano_un_lapso_con_evaluaciones(client, course):
    await add(client, course, term=2, name="Examen", weight=100)
    response = await client.put(
        url(course, "/grades"),
        json={"grades": [{"student_id": course["students"]["Ana Pérez"], "term": 2, "value": 9}]},
        headers=course["headers"],
    )
    assert response.status_code == 422
    assert "plan de evaluación" in response.json()["detail"]


async def test_la_primera_evaluacion_exige_el_lapso_sin_notas(client, course):
    ana = course["students"]["Ana Pérez"]
    await client.put(
        url(course, "/grades"),
        json={"grades": [{"student_id": ana, "term": 1, "value": 9}]},
        headers=course["headers"],
    )
    response = await client.post(
        url(course, "/evaluations"),
        json={"term": 1, "name": "Examen", "weight": 100},
        headers=course["headers"],
    )
    assert response.status_code == 409
    assert "1 notas cargadas" in response.json()["detail"]


async def test_borrar_la_ultima_evaluacion_devuelve_el_lapso_a_carga_directa(client, course):
    examen = await add(client, course, name="Examen", weight=100)
    await grade(client, course, 1, [entry(course, "Bea Gómez", examen, 16)])
    response = await client.delete(
        url(course, f"/evaluations/{examen['id']}"), headers=course["headers"]
    )
    assert response.status_code == 204
    book = await gradebook(client, course)
    assert book["computed_terms"] == []
    assert all(s["grades"][0]["value"] is None for s in book["students"])

    missing = await client.delete(
        url(course, f"/evaluations/{examen['id']}"), headers=course["headers"]
    )
    assert missing.status_code == 404


async def test_validaciones_de_las_notas_de_evaluacion(client, course):
    examen = await add(client, course, name="Examen", weight=100)
    other = await add(client, course, term=2, name="Otro", weight=100)

    out_of_scale = await grade(client, course, 1, [entry(course, "Ana Pérez", examen, 25)], 422)
    assert "Examen" in out_of_scale["detail"]
    wrong_term = await grade(client, course, 1, [entry(course, "Ana Pérez", other, 10)], 404)
    assert "no es del lapso 1" in wrong_term["detail"]
    stranger = {"student_id": 999, "evaluation_id": examen["id"], "value": 10}
    await grade(client, course, 1, [stranger], 404)
    duplicated = [entry(course, "Ana Pérez", examen, 10)] * 2
    await grade(client, course, 1, duplicated, 422)
    await grade(client, course, 4, [entry(course, "Ana Pérez", examen, 10)], 422)
    response = await client.get(
        url(course, "/terms/9/evaluation-grades"), headers=course["headers"]
    )
    assert response.status_code == 422


async def test_solo_el_profesor_de_la_materia(client, course, login_as):
    other = await login_as(Role.TEACHER, email="otro@uni.edu")
    response = await client.get(url(course, "/evaluations"), headers=other)
    assert response.status_code == 404
    response = await client.post(
        url(course, "/evaluations"), json={"term": 1, "name": "X", "weight": 10}, headers=other
    )
    assert response.status_code == 404


async def test_periodo_cerrado_no_admite_cambios(client, course, login_as):
    examen = await add(client, course, name="Examen", weight=100)
    coord = await login_as(Role.COORDINATOR, email="coord2@uni.edu")
    subject = (await client.get(f"/subjects/{course['subject_id']}", headers=coord)).json()
    closed = await client.post(f"/semesters/{subject['semester']['id']}/close", headers=coord)
    assert closed.status_code == 200, closed.text

    responses = [
        await client.post(
            url(course, "/evaluations"),
            json={"term": 2, "name": "X", "weight": 10},
            headers=course["headers"],
        ),
        await client.patch(
            url(course, f"/evaluations/{examen['id']}"),
            json={"weight": 90},
            headers=course["headers"],
        ),
        await client.delete(url(course, f"/evaluations/{examen['id']}"), headers=course["headers"]),
        await client.put(
            url(course, "/terms/1/evaluation-grades"),
            json={"grades": [entry(course, "Ana Pérez", examen, 10)]},
            headers=course["headers"],
        ),
    ]
    assert [r.status_code for r in responses] == [409] * 4


async def test_la_configuracion_respeta_las_evaluaciones(client, course, login_as):
    # Plan incompleto: hay notas de evaluación pero aún no hay nota del lapso
    examen = await add(client, course, term=3, name="Examen", weight=50)
    await grade(client, course, 3, [entry(course, "Ana Pérez", examen, 19.25)])
    admin = await login_as(Role.SUPER_ADMIN, email="root@uni.edu")
    current = (await client.get("/settings/academic")).json()
    payload = {k: v for k, v in current.items() if k != "summary"}

    async def put(**changes):
        return await client.put("/settings/academic", json={**payload, **changes}, headers=admin)

    fewer_terms = await put(terms_count=2)
    assert fewer_terms.status_code == 409
    assert "1 evaluaciones" in fewer_terms.json()["detail"]
    smaller_scale = await put(max_grade=18, passing_grade=9)
    assert smaller_scale.status_code == 409
    assert "fuera de la escala" in smaller_scale.json()["detail"]
    fewer_decimals = await put(grade_decimals=1)
    assert fewer_decimals.status_code == 409
    assert "19.25" in fewer_decimals.json()["detail"]


async def test_el_estudiante_ve_su_plan_de_evaluacion(client, campus, session):
    from app.ai.student_tools import TOOLS

    mat = campus["subjects"]["MAT-101"]["id"]
    pedro = campus["teachers"]["Pedro Profe"]
    ana = campus["students"]["Ana"]
    created = await client.post(
        f"/teacher/subjects/{mat}/evaluations",
        json={"term": 3, "name": "Examen final", "weight": 60, "due_date": "2026-12-01"},
        headers=pedro,
    )
    await client.post(
        f"/teacher/subjects/{mat}/evaluations",
        json={"term": 3, "name": "Proyecto", "weight": 40},
        headers=pedro,
    )
    await client.put(
        f"/teacher/subjects/{mat}/terms/3/evaluation-grades",
        json={
            "grades": [
                {"student_id": ana["id"], "evaluation_id": created.json()["id"], "value": 15}
            ]
        },
        headers=pedro,
    )

    detail = (await client.get(f"/student/subjects/{mat}", headers=ana["headers"])).json()
    assert detail["evaluations"] == [
        {"term": 3, "name": "Examen final", "weight": 60, "due_date": "2026-12-01", "value": 15},
        {"term": 3, "name": "Proyecto", "weight": 40, "due_date": None, "value": None},
    ]
    assert detail["grades"][2]["value"] is None

    result = await TOOLS["notas_de_materia"].run(session, ana["id"], {"materia": "MAT-101"})
    plan = result["planes_de_evaluacion"]["Lapso 3"]
    assert plan["porcentaje_planificado"] == 100
    assert plan["puntos_acumulados"] == 9
    assert plan["evaluaciones"][0] == {
        "nombre": "Examen final",
        "porcentaje": 60,
        "fecha": "2026-12-01",
        "nota": 15,
    }
    # Otra materia sin plan: no aparece nada
    fis = await TOOLS["notas_de_materia"].run(session, ana["id"], {"materia": "FIS-101"})
    assert fis["planes_de_evaluacion"] == {}
