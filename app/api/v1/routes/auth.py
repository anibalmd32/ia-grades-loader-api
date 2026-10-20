import re
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import CurrentToken, CurrentUser, DbSession
from app.schemas.auth import (
    BiometricCredential,
    BiometricLogin,
    ChangePasswordRequest,
    StaffLogin,
    StudentLogin,
    TokenResponse,
)
from app.schemas.user import UserRead
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["Autenticación"])

_CEDULA_RE = re.compile(r"^\s*([A-Z]{1,3})[\s\-]*(\d[\d.]*)\s*$", re.IGNORECASE)

_INVALID = HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales incorrectas")
_DISABLED = HTTPException(status.HTTP_403_FORBIDDEN, "La cuenta está desactivada")


async def _login(coro) -> TokenResponse:
    try:
        return auth_service.issue_token(await coro)
    except auth_service.InvalidCredentialsError:
        raise _INVALID from None
    except auth_service.AccountDisabledError:
        raise _DISABLED from None


@router.post("/login/staff", summary="Login del personal (email)")
async def login_staff(data: StaffLogin, db: DbSession) -> TokenResponse:
    """Super admin, coordinadores y profesores inician sesión con email y contraseña."""
    return await _login(auth_service.authenticate_staff(db, data.email, data.password))


@router.post("/login/student", summary="Login de estudiantes (cédula)")
async def login_student(data: StudentLogin, db: DbSession) -> TokenResponse:
    """Los estudiantes inician sesión con tipo (V/E), número de cédula y contraseña."""
    return await _login(
        auth_service.authenticate_student(db, data.nationality, data.cedula, data.password)
    )


@router.post("/login/biometric", summary="Login con huella (app del móvil)")
async def login_biometric(data: BiometricLogin, db: DbSession) -> TokenResponse:
    """La app del móvil envía la credencial que recibió al activar el inicio con huella
    (`POST /auth/biometric`), solo después de que el usuario verifique su huella o cara."""
    return await _login(auth_service.authenticate_biometric(db, data.credential))


@router.post(
    "/biometric",
    status_code=status.HTTP_201_CREATED,
    summary="Activar el inicio de sesión con huella en este teléfono",
)
async def register_biometric(user: CurrentUser, db: DbSession) -> BiometricCredential:
    """Devuelve la credencial del teléfono una sola vez. Cerrar sesión no la invalida;
    cambiar o restablecer la contraseña, sí."""
    device_id, credential = await auth_service.register_biometric_device(db, user)
    return BiometricCredential(device_id=device_id, credential=credential)


@router.delete(
    "/biometric/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Desactivar el inicio de sesión con huella en un teléfono",
)
async def remove_biometric(device_id: int, user: CurrentUser, db: DbSession) -> None:
    if not await auth_service.remove_biometric_device(db, user, device_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dispositivo no encontrado")


@router.post("/token", summary="Login para Swagger (OAuth2)")
async def login_oauth2(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession
) -> TokenResponse:
    """Usado por el botón **Authorize** de Swagger.

    En `username` escribe el email (personal) o la cédula con su tipo, p. ej. `V-12345678`
    (estudiantes). El front debe usar `/login/staff` o `/login/student`.
    """
    if "@" in form.username:
        return await _login(auth_service.authenticate_staff(db, form.username, form.password))
    match = _CEDULA_RE.match(form.username)
    if match is None:
        raise _INVALID
    nationality = match.group(1).upper()
    cedula = int(match.group(2).replace(".", ""))
    return await _login(auth_service.authenticate_student(db, nationality, cedula, form.password))


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cerrar sesión (revoca el token actual)",
)
async def logout(token: CurrentToken, db: DbSession) -> None:
    """Invalida el token con el que se hace la petición; deja de servir de inmediato.

    En Swagger, después de llamarlo pulsa **Authorize → Logout** para que deje de enviarlo.
    """
    await auth_service.logout(db, token)


@router.get("/me", summary="Usuario autenticado")
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cambiar la contraseña propia",
)
async def change_password(data: ChangePasswordRequest, user: CurrentUser, db: DbSession) -> None:
    """Cierra **todas** las sesiones del usuario, incluida la actual: después hay que volver
    a iniciar sesión con la contraseña nueva."""
    try:
        await auth_service.change_password(db, user, data.current_password, data.new_password)
    except auth_service.InvalidCredentialsError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "La contraseña actual no es correcta"
        ) from None
