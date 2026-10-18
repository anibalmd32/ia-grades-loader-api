import pytest
from sqlalchemy import func, select

from app.models import Installation, Role, User
from app.services import setup as setup_service
from tests.factories import names

ADMIN = {**names("Admin Principal"), "email": "Admin@Uni.edu", "password": "clave-segura"}


async def test_flujo_de_instalacion(client):
    assert (await client.get("/setup/status")).json() == {"installed": False}

    response = await client.post("/setup", json=ADMIN)
    assert response.status_code == 201
    body = response.json()
    assert body["role"] == "super_admin"
    assert body["must_change_password"] is False

    me = await client.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["email"] == "admin@uni.edu"

    assert (await client.get("/setup/status")).json() == {"installed": True}


async def test_instalador_desaparece_tras_instalar(client):
    await client.post("/setup", json=ADMIN)
    otro = {**ADMIN, "email": "otro@uni.edu"}
    response = await client.post("/setup", json=otro)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


async def test_password_corta_rechazada(client):
    response = await client.post("/setup", json={**ADMIN, "password": "corta"})
    assert response.status_code == 422


async def test_carrera_de_instalacion_no_deja_usuarios_huerfanos(sessionmaker, monkeypatch):
    """Simula dos instalaciones simultáneas: ambas pasan el chequeo inicial y la
    restricción de la tabla installation (id = 1) es la que frena a la segunda."""
    async with sessionmaker() as db:
        await setup_service.install(db, "Admin", "1", "a1@uni.edu", "clave-segura")

    async def never_installed(_db):
        return False

    monkeypatch.setattr(setup_service, "is_installed", never_installed)
    async with sessionmaker() as db:
        with pytest.raises(setup_service.AlreadyInstalledError):
            await setup_service.install(db, "Admin", "2", "a2@uni.edu", "clave-segura")

    async with sessionmaker() as db:
        admins = await db.scalar(
            select(func.count()).select_from(User).where(User.role == Role.SUPER_ADMIN)
        )
        installs = await db.scalar(select(func.count()).select_from(Installation))
    assert admins == 1
    assert installs == 1
