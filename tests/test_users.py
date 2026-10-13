import pytest

from app.models import Role


@pytest.fixture
async def admin(login_as):
    return await login_as(Role.SUPER_ADMIN, email="admin@uni.edu")


@pytest.fixture
async def coord(login_as):
    return await login_as(Role.COORDINATOR, email="coord@uni.edu")


def student_payload(cedula=12345678, nationality="V", name="Ana Pérez") -> dict:
    return {"full_name": name, "nationality": nationality, "cedula": cedula}


# --- Permisos ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "coordinators", "teachers", "students"),
    [
        (Role.SUPER_ADMIN, 200, 200, 200),
        (Role.COORDINATOR, 403, 200, 200),
        (Role.TEACHER, 403, 403, 403),
        (Role.STUDENT, 403, 403, 403),
    ],
)
async def test_quien_gestiona_que(client, login_as, role, coordinators, teachers, students):
    headers = await login_as(role, **({} if role == Role.STUDENT else {"email": "yo@uni.edu"}))
    assert (await client.get("/coordinators", headers=headers)).status_code == coordinators
    assert (await client.get("/teachers", headers=headers)).status_code == teachers
    assert (await client.get("/students", headers=headers)).status_code == students


# --- Alta y login con la contraseña generada ---------------------------------


async def test_super_admin_crea_coordinador_que_puede_entrar(client, admin):
    response = await client.post(
        "/coordinators", json={"full_name": "Carla Coord", "email": "Carla@Uni.edu"}, headers=admin
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "carla@uni.edu"
    assert body["user"]["role"] == "coordinator"
    assert body["user"]["must_change_password"] is True

    login = await client.post(
        "/auth/login/staff",
        json={"email": "carla@uni.edu", "password": body["generated_password"]},
    )
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True


async def test_coordinador_crea_estudiante_que_entra_con_cedula(client, coord):
    response = await client.post(
        "/students", json=student_payload(cedula="12.345.678"), headers=coord
    )
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["cedula_display"] == "V-12345678"
    assert body["user"]["email"] is None

    login = await client.post(
        "/auth/login/student",
        json={"nationality": "V", "cedula": 12345678, "password": body["generated_password"]},
    )
    assert login.status_code == 200


async def test_duplicados(client, coord):
    await client.post("/teachers", json={"full_name": "Pedro", "email": "p@uni.edu"}, headers=coord)
    dup_email = await client.post(
        "/teachers", json={"full_name": "Pedro Dos", "email": "P@uni.edu"}, headers=coord
    )
    assert dup_email.status_code == 409

    await client.post("/students", json=student_payload(100), headers=coord)
    dup_cedula = await client.post("/students", json=student_payload(100), headers=coord)
    assert dup_cedula.status_code == 409
    # Mismo número con otro tipo de cédula es otra persona
    other = await client.post("/students", json=student_payload(100, "E"), headers=coord)
    assert other.status_code == 201


async def test_validaciones_de_entrada(client, coord):
    bad_email = await client.post(
        "/teachers", json={"full_name": "Pedro", "email": "no-es-email"}, headers=coord
    )
    assert bad_email.status_code == 422
    bad_type = await client.post("/students", json=student_payload(nationality="X"), headers=coord)
    assert bad_type.status_code == 422
    bad_cedula = await client.post("/students", json=student_payload(cedula=0), headers=coord)
    assert bad_cedula.status_code == 422


# --- Consulta, edición, desactivación y reseteo ------------------------------


async def test_listar_y_buscar_estudiantes(client, coord):
    await client.post("/students", json=student_payload(111, name="Ana Pérez"), headers=coord)
    await client.post("/students", json=student_payload(222, name="Luis Gómez"), headers=coord)
    teacher = {"full_name": "Ana Profe", "email": "a@uni.edu"}
    await client.post("/teachers", json=teacher, headers=coord)

    all_students = (await client.get("/students", headers=coord)).json()
    assert all_students["total"] == 2
    assert [u["full_name"] for u in all_students["items"]] == ["Ana Pérez", "Luis Gómez"]

    by_name = (await client.get("/students?q=ana", headers=coord)).json()
    assert [u["full_name"] for u in by_name["items"]] == ["Ana Pérez"]

    by_cedula = (await client.get("/students?q=V-222", headers=coord)).json()
    assert [u["full_name"] for u in by_cedula["items"]] == ["Luis Gómez"]


async def test_ids_de_otro_rol_no_se_encuentran(client, coord):
    created = await client.post(
        "/teachers", json={"full_name": "Pedro", "email": "p@uni.edu"}, headers=coord
    )
    teacher_id = created.json()["user"]["id"]
    assert (await client.get(f"/students/{teacher_id}", headers=coord)).status_code == 404
    assert (await client.get(f"/teachers/{teacher_id}", headers=coord)).status_code == 200


async def test_editar_estudiante_valida_cedula(client, coord):
    a = (await client.post("/students", json=student_payload(1, name="Ana"), headers=coord)).json()
    await client.post("/students", json=student_payload(2, name="Bea"), headers=coord)
    student_id = a["user"]["id"]

    conflict = await client.patch(f"/students/{student_id}", json={"cedula": 2}, headers=coord)
    assert conflict.status_code == 409

    ok = await client.patch(
        f"/students/{student_id}", json={"cedula": 2, "nationality": "E"}, headers=coord
    )
    assert ok.status_code == 200
    assert ok.json()["cedula_display"] == "E-2"


async def test_desactivar_impide_el_acceso(client, coord):
    teacher = {"full_name": "Pedro", "email": "p@uni.edu"}
    created = (await client.post("/teachers", json=teacher, headers=coord)).json()
    teacher_id = created["user"]["id"]
    off = await client.patch(f"/teachers/{teacher_id}", json={"is_active": False}, headers=coord)
    assert off.json()["is_active"] is False

    login = await client.post(
        "/auth/login/staff", json={"email": "p@uni.edu", "password": created["generated_password"]}
    )
    assert login.status_code == 403

    inactive = (await client.get("/teachers?is_active=false", headers=coord)).json()
    assert inactive["total"] == 1


async def test_reset_password(client, coord):
    created = (await client.post("/students", json=student_payload(5), headers=coord)).json()
    student_id = created["user"]["id"]

    reset = await client.post(f"/students/{student_id}/reset-password", headers=coord)
    new_password = reset.json()["generated_password"]
    assert new_password != created["generated_password"]

    login = lambda pw: client.post(  # noqa: E731
        "/auth/login/student", json={"nationality": "V", "cedula": 5, "password": pw}
    )
    assert (await login(created["generated_password"])).status_code == 401
    assert (await login(new_password)).status_code == 200
