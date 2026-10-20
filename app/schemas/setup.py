from pydantic import BaseModel, Field

from app.schemas.common import Email, NewPassword
from app.schemas.user import FirstName, LastName


class SetupStatus(BaseModel):
    installed: bool = Field(description="True si ya existe el super admin")
    token_required: bool = Field(
        default=False,
        description="True si el instalador pide el código de instalación (cabecera "
        "X-Setup-Token): se configura en el servidor con SETUP_TOKEN",
    )


class SetupRequest(BaseModel):
    first_name: FirstName
    last_name: LastName
    email: Email
    password: NewPassword
