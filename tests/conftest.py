from collections.abc import AsyncIterator

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (registra las tablas)
from app.db.base import Base


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """Sesión sobre SQLite en memoria con el esquema completo."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fks(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s

    await engine.dispose()
