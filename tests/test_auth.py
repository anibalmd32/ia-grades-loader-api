from datetime import datetime

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import require_roles
from app.core.security import create_access_token
from app.db.session import get_db
from app.models import Nationality, RevokedToken, Role
from tests.factories import names


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def login_staff(client, email: str, password: str = "secreta123"):
    return await client.post("/auth/login/staff", json={"email": email, "password": password})


async def login_student(client, cedula, nationality: str = "V", password: str = "secreta123"):
    return await client.post(
        "/auth/login/student",
        json={"nationality": nationality, "cedula": cedula, "password": password},
    )


@pytest.mark.parametrize("role", [Role.SUPER_ADMIN, Role.COORDINATOR, Role.TEACHER])
async def test_login_personal_con_email(client, create_user, role):
    await create_user(role, email="persona@uni.edu")
    response = await login_staff(client, "PERSONA@uni.edu")
    assert response.status_code == 200
    assert response.json()["role"] == role.value


async def test_login_estudiante_con_cedula(client, create_user):
    await create_user(Role.STUDENT, cedula=12345678, must_change_password=True)
    response = await login_student(client, "12.345.678")
    assert response.status_code == 200
    assert response.json()["must_change_password"] is True


async def test_cedula_distingue_nacionalidad(client, create_user):
    await create_user(Role.STUDENT, nationality=Nationality.EXTRANJERO, cedula=555)
    assert (await login_student(client, 555, "V")).status_code == 401
    assert (await login_student(client, 555, "E")).status_code == 200


async def test_credenciales_incorrectas(client, create_user):
    await create_user(Role.TEACHER, email="profe@uni.edu")
    assert (await login_staff(client, "profe@uni.edu", "mala")).status_code == 401
    assert (await login_staff(client, "nadie@uni.edu")).status_code == 401


async def test_estudiante_no_entra_por_login_de_personal(client, create_user):
    # Aunque un estudiante tuviera email, solo puede entrar con su cédula
    await create_user(Role.STUDENT, email="est@uni.edu")
    assert (await login_staff(client, "est@uni.edu")).status_code == 401


async def test_cuenta_desactivada(client, create_user):
    await create_user(Role.TEACHER, email="baja@uni.edu", is_active=False)
    response = await login_staff(client, "baja@uni.edu")
    assert response.status_code == 403


async def test_token_oauth2_para_swagger(client, create_user):
    await create_user(Role.COORDINATOR, email="coord@uni.edu")
    await create_user(Role.STUDENT, cedula=7654321)

    for username in ["coord@uni.edu", "V-7654321", "v7.654.321", "V 7654321"]:
        response = await client.post(
            "/auth/token", data={"username": username, "password": "secreta123"}
        )
        assert response.status_code == 200, username

    bad = await client.post("/auth/token", data={"username": "X-1", "password": "secreta123"})
    assert bad.status_code == 401


async def test_me(client, create_user):
    await create_user(Role.STUDENT, cedula=1234, **names("Ana Pérez"))
    token = (await login_student(client, 1234)).json()["access_token"]
    me = (await client.get("/auth/me", headers=bearer(token))).json()
    assert me["full_name"] == "Ana Pérez"
    assert me["cedula_display"] == "V-1234"
    assert me["telegram_linked"] is False


async def test_me_sin_token_o_token_invalido(client):
    assert (await client.get("/auth/me")).status_code == 401
    assert (await client.get("/auth/me", headers=bearer("basura"))).status_code == 401


async def test_usuario_desactivado_pierde_acceso_con_token_vigente(client, create_user, session):
    user = await create_user(Role.TEACHER, email="t@uni.edu")
    token = (await login_staff(client, "t@uni.edu")).json()["access_token"]
    user.is_active = False
    await session.commit()
    assert (await client.get("/auth/me", headers=bearer(token))).status_code == 401


async def test_logout_revoca_solo_el_token_usado(client, create_user):
    await create_user(Role.SUPER_ADMIN, email="admin@uni.edu")
    token_a = (await login_staff(client, "admin@uni.edu")).json()["access_token"]
    token_b = (await login_staff(client, "admin@uni.edu")).json()["access_token"]

    assert (await client.post("/auth/logout", headers=bearer(token_a))).status_code == 204
    assert (await client.get("/auth/me", headers=bearer(token_a))).status_code == 401
    assert (await client.post("/auth/logout", headers=bearer(token_a))).status_code == 401
    # La otra sesión (otro dispositivo) sigue activa
    assert (await client.get("/auth/me", headers=bearer(token_b))).status_code == 200


async def test_logout_limpia_revocados_vencidos(client, create_user, session):
    session.add(RevokedToken(jti="viejo", expires_at=datetime(2000, 1, 1)))
    await session.commit()
    await create_user(Role.TEACHER, email="t@uni.edu")
    token = (await login_staff(client, "t@uni.edu")).json()["access_token"]
    await client.post("/auth/logout", headers=bearer(token))
    session.expire_all()
    assert await session.get(RevokedToken, "viejo") is None


async def test_logout_sin_token(client):
    assert (await client.post("/auth/logout")).status_code == 401


async def test_cambio_de_password(client, create_user):
    await create_user(Role.STUDENT, cedula=99, must_change_password=True)
    token = (await login_student(client, 99)).json()["access_token"]

    wrong = await client.post(
        "/auth/change-password",
        json={"current_password": "mala", "new_password": "nueva-clave"},
        headers=bearer(token),
    )
    assert wrong.status_code == 400

    ok = await client.post(
        "/auth/change-password",
        json={"current_password": "secreta123", "new_password": "nueva-clave"},
        headers=bearer(token),
    )
    assert ok.status_code == 204
    # Cambiar la contraseña cierra la sesión actual
    assert (await client.get("/auth/me", headers=bearer(token))).status_code == 401

    assert (await login_student(client, 99)).status_code == 401
    relogin = await login_student(client, 99, password="nueva-clave")
    assert relogin.json()["must_change_password"] is False


@pytest.fixture
async def roles_client(sessionmaker):
    """Mini-app aislada con un endpoint restringido a coordinadores."""
    test_app = FastAPI()

    @test_app.get("/coordinator", dependencies=[Depends(require_roles(Role.COORDINATOR))])
    async def _route():
        return {"ok": True}

    async def override_get_db():
        async with sessionmaker() as s:
            yield s

    test_app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        yield c


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (Role.SUPER_ADMIN, 200),
        (Role.COORDINATOR, 200),
        (Role.TEACHER, 403),
        (Role.STUDENT, 403),
    ],
)
async def test_super_admin_hereda_permisos_de_coordinador(
    roles_client, create_user, role, expected
):
    user = await create_user(role)
    token, _ = create_access_token(user.id, user.role.value)
    response = await roles_client.get("/coordinator", headers=bearer(token))
    assert response.status_code == expected


async def test_cambio_de_password_cierra_todas_las_sesiones(client, create_user):
    await create_user(Role.TEACHER, email="t@uni.edu")
    other_device = (await login_staff(client, "t@uni.edu")).json()["access_token"]
    current = (await login_staff(client, "t@uni.edu")).json()["access_token"]

    await client.post(
        "/auth/change-password",
        json={"current_password": "secreta123", "new_password": "nueva-clave"},
        headers=bearer(current),
    )
    assert (await client.get("/auth/me", headers=bearer(other_device))).status_code == 401
    fresh = (await login_staff(client, "t@uni.edu", "nueva-clave")).json()["access_token"]
    assert (await client.get("/auth/me", headers=bearer(fresh))).status_code == 200
