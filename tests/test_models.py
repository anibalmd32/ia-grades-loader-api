import pytest
from sqlalchemy.exc import IntegrityError

from app.models import (
    Enrollment,
    Grade,
    Installation,
    Nationality,
    Role,
    Semester,
    Subject,
    User,
)


def make_student(cedula: int, nationality: Nationality = Nationality.VENEZOLANO) -> User:
    return User(
        role=Role.STUDENT,
        full_name=f"Estudiante {cedula}",
        nationality=nationality,
        cedula=cedula,
        password_hash="x",
    )


def make_staff(email: str, role: Role = Role.TEACHER) -> User:
    return User(role=role, full_name=email, email=email, password_hash="x")


async def seed_enrollment(session) -> Enrollment:
    student = make_student(1)
    semester = Semester(name="2026-I")
    subject = Subject(code="MAT101", name="Matemática I", semester=semester)
    session.add_all([student, subject])
    await session.flush()
    enrollment = Enrollment(student_id=student.id, subject_id=subject.id)
    session.add(enrollment)
    await session.flush()
    return enrollment


async def test_cedula_unica_por_nacionalidad(session):
    session.add_all([make_student(123), make_student(123)])
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_misma_cedula_distinta_nacionalidad(session):
    session.add_all([make_student(123), make_student(123, Nationality.EXTRANJERO)])
    await session.flush()


async def test_email_unico(session):
    session.add_all([make_staff("a@x.com"), make_staff("a@x.com", Role.COORDINATOR)])
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_personal_requiere_email(session):
    session.add(User(role=Role.TEACHER, full_name="Sin email", password_hash="x"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_estudiante_requiere_cedula_y_nacionalidad(session):
    session.add(User(role=Role.STUDENT, full_name="Sin cédula", cedula=5, password_hash="x"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_cedula_positiva(session):
    session.add(make_student(0))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_usuario_activo_por_defecto(session):
    user = make_student(9)
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
    admin = make_staff("admin@x.com", Role.SUPER_ADMIN)
    session.add(admin)
    await session.flush()
    session.add(Installation(id=1, super_admin_id=admin.id))
    await session.flush()
    session.add(Installation(id=2, super_admin_id=admin.id))
    with pytest.raises(IntegrityError):
        await session.flush()
