from pydantic import BaseModel, ConfigDict, Field

from app.models import Nationality, Role


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
