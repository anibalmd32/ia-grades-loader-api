from pydantic import BaseModel, Field

from app.schemas.common import Email, NewPassword


class SetupStatus(BaseModel):
    installed: bool = Field(description="True si ya existe el super admin")


class SetupRequest(BaseModel):
    full_name: str = Field(min_length=3, max_length=150)
    email: Email
    password: NewPassword
