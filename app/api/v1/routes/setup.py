from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.schemas.auth import TokenResponse
from app.schemas.setup import SetupRequest, SetupStatus
from app.services import auth as auth_service
from app.services import setup as setup_service

router = APIRouter(prefix="/setup", tags=["Instalación"])


@router.get("/status", summary="¿El sistema ya está instalado?")
async def setup_status(db: DbSession) -> SetupStatus:
    """El front lo consulta al arrancar: si `installed` es false muestra el instalador."""
    return SetupStatus(installed=await setup_service.is_installed(db))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Crear el super admin (solo la primera vez)",
    responses={404: {"description": "El sistema ya está instalado"}},
)
async def setup(data: SetupRequest, db: DbSession) -> TokenResponse:
    """Crea la cuenta de super admin y devuelve su token.

    Una vez instalado, responde **404** igual que una ruta inexistente.
    """
    try:
        admin = await setup_service.install(
            db, data.first_name, data.last_name, data.email, data.password
        )
    except setup_service.AlreadyInstalledError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from None
    return auth_service.issue_token(admin)
