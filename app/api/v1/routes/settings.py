"""Configuración académica: periodos, lapsos y escala de notas."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, require_roles
from app.models import Role, User
from app.schemas.settings import AcademicSettingsRead, AcademicSettingsUpdate
from app.services import rules

router = APIRouter(prefix="/settings", tags=["Configuración"])


@router.get("/academic", summary="Reglas académicas vigentes")
async def get_academic_settings(db: DbSession) -> AcademicSettingsRead:
    """Pública (sin sesión): la app la usa para mostrar los nombres de periodos y lapsos y
    la escala de notas en todas las pantallas, incluido el login."""
    return AcademicSettingsRead.from_rules(await rules.get_rules(db))


@router.put("/academic", summary="Cambiar las reglas académicas (solo super admin)")
async def update_academic_settings(
    data: AcademicSettingsUpdate,
    db: DbSession,
    _: Annotated[User, Depends(require_roles(Role.SUPER_ADMIN))],
) -> AcademicSettingsRead:
    """Reemplaza la configuración completa. Afecta a la carga de notas, la consulta de los
    estudiantes, la importación con IA y el asistente.

    Responde **409** si alguna nota ya cargada dejaría de ser válida (p. ej. reducir el
    número de lapsos cuando ya hay notas en el último, o cambiar la escala dejando notas
    fuera de ella).
    """
    return AcademicSettingsRead.from_rules(await rules.update_rules(db, data.to_rules()))
