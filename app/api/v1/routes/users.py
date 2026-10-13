"""Routers de cuentas: coordinadores, profesores y estudiantes comparten la misma forma."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

from app.api.deps import DbSession, require_roles
from app.models import Role
from app.schemas.common import Page
from app.schemas.user import (
    PasswordReset,
    StaffCreate,
    StaffUpdate,
    StudentCreate,
    StudentUpdate,
    UserCreated,
    UserRead,
)
from app.services import users


def build_user_router(
    *,
    role: Role,
    prefix: str,
    tag: str,
    manager: Role,
    create_schema: type[BaseModel],
    update_schema: type[BaseModel],
    singular: str,
    plural: str,
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag], dependencies=[Depends(require_roles(manager))])

    @router.get("", summary=f"Listar {plural}")
    async def list_users(
        db: DbSession,
        q: Annotated[str | None, Query(description="Busca por nombre, email o cédula")] = None,
        is_active: bool | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Page[UserRead]:
        items, total = await users.list_users(
            db, role, q=q, is_active=is_active, limit=limit, offset=offset
        )
        return Page(
            items=[UserRead.model_validate(u) for u in items],
            total=total,
            limit=limit,
            offset=offset,
        )

    @router.post("", status_code=status.HTTP_201_CREATED, summary=f"Crear {singular}")
    async def create_user(data: create_schema, db: DbSession) -> UserCreated:
        """La contraseña se genera automáticamente y **solo se muestra en esta respuesta**.
        El usuario deberá cambiarla en su primer inicio de sesión."""
        user, password = await users.create_user(db, role, data.model_dump())
        return UserCreated(user=UserRead.model_validate(user), generated_password=password)

    @router.get("/{user_id}", summary=f"Ver {singular}")
    async def get_user(user_id: int, db: DbSession) -> UserRead:
        return UserRead.model_validate(await users.get_user(db, role, user_id))

    @router.patch("/{user_id}", summary=f"Modificar o desactivar {singular}")
    async def update_user(user_id: int, data: update_schema, db: DbSession) -> UserRead:
        """Envía `is_active: false` para desactivar la cuenta (no se borra)."""
        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        return UserRead.model_validate(await users.update_user(db, role, user_id, changes))

    @router.post("/{user_id}/reset-password", summary="Restablecer contraseña")
    async def reset_password(user_id: int, db: DbSession) -> PasswordReset:
        """Genera una contraseña nueva que **solo se muestra en esta respuesta**."""
        return PasswordReset(generated_password=await users.reset_password(db, role, user_id))

    return router


coordinators_router = build_user_router(
    role=Role.COORDINATOR,
    prefix="/coordinators",
    tag="Coordinadores",
    manager=Role.SUPER_ADMIN,
    create_schema=StaffCreate,
    update_schema=StaffUpdate,
    singular="coordinador",
    plural="coordinadores",
)

teachers_router = build_user_router(
    role=Role.TEACHER,
    prefix="/teachers",
    tag="Profesores",
    manager=Role.COORDINATOR,
    create_schema=StaffCreate,
    update_schema=StaffUpdate,
    singular="profesor",
    plural="profesores",
)

students_router = build_user_router(
    role=Role.STUDENT,
    prefix="/students",
    tag="Estudiantes",
    manager=Role.COORDINATOR,
    create_schema=StudentCreate,
    update_schema=StudentUpdate,
    singular="estudiante",
    plural="estudiantes",
)
