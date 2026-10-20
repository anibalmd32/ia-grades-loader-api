import hmac
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.api.deps import DbSession
from app.api.v1.routes.auth import client_ip
from app.core.config import get_settings
from app.models import User
from app.schemas.auth import TokenResponse
from app.schemas.setup import SetupRequest, SetupStatus
from app.services import auth as auth_service
from app.services import setup as setup_service

router = APIRouter(prefix="/setup", tags=["Instalación"])


@router.get("/status", summary="¿El sistema ya está instalado?")
async def setup_status(db: DbSession) -> SetupStatus:
    """El front lo consulta al arrancar: si `installed` es false muestra el instalador."""
    return SetupStatus(
        installed=await setup_service.is_installed(db), token_required=_token_required()
    )


def _token_required() -> bool:
    settings = get_settings()
    return bool(settings.setup_token.get_secret_value()) or settings.app_env == "production"


def _check_setup_token(received: str | None) -> None:
    """El código de instalación, si el servidor lo exige. En producción es obligatorio."""
    expected = get_settings().setup_token.get_secret_value()
    if not expected:
        if _token_required():
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Instalación bloqueada: define SETUP_TOKEN en el servidor y escríbelo aquí.",
            )
        return
    if received is None or not hmac.compare_digest(received.encode(), expected.encode()):
        raise auth_service.InvalidCredentialsError


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Crear el super admin (solo la primera vez)",
    responses={
        403: {"description": "Falta o no coincide el código de instalación"},
        404: {"description": "El sistema ya está instalado"},
    },
)
async def setup(
    data: SetupRequest,
    db: DbSession,
    request: Request,
    setup_token: Annotated[str | None, Header(alias="X-Setup-Token", max_length=256)] = None,
) -> TokenResponse:
    """Crea la cuenta de super admin y devuelve su token.

    Si el servidor tiene `SETUP_TOKEN` (obligatorio con `APP_ENV=production`), hay que enviarlo
    en la cabecera `X-Setup-Token`: así nadie puede adelantarse a instalar una API recién
    desplegada. Una vez instalado, responde **404** igual que una ruta inexistente.
    """
    if await setup_service.is_installed(db):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")

    async def attempt() -> User:
        _check_setup_token(setup_token)
        return User()

    try:
        # Con límite de intentos fallidos, como el login
        await auth_service.guarded(("setup",), client_ip(request), attempt)
    except auth_service.InvalidCredentialsError:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "El código de instalación no es correcto"
        ) from None
    try:
        admin = await setup_service.install(
            db, data.first_name, data.last_name, data.email, data.password
        )
    except setup_service.AlreadyInstalledError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found") from None
    return auth_service.issue_token(admin)
