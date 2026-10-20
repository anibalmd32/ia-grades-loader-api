import re
from collections.abc import Awaitable, Callable, Hashable
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import CurrentToken, CurrentUser, DbSession, PendingUser
from app.models import User
from app.schemas.auth import (
    BiometricCredential,
    BiometricDeviceRead,
    BiometricLogin,
    BiometricRegister,
    ChangePasswordRequest,
    StaffLogin,
    StudentLogin,
    TokenResponse,
)
from app.schemas.user import UserRead
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["Autenticación"])

_CEDULA_RE = re.compile(r"^\s*([A-Z]{1,3})[\s\-]*(\d[\d.]*)\s*$", re.IGNORECASE)

# Antigüedad máxima de la sesión para activar la huella sin volver a pedir la contraseña
BIOMETRIC_FRESH_SESSION = timedelta(minutes=10)

_INVALID = HTTPException(status.HTTP_401_UNAUTHORIZED, "Credenciales incorrectas")
_DISABLED = HTTPException(status.HTTP_403_FORBIDDEN, "La cuenta está desactivada")


def client_ip(request: Request) -> str:
    # Detrás de un proxy, uvicorn (--proxy-headers) ya pone aquí la IP real del cliente
    return request.client.host if request.client else "desconocida"


async def _login(
    request: Request, account: Hashable, attempt: Callable[[], Awaitable[User]]
) -> TokenResponse:
    try:
        user = await auth_service.guarded(account, client_ip(request), attempt)
        return auth_service.issue_token(user)
    except auth_service.InvalidCredentialsError:
        raise _INVALID from None
    except auth_service.AccountDisabledError:
        raise _DISABLED from None


@router.post("/login/staff", summary="Login del personal (email)")
async def login_staff(data: StaffLogin, db: DbSession, request: Request) -> TokenResponse:
    """Super admin, coordinadores y profesores inician sesión con email y contraseña.

    Tras varios intentos fallidos seguidos (por cuenta o por IP) responde 429 con
    `Retry-After`."""
    return await _login(
        request,
        ("staff", data.email.lower()),
        lambda: auth_service.authenticate_staff(db, data.email, data.password),
    )


@router.post("/login/student", summary="Login de estudiantes (cédula)")
async def login_student(data: StudentLogin, db: DbSession, request: Request) -> TokenResponse:
    """Los estudiantes inician sesión con tipo (V/E), número de cédula y contraseña.

    Tras varios intentos fallidos seguidos responde 429 con `Retry-After`."""
    return await _login(
        request,
        ("student", data.nationality, data.cedula),
        lambda: auth_service.authenticate_student(db, data.nationality, data.cedula, data.password),
    )


@router.post("/login/biometric", summary="Login con huella (app del móvil)")
async def login_biometric(data: BiometricLogin, db: DbSession, request: Request) -> TokenResponse:
    """La app del móvil envía la credencial que recibió al activar el inicio con huella
    (`POST /auth/biometric`), solo después de que el usuario verifique su huella o cara."""
    device = data.credential.partition(".")[0]
    return await _login(
        request,
        ("biometric", device),
        lambda: auth_service.authenticate_biometric(db, data.credential),
    )


@router.post(
    "/biometric",
    status_code=status.HTTP_201_CREATED,
    summary="Activar el inicio de sesión con huella en este teléfono",
)
async def register_biometric(
    user: CurrentUser,
    token: CurrentToken,
    db: DbSession,
    request: Request,
    data: BiometricRegister | None = None,
) -> BiometricCredential:
    """Devuelve la credencial del teléfono una sola vez. Cerrar sesión no la invalida;
    cambiar o restablecer la contraseña, sí.

    Sin `current_password` solo vale con una sesión abierta hace menos de 10 minutos (la app
    lo activa justo después del login); si no, responde 403 y hay que enviar la contraseña.
    Cada usuario puede tener hasta 5 teléfonos: al activar otro, se borra el más antiguo."""
    password = data.current_password if data else None
    if password is not None:

        async def attempt() -> User:
            if not await auth_service.verify_user_password(user, password):
                raise auth_service.InvalidCredentialsError
            return user

        try:
            await auth_service.guarded(("password", user.id), client_ip(request), attempt)
        except auth_service.InvalidCredentialsError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "La contraseña no es correcta"
            ) from None
    elif auth_service.session_age(token) > BIOMETRIC_FRESH_SESSION:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Confirma tu contraseña para activar el inicio de sesión con huella",
        )
    device_id, credential = await auth_service.register_biometric_device(db, user)
    return BiometricCredential(device_id=device_id, credential=credential)


@router.get("/biometric", summary="Teléfonos con el inicio de sesión con huella activado")
async def list_biometric(user: CurrentUser, db: DbSession) -> list[BiometricDeviceRead]:
    """Del más reciente al más antiguo. Sirve para desactivar uno que ya no se usa."""
    devices = await auth_service.list_biometric_devices(db, user)
    return [BiometricDeviceRead.model_validate(d) for d in devices]


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
    form: Annotated[OAuth2PasswordRequestForm, Depends()], db: DbSession, request: Request
) -> TokenResponse:
    """Usado por el botón **Authorize** de Swagger.

    En `username` escribe el email (personal) o la cédula con su tipo, p. ej. `V-12345678`
    (estudiantes). El front debe usar `/login/staff` o `/login/student`.
    """
    if len(form.username) > 254 or len(form.password) > 128:
        raise _INVALID
    if "@" in form.username:
        return await _login(
            request,
            ("staff", form.username.lower()),
            lambda: auth_service.authenticate_staff(db, form.username, form.password),
        )
    match = _CEDULA_RE.match(form.username)
    if match is None:
        raise _INVALID
    nationality = match.group(1).upper()
    cedula = int(match.group(2).replace(".", ""))
    return await _login(
        request,
        ("student", nationality, cedula),
        lambda: auth_service.authenticate_student(db, nationality, cedula, form.password),
    )


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
async def me(user: PendingUser) -> UserRead:
    return UserRead.model_validate(user)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cambiar la contraseña propia",
)
async def change_password(
    data: ChangePasswordRequest, user: PendingUser, db: DbSession, request: Request
) -> None:
    """Cierra **todas** las sesiones del usuario, incluida la actual: después hay que volver
    a iniciar sesión con la contraseña nueva."""

    async def attempt() -> User:
        await auth_service.change_password(db, user, data.current_password, data.new_password)
        return user

    try:
        # Con un token robado no se puede probar contraseñas sin límite
        await auth_service.guarded(("password", user.id), client_ip(request), attempt)
    except auth_service.InvalidCredentialsError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "La contraseña actual no es correcta"
        ) from None
