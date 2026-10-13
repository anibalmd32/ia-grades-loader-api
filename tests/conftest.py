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
