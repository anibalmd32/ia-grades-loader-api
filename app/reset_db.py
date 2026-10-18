"""Reinicia la base de datos: borra TODAS las tablas y las vuelve a crear vacías.

Pensado para limpiar después de las pruebas (p. ej. desde la shell de Render):

    python -m app.reset_db            # pide escribir el nombre de la base para confirmar
    python -m app.reset_db --yes      # sin preguntar (scripts)

Tras reiniciar no queda ningún usuario: el instalador del super admin
(`POST /api/v1/setup`) vuelve a estar disponible. Las tablas se recrean con las
migraciones de Alembic, así que el esquema queda igual que en un despliegue nuevo.
"""

import argparse
import asyncio
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.url import engine_url_and_args

ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def _drop_tables(connection: Connection) -> list[str]:
    tables = inspect(connection).get_table_names()
    mysql = connection.dialect.name == "mysql"
    if mysql:
        # Sin esto habría que borrar en orden inverso a las claves foráneas
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
    for table in tables:
        connection.execute(
            text(f"DROP TABLE IF EXISTS {connection.dialect.identifier_preparer.quote(table)}")
        )
    if mysql:
        connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    return tables


async def drop_all_tables(engine: AsyncEngine) -> list[str]:
    """Borra todas las tablas de la base (incluida alembic_version). Devuelve sus nombres."""
    async with engine.begin() as connection:
        return await connection.run_sync(_drop_tables)


def recreate_schema() -> None:
    """Aplica todas las migraciones sobre la base vacía."""
    command.upgrade(Config(str(ALEMBIC_INI)), "head")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Borra todos los datos y recrea las tablas.")
    parser.add_argument("--yes", action="store_true", help="no pedir confirmación")
    args = parser.parse_args(argv)

    url, connect_args = engine_url_and_args(get_settings().database_url)
    parsed = make_url(url)
    database = parsed.database or ""
    print(f"Base de datos: {database} en {parsed.host or 'local'}")
    print("Se borrarán TODOS los datos: usuarios, materias, notas, conversaciones...")
    if not args.yes:
        answer = input(f"Escribe el nombre de la base ({database}) para confirmar: ").strip()
        if answer != database:
            print("Cancelado: no se ha borrado nada.")
            return 1

    engine = create_async_engine(url, connect_args=connect_args, poolclass=NullPool)

    async def _drop() -> list[str]:
        try:
            return await drop_all_tables(engine)
        finally:
            await engine.dispose()

    dropped = asyncio.run(_drop())
    print(f"Tablas borradas: {len(dropped)}")
    recreate_schema()
    print("Listo: base vacía con el esquema al día. Crea el super admin con el instalador.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
