from pydantic import BaseModel, Field

from app.models import Role
from app.schemas.common import CedulaNumber, DocumentCode, Email, NewPassword


class StaffLogin(BaseModel):
    """Inicio de sesión de super admin, coordinadores y profesores."""

    email: Email
    password: str = Field(min_length=1, max_length=128)


class StudentLogin(BaseModel):
    """Inicio de sesión de estudiantes."""

    nationality: DocumentCode
    cedula: CedulaNumber
    password: str = Field(min_length=1, max_length=128)


class BiometricLogin(BaseModel):
    """Inicio de sesión con huella desde la app del móvil."""

    credential: str = Field(min_length=1, max_length=128)


class BiometricCredential(BaseModel):
    device_id: int
    credential: str = Field(
        description="Credencial del teléfono (`<id>.<secreto>`). Se devuelve una sola vez: la "
        "app la guarda y la envía a `/auth/login/biometric` tras verificar la huella"
    )


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Segundos de validez del token")
    role: Role
    must_change_password: bool = Field(
        description="True si el usuario aún usa la contraseña generada por el sistema"
    )


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: NewPassword
