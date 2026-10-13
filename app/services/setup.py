"""Instalador: crea el único super admin mientras el sistema no esté instalado."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import Installation, Role, User
from app.models.installation import INSTALLATION_ID


class AlreadyInstalledError(Exception):
    pass


async def is_installed(db: AsyncSession) -> bool:
    return await db.scalar(select(Installation.id).limit(1)) is not None


async def install(db: AsyncSession, full_name: str, email: str, password: str) -> User:
    if await is_installed(db):
        raise AlreadyInstalledError

    admin = User(
        role=Role.SUPER_ADMIN,
        full_name=full_name,
        email=email.lower(),
        password_hash=hash_password(password),
    )
    db.add(admin)
    try:
        await db.flush()
        # La PK fija (id = 1) hace que una segunda instalación simultánea falle aquí
        db.add(Installation(id=INSTALLATION_ID, super_admin_id=admin.id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise AlreadyInstalledError from None
    return admin
