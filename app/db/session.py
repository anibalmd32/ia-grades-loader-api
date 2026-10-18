from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.url import engine_url_and_args

settings = get_settings()
database_url, connect_args = engine_url_and_args(settings.database_url)

engine = create_async_engine(
    database_url,
    connect_args=connect_args,
    echo=settings.database_echo,
    pool_pre_ping=True,
    pool_recycle=3600,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependencia de FastAPI: una sesión por petición."""
    async with SessionLocal() as session:
        yield session
