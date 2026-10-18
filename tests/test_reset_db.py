import asyncio
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app import reset_db
from app.core.config import get_settings
from app.db.base import Base


@pytest.fixture
async def database(tmp_path, monkeypatch):
    """Base SQLite en archivo con todas las tablas, usada como DATABASE_URL."""
    path = tmp_path / "notas.db"
    url = f"sqlite+aiosqlite:///{path}"
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()
    monkeypatch.setattr(get_settings(), "database_url", url)
    recreated = []
    monkeypatch.setattr(reset_db, "recreate_schema", lambda: recreated.append(True))
    return {"path": path, "recreated": recreated}


def tables(path) -> set[str]:
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return {name for (name,) in rows}


async def test_borra_todas_las_tablas_y_recrea_el_esquema(database):
    assert "users" in tables(database["path"])
    # asyncio.run() necesita un hilo sin bucle de eventos en marcha
    code = await _run_in_thread(["--yes"])
    assert code == 0
    assert tables(database["path"]) == set()
    assert database["recreated"] == [True]


async def test_sin_confirmacion_no_borra_nada(database, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "otra-base")
    assert await _run_in_thread([]) == 1
    assert "users" in tables(database["path"])
    assert database["recreated"] == []


async def test_confirmando_con_el_nombre_de_la_base(database, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: str(database["path"]))
    assert await _run_in_thread([]) == 0
    assert tables(database["path"]) == set()


async def _run_in_thread(argv):
    return await asyncio.to_thread(reset_db.main, argv)
