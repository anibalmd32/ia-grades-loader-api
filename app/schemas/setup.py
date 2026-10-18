from pydantic import BaseModel, Field

from app.schemas.common import Email, NewPassword
from app.schemas.user import FirstName, LastName


class SetupStatus(BaseModel):
    installed: bool = Field(description="True si ya existe el super admin")


class SetupRequest(BaseModel):
    first_name: FirstName
    last_name: LastName
    email: Email
    password: NewPassword
