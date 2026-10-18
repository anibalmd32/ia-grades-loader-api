"""Gestión de cuentas de coordinadores, profesores y estudiantes."""

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.core.security import generate_password, hash_password
from app.models import Enrollment, Nationality, Role, TeacherSubject, User

ROLE_NAMES = {
    Role.COORDINATOR: "Coordinador",
    Role.TEACHER: "Profesor",
    Role.STUDENT: "Estudiante",
}


async def get_user(db: AsyncSession, role: Role, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None or user.role != role:
        raise NotFoundError(f"{ROLE_NAMES[role]} no encontrado")
    return user


async def subjects_per_user(db: AsyncSession, role: Role, user_ids: list[int]) -> dict[int, int]:
    """Materias asignadas (profesores) o matriculadas (estudiantes) de cada usuario, en una
    sola consulta. Los coordinadores no tienen materias: devuelve {}."""
    match role:
        case Role.TEACHER:
            column = TeacherSubject.teacher_id
        case Role.STUDENT:
            column = Enrollment.student_id
        case _:
            return {}
    rows = await db.execute(
        select(column, func.count()).where(column.in_(user_ids)).group_by(column)
    )
    return {uid: 0 for uid in user_ids} | dict(rows.all())


async def _ensure_unique(db: AsyncSession, fields: dict[str, Any], exclude_id: int | None = None):
    checks = []
    if fields.get("email"):
        checks.append(
            (User.email == fields["email"], f"Ya existe un usuario con el email {fields['email']}")
        )
    if fields.get("nationality") and fields.get("cedula"):
        nationality = Nationality(fields["nationality"])
        checks.append(
            (
                (User.nationality == nationality) & (User.cedula == fields["cedula"]),
                f"Ya existe un usuario con la cédula {nationality.value}-{fields['cedula']}",
            )
        )
    for condition, message in checks:
        query = select(User.id).where(condition)
        if exclude_id is not None:
            query = query.where(User.id != exclude_id)
        if await db.scalar(query) is not None:
            raise ConflictError(message)


async def list_users(
    db: AsyncSession,
    role: Role,
    *,
    q: str | None,
    is_active: bool | None,
    limit: int,
    offset: int,
) -> tuple[list[User], int]:
    query = select(User).where(User.role == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
    if q and (term := q.strip()):
        conditions = [User.full_name.ilike(f"%{term}%"), User.email.ilike(f"%{term}%")]
        digits = term.replace(".", "").replace("-", "").lstrip("VvEe ")
        if digits.isdigit():
            conditions.append(User.cedula == int(digits))
        query = query.where(or_(*conditions))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    items = await db.scalars(query.order_by(User.full_name).limit(limit).offset(offset))
    return list(items), total or 0


async def create_user(db: AsyncSession, role: Role, fields: dict[str, Any]) -> tuple[User, str]:
    """Crea la cuenta con una contraseña generada que el usuario deberá cambiar."""
    await _ensure_unique(db, fields)
    password = generate_password()
    user = User(
        role=role,
        password_hash=hash_password(password),
        must_change_password=True,
        **fields,
    )
    db.add(user)
    await db.commit()
    return user, password


async def update_user(db: AsyncSession, role: Role, user_id: int, changes: dict[str, Any]) -> User:
    user = await get_user(db, role, user_id)
    if "email" in changes:
        await _ensure_unique(db, {"email": changes["email"]}, exclude_id=user_id)
    if changes.keys() & {"nationality", "cedula"}:
        # La cédula es única junto a su tipo: se valida la combinación resultante
        cedula = {
            "nationality": changes.get("nationality", user.nationality),
            "cedula": changes.get("cedula", user.cedula),
        }
        await _ensure_unique(db, cedula, exclude_id=user_id)
    for field, value in changes.items():
        setattr(user, field, value)
    await db.commit()
    return user


async def reset_password(db: AsyncSession, role: Role, user_id: int) -> str:
    user = await get_user(db, role, user_id)
    password = generate_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.token_version += 1  # cierra todas sus sesiones abiertas
    await db.commit()
    return password
