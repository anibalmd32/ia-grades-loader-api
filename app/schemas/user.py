from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.models import Nationality, Role
from app.schemas.common import CedulaNumber, Email

FullName = Annotated[str, AfterValidator(str.strip), Field(min_length=3, max_length=150)]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Role
    full_name: str
    email: str | None
    nationality: Nationality | None
    cedula: int | None
    cedula_display: str | None = Field(description="Cédula con formato, p. ej. V-12345678")
    is_active: bool
    must_change_password: bool
    telegram_linked: bool
    subjects_count: int | None = Field(
        default=None,
        description="Materias asignadas (profesor) o matriculadas (estudiante). Solo en el "
        "listado y el detalle de profesores y estudiantes; null en coordinadores y dentro de "
        "otras respuestas.",
    )


class StaffCreate(BaseModel):
    """Alta de coordinador o profesor."""

    full_name: FullName
    email: Email


class StaffUpdate(BaseModel):
    full_name: FullName | None = None
    email: Email | None = None
    is_active: bool | None = None


class StudentCreate(BaseModel):
    full_name: FullName
    nationality: Nationality = Field(description="V = venezolano, E = extranjero")
    cedula: CedulaNumber


class StudentUpdate(BaseModel):
    full_name: FullName | None = None
    nationality: Nationality | None = None
    cedula: CedulaNumber | None = None
    is_active: bool | None = None


class UserCreated(BaseModel):
    user: UserRead
    generated_password: str = Field(
        description="Contraseña inicial. Solo se muestra esta vez: entrégala al usuario."
    )


class PasswordReset(BaseModel):
    generated_password: str = Field(
        description="Nueva contraseña. Solo se muestra esta vez: entrégala al usuario."
    )
