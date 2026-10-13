from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (registra las tablas)
from app.core.security import create_access_token, hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app as fastapi_app
from app.models import Nationality, Role, User


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


@pytest.fixture
async def sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """SQLite en memoria con el esquema completo, compartida por todas las conexiones."""
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
        user = User(
            role=role,
            full_name=fields.pop("full_name", f"Usuario {role.value}"),
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
        "/teachers", json={"full_name": "Pedro", "email": "p@uni.edu"}, headers=coord
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
            json={"full_name": name, "nationality": nationality, "cedula": cedula},
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
