from pydantic import BaseModel, ConfigDict, Field

from app.models import MessageRole
from app.schemas.import_jobs import UtcDatetime


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000, examples=["¿En qué materia voy peor?"])
    conversation_id: int | None = Field(
        default=None, description="Conversación a continuar; vacío = empezar una nueva"
    )


class ChatResponse(BaseModel):
    conversation_id: int
    answer: str


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str | None = Field(description="Primera pregunta de la conversación")
    created_at: UtcDatetime
    updated_at: UtcDatetime


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: MessageRole
    content: str
    created_at: UtcDatetime


class ConversationRead(ConversationSummary):
    messages: list[MessageRead]
