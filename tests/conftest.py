"""Fixtures comunes.

Por defecto los tests usan SQLite en memoria (rápido). Para correr la MISMA suite contra MySQL
real, define TEST_DATABASE_URL apuntando a una base de pruebas (su nombre debe contener
"test"; se vacía en cada test):

    TEST_DATABASE_URL=mysql+asyncmy://root:CLAVE@localhost:3306/ia_grades_loader_test uv run pytest
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

import app.models  # noqa: F401  (registra las tablas)
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app as fastapi_app
from app.models import Nationality, Role, User
from tests.factories import names


@pytest.fixture(autouse=True, scope="session")
def _fast_password_hashing():
    """Argon2 con parámetros mínimos en tests: el hash real es lento a propósito y
    multiplicaba el tiempo de la suite. En producción se usan los recomendados."""
    from pwdlib import PasswordHash
    from pwdlib.hashers.argon2 import Argon2Hasher

    import app.core.security as security

    original = security._password_hash
    security._password_hash = PasswordHash(
        (Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),)
    )
    yield
    security._password_hash = original


@pytest.fixture(autouse=True, scope="session")
def _test_jwt_secret():
    """Clave JWT fija para los tests: sin .env (p. ej. en el CI) sería la de por defecto,
    demasiado corta, y PyJWT avisa (InsecureKeyLengthWarning), que pytest trata como error."""
    from pydantic import SecretStr

    from app.core.config import get_settings

    settings = get_settings()
    original = settings.jwt_secret_key
    settings.jwt_secret_key = SecretStr("clave-de-pruebas-" + "x" * 48)
    yield
    settings.jwt_secret_key = original


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from app.services.chat import question_limiter

    question_limiter.reset()
    yield
    question_limiter.reset()


TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


@pytest.fixture(scope="session")
def _mysql_schema() -> None:
    """Prepara la base MySQL de pruebas con las migraciones reales (de cero a head)."""
    if not TEST_DATABASE_URL:
        return
    database = make_url(TEST_DATABASE_URL).database or ""
    from app.core.config import get_settings

    if "test" not in database or get_settings().database_url == TEST_DATABASE_URL:
        pytest.exit(
            f"TEST_DATABASE_URL apunta a '{database}': por seguridad debe ser una base "
            "distinta de la de trabajo y su nombre debe contener 'test' (se vacía en cada test)",
            returncode=2,
        )
    import asyncio

    async def drop_everything() -> None:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            tables = await conn.execute(
                text("SELECT table_name FROM information_schema.tables WHERE table_schema = :db"),
                {"db": database},
            )
            await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            for (table,) in tables.all():
                await conn.execute(text(f"DROP TABLE `{table}`"))
            await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        await engine.dispose()

    asyncio.run(drop_everything())
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    root = Path(__file__).resolve().parent.parent
    # Ida y vuelta completa sobre la base vacía: valida también los downgrade en MySQL
    for args in (["upgrade", "head"], ["downgrade", "base"], ["upgrade", "head"]):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = result.stderr[-2000:]
            pytest.exit(f"alembic {' '.join(args)} falló en MySQL:\n{detail}", returncode=2)


async def _mysql_sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    # READ COMMITTED: la sesión del fixture `session` vive todo el test y, con el REPEATABLE
    # READ por defecto de MySQL, no vería lo que la API confirma después de su primera
    # lectura. En la app cada petición usa su propia sesión corta, así que no le afecta.
    engine = create_async_engine(
        TEST_DATABASE_URL, poolclass=NullPool, isolation_level="READ COMMITTED"
    )
    async with engine.begin() as conn:
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in Base.metadata.sorted_tables:
            await conn.execute(text(f"TRUNCATE TABLE `{table.name}`"))
        await conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def sessionmaker(_mysql_schema) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """SQLite en memoria (o MySQL si hay TEST_DATABASE_URL) con el esquema completo."""
    if TEST_DATABASE_URL:
        async for maker in _mysql_sessionmaker():
            yield maker
        return

    # Ojo: StaticPool comparte UNA conexión entre todas las sesiones (necesario para que la
    # base en memoria sea la misma); no sirve para probar concurrencia real.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fks(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def session(sessionmaker) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


@pytest.fixture
async def client(sessionmaker) -> AsyncIterator[AsyncClient]:
    async def override_get_db():
        async with sessionmaker() as s:
            yield s

    fastapi_app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
        yield c
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def create_user(session):
    """Crea usuarios directamente en BD. Contraseña por defecto: 'secreta123'."""

    async def _create(role: Role, password: str = "secreta123", **fields) -> User:
        if role == Role.STUDENT:
            fields.setdefault("nationality", Nationality.VENEZOLANO)
            fields.setdefault("cedula", 12345678)
        else:
            fields.setdefault("email", f"{role.value}@uni.edu")
        if "first_name" not in fields:
            fields.update(names(fields.pop("full_name", f"Usuario {role.value}")))
        user = User(
            role=role,
            password_hash=hash_password(password),
            **fields,
        )
        session.add(user)
        await session.commit()
        return user

    return _create


@pytest.fixture
def login_as(create_user):
    """Crea un usuario del rol indicado y devuelve las cabeceras con su token."""

    async def _login(role: Role, **fields) -> dict[str, str]:
        user = await create_user(role, **fields)
        token, _ = create_access_token(user.id, user.role.value)
        return {"Authorization": f"Bearer {token}"}

    return _login


@pytest.fixture
async def course(client, login_as, session):
    """Materia MAT-101 con un profesor asignado y 4 estudiantes (V-1, V-2, E-2, V-3)."""
    coord = await login_as(Role.COORDINATOR, email="coord@uni.edu")
    semester = (await client.post("/semesters", json={"name": "2026-I"}, headers=coord)).json()
    subject = await client.post(
        "/subjects",
        json={"semester_id": semester["id"], "code": "MAT-101", "name": "Matemática I"},
        headers=coord,
    )
    subject_id = subject.json()["id"]
    teacher = await client.post(
        "/teachers", json={**names("Pedro"), "email": "p@uni.edu"}, headers=coord
    )
    teacher_id = teacher.json()["user"]["id"]
    await client.put(
        f"/teachers/{teacher_id}/subjects", json={"subject_ids": [subject_id]}, headers=coord
    )
    students = {}
    for nationality, cedula, name in [
        ("V", 1, "Ana Pérez"),
        ("V", 2, "Bea Gómez"),
        ("E", 2, "Bob Smith"),
        ("V", 3, "Carlos Ruiz"),
    ]:
        response = await client.post(
            "/students",
            json={**names(name), "nationality": nationality, "cedula": cedula},
            headers=coord,
        )
        students[name] = response.json()["user"]["id"]
    await client.post(
        f"/subjects/{subject_id}/students",
        json={"student_ids": list(students.values())},
        headers=coord,
    )
    user = await session.get(User, teacher_id)
    token, _ = create_access_token(user.id, user.role.value, user.token_version)
    return {
        "subject_id": subject_id,
        "students": students,
        "headers": {"Authorization": f"Bearer {token}"},
    }


@pytest.fixture
async def campus(client, login_as, session):
    """Dos semestres, tres materias, dos profesores; Ana cursa tres materias, Beto una."""
    coord = await login_as(Role.COORDINATOR, email="coord@uni.edu")

    async def post(url, payload):
        response = await client.post(url, json=payload, headers=coord)
        assert response.status_code in (200, 201), response.text
        return response.json()

    s1 = await post("/semesters", {"name": "2026-I"})
    s2 = await post("/semesters", {"name": "2026-II"})
    mat = await post("/subjects", {"semester_id": s1["id"], "code": "MAT-101", "name": "Mate I"})
    fis = await post("/subjects", {"semester_id": s1["id"], "code": "FIS-101", "name": "Física"})
    mat2 = await post("/subjects", {"semester_id": s2["id"], "code": "MAT-201", "name": "Mate II"})

    teachers = {}
    for name, email, subjects in [
        ("Pedro Profe", "p@uni.edu", [mat["id"], fis["id"]]),
        ("Laura Profe", "l@uni.edu", [mat["id"]]),
    ]:
        teacher = await post("/teachers", {**names(name), "email": email})
        teacher_id = teacher["user"]["id"]
        await client.put(
            f"/teachers/{teacher_id}/subjects", json={"subject_ids": subjects}, headers=coord
        )
        user = await session.get(User, teacher_id)
        token, _ = create_access_token(user.id, user.role.value, user.token_version)
        teachers[name] = {"Authorization": f"Bearer {token}"}

    students = {}
    for name, cedula, subjects in [
        ("Ana", 1, [mat["id"], fis["id"], mat2["id"]]),
        ("Beto", 2, [mat["id"]]),
    ]:
        created = await post("/students", {**names(name), "nationality": "V", "cedula": cedula})
        student_id = created["user"]["id"]
        await post(f"/students/{student_id}/subjects", {"subject_ids": subjects})
        user = await session.get(User, student_id)
        token, _ = create_access_token(user.id, user.role.value, user.token_version)
        students[name] = {"id": student_id, "headers": {"Authorization": f"Bearer {token}"}}

    async def grade(teacher, subject, student, term, value):
        entry = {"student_id": students[student]["id"], "term": term, "value": value}
        response = await client.put(
            f"/teacher/subjects/{subject['id']}/grades",
            json={"grades": [entry]},
            headers=teachers[teacher],
        )
        assert response.status_code == 200, response.text

    await grade("Pedro Profe", mat, "Ana", 1, 15)
    await grade("Pedro Profe", mat, "Ana", 2, 18.5)
    await grade("Pedro Profe", fis, "Ana", 1, 10)
    await grade("Pedro Profe", mat, "Beto", 1, 20)
    return {
        "students": students,
        "teachers": teachers,
        "subjects": {"MAT-101": mat, "FIS-101": fis, "MAT-201": mat2},
    }
