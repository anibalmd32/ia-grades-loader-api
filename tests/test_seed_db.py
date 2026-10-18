import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app import reset_db, seed_db
from app.core.config import get_settings
from app.db.base import Base
from app.models import Enrollment, Grade, ImportJob, Installation, Role, User
from app.seed_db import DEFAULT_PASSWORD, NotEmptyError, seed


@pytest.fixture
async def seeded(session):
    return await seed(session)


async def login(client, **credentials):
    if "email" in credentials:
        response = await client.post(
            "/auth/login/staff", json={**credentials, "password": DEFAULT_PASSWORD}
        )
    else:
        response = await client.post(
            "/auth/login/student", json={**credentials, "password": DEFAULT_PASSWORD}
        )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_siembra_todo(session, seeded):
    assert seeded.students == 32
    assert seeded.subjects == 10
    count = lambda model: session.scalar(select(func.count()).select_from(model))  # noqa: E731
    assert await count(Installation) == 1
    assert await count(Enrollment) == seeded.enrollments
    assert await count(Grade) == seeded.grades > 400
    assert await count(ImportJob) == 3
    roles = dict((await session.execute(select(User.role, func.count()).group_by(User.role))).all())
    assert roles == {Role.SUPER_ADMIN: 1, Role.COORDINATOR: 2, Role.TEACHER: 6, Role.STUDENT: 32}


async def test_no_siembra_sobre_una_base_con_datos(session, seeded):
    with pytest.raises(NotEmptyError):
        await seed(session)


async def test_el_instalador_ya_no_esta_disponible(client, seeded):
    assert (await client.get("/setup/status")).json() == {"installed": True}


async def test_demo_del_estudiante(client, seeded):
    headers = await login(client, nationality="V", cedula=12345678)
    body = (await client.get("/student/subjects", headers=headers)).json()
    standings = {s["subject"]["code"]: s["projection"]["standing"] for s in body["subjects"]}
    assert standings == {
        "LEN-101": "passed",
        "MAT-101": "passed",
        "FIS-101": "passed",
        "PRO-101": "passed",
        "QUI-101": "failed",
        "EST-101": "impossible",
        "FIS-102": "in_progress",
        "ING-101": "in_progress",
        "MAT-102": "secured",
        "PRO-102": "passed",
    }
    assert body["average"] == 13.34  # el que menciona la conversación de ejemplo
    assert [s["semester"]["name"] for s in body["semesters"]] == ["2025-II", "2026-I"]

    conversations = (await client.get("/student/conversations", headers=headers)).json()
    assert conversations["total"] == 2
    assert conversations["items"][0]["title"] == "¿En qué materia voy peor?"


async def test_demo_del_profesor(client, seeded):
    headers = await login(client, email="profesor@demo.edu")
    subjects = (await client.get("/teacher/subjects", headers=headers)).json()
    assert [s["code"] for s in subjects] == ["MAT-101", "MAT-102"]
    mat102 = next(s for s in subjects if s["code"] == "MAT-102")
    assert mat102["students_count"] == 32
    assert mat102["grades_count"] == {"1": 32, "2": 32, "3": 0}

    jobs = (await client.get("/teacher/import-jobs", headers=headers)).json()
    assert [(j["status"], j["confirmed_at"] is not None) for j in jobs] == [
        ("done", False),
        ("done", True),
        ("failed", False),
    ]
    pending = (await client.get(f"/teacher/import-jobs/{jobs[0]['id']}", headers=headers)).json()
    assert pending["result"]["summary"] == {"ok": 28, "warning": 1, "error": 2}
    assert len(pending["result"]["students_without_grades"]) == 2


async def test_demo_de_administracion(client, seeded):
    admin = await login(client, email="admin@demo.edu")
    coordinators = (await client.get("/coordinators", headers=admin)).json()
    assert coordinators["total"] == 2
    settings = (await client.get("/settings/academic")).json()
    assert settings["institution_name"] == "Universidad Demo"

    coord = await login(client, email="coordinacion@demo.edu")
    students = (await client.get("/students?limit=100", headers=coord)).json()
    assert students["total"] == 32
    assert sum(not s["is_active"] for s in students["items"]) == 1
    assert sum(s["nationality"] == "E" for s in students["items"]) == 4
    teachers = (await client.get("/teachers", headers=coord)).json()["items"]
    assert all(t["subjects_count"] == 2 for t in teachers)


async def test_profesor_con_contrasena_inicial(client, seeded):
    response = await client.post(
        "/auth/login/staff", json={"email": "ricardo.mendez@demo.edu", "password": "demo1234"}
    )
    assert response.json()["must_change_password"] is True


# --- Línea de comandos ------------------------------------------------------------


@pytest.fixture
async def database(tmp_path, monkeypatch):
    path = tmp_path / "demo.db"
    url = f"sqlite+aiosqlite:///{path}"
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    monkeypatch.setattr(get_settings(), "database_url", url)
    return url


async def test_comando_siembra_y_se_niega_a_repetir(database, capsys):
    assert await asyncio.to_thread(seed_db.main, []) == 0
    out = capsys.readouterr().out
    assert "Contraseña de todas las cuentas: demo1234" in out
    assert "V-12345678" in out
    assert await asyncio.to_thread(seed_db.main, []) == 1
    assert "reset_db --yes --seed" in capsys.readouterr().out


async def test_contrasena_demasiado_corta(database):
    with pytest.raises(SystemExit):
        await asyncio.to_thread(seed_db.main, ["--password", "corta"])


async def test_reset_con_seed(database, monkeypatch):
    async def recreate():
        engine = create_async_engine(database, poolclass=NullPool)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    monkeypatch.setattr(reset_db, "recreate_schema", lambda: asyncio.run(recreate()))
    assert await asyncio.to_thread(seed_db.main, []) == 0
    # Con datos: reset --seed borra y vuelve a sembrar
    assert await asyncio.to_thread(reset_db.main, ["--yes", "--seed"]) == 0
