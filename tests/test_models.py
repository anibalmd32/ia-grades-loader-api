import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Enrollment, Grade, Installation, Role, Semester, Subject, User


def make_user(cedula: str, role: Role = Role.STUDENT) -> User:
    return User(cedula=cedula, full_name=f"Usuario {cedula}", role=role, password_hash="x")


async def seed_enrollment(session) -> Enrollment:
    student = make_user("V1")
    semester = Semester(name="2026-I")
    subject = Subject(code="MAT101", name="Matemática I", semester=semester)
    session.add_all([student, subject])
    await session.flush()
    enrollment = Enrollment(student_id=student.id, subject_id=subject.id)
    session.add(enrollment)
    await session.flush()
    return enrollment


async def test_cedula_unica(session):
    session.add_all([make_user("V123"), make_user("V123", Role.TEACHER)])
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_usuario_activo_por_defecto(session):
    user = make_user("V9")
    session.add(user)
    await session.flush()
    await session.refresh(user)
    assert user.is_active is True
    assert user.must_change_password is False


@pytest.mark.parametrize(("term", "value"), [(0, 10), (4, 10), (1, 0), (1, 21)])
async def test_nota_fuera_de_rango(session, term, value):
    enrollment = await seed_enrollment(session)
    session.add(Grade(enrollment_id=enrollment.id, term=term, value=value))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_una_nota_por_lapso(session):
    enrollment = await seed_enrollment(session)
    session.add(Grade(enrollment_id=enrollment.id, term=1, value=15))
    await session.flush()
    session.add(Grade(enrollment_id=enrollment.id, term=1, value=18))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_matricula_unica(session):
    enrollment = await seed_enrollment(session)
    session.add(Enrollment(student_id=enrollment.student_id, subject_id=enrollment.subject_id))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_instalacion_unica(session):
    admin = make_user("V0", Role.SUPER_ADMIN)
    session.add(admin)
    await session.flush()
    session.add(Installation(id=1, super_admin_id=admin.id))
    await session.flush()
    session.add(Installation(id=2, super_admin_id=admin.id))
    with pytest.raises(IntegrityError):
        await session.flush()
