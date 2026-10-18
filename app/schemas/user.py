from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.models import Role
from app.schemas.common import CedulaNumber, DocumentCode, Email

FirstName = Annotated[
    str,
    AfterValidator(str.strip),
    Field(min_length=1, max_length=100, description="Nombre(s)", examples=["Ana María"]),
]
LastName = Annotated[
    str,
    AfterValidator(str.strip),
    Field(min_length=1, max_length=100, description="Apellido(s)", examples=["Pérez Gómez"]),
]


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Role
    first_name: str
    last_name: str
    full_name: str = Field(description="Nombre y apellido")
    email: str | None
    nationality: str | None = Field(description="Tipo de documento (V, E...)")
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

    first_name: FirstName
    last_name: LastName
    email: Email


class StaffUpdate(BaseModel):
    first_name: FirstName | None = None
    last_name: LastName | None = None
    email: Email | None = None
    is_active: bool | None = None


class StudentCreate(BaseModel):
    first_name: FirstName
    last_name: LastName
    nationality: DocumentCode
    cedula: CedulaNumber


class StudentUpdate(BaseModel):
    first_name: FirstName | None = None
    last_name: LastName | None = None
    nationality: DocumentCode | None = None
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
