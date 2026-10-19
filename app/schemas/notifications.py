from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Page
from app.schemas.grades import UtcDatetime


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Creciente: sirve para saber cuáles son nuevas")
    title: str
    body: str
    subject_id: int | None = Field(description="Materia a la que se refiere")
    created_at: UtcDatetime
    read_at: UtcDatetime | None = Field(description="null = sin leer")


class NotificationPage(Page[NotificationRead]):
    unread: int = Field(description="Avisos sin leer en total")


class NotificationsRead(BaseModel):
    ids: list[int] | None = Field(
        default=None, description="Avisos a marcar como leídos; sin enviar = todos"
    )


class UnreadCount(BaseModel):
    unread: int
