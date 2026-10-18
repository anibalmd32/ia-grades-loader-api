from pydantic import BaseModel, Field

from app.schemas.import_jobs import UtcDatetime


class TelegramStatus(BaseModel):
    enabled: bool = Field(
        description="El bot está configurado en el servidor y la institución lo tiene activado"
    )
    linked: bool = Field(description="El estudiante ya vinculó su Telegram")
    bot_username: str | None


class TelegramLinkInvite(BaseModel):
    url: str = Field(description="Abrir en el móvil: lleva al bot con el código ya puesto")
    expires_at: UtcDatetime
