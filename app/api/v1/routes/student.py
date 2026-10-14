"""Consulta de notas por el estudiante autenticado."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from fastapi.sse import EventSourceResponse, ServerSentEvent

from app.ai.client import LLMClient, get_llm
from app.api.deps import DbSession, require_roles
from app.core.config import get_settings
from app.core.errors import AppError
from app.models import ChatChannel, Conversation, Role, User
from app.schemas.academic import SubjectRead
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationRead,
    ConversationSummary,
)
from app.schemas.common import Page
from app.schemas.grades import (
    StudentOverview,
    StudentSubjectGrades,
    SubjectProjection,
    TermGrade,
)
from app.schemas.telegram import TelegramLinkInvite, TelegramStatus
from app.services import chat, conversations, student_grades
from app.services import telegram_link as telegram_link_service
from app.services.student_grades import SubjectGrades

router = APIRouter(prefix="/student", tags=["Estudiante"])

Student = Annotated[User, Depends(require_roles(Role.STUDENT))]


def _to_schema(item: SubjectGrades) -> StudentSubjectGrades:
    return StudentSubjectGrades(
        subject=SubjectRead.model_validate(item.subject),
        teachers=item.teachers,
        grades=[TermGrade(term=t, value=v) for t, v in item.grades.items()],
        average=item.average,
        projection=SubjectProjection.model_validate(item.projection, from_attributes=True),
    )


@router.get("/subjects", summary="Mis materias y notas")
async def my_subjects(student: Student, db: DbSession) -> StudentOverview:
    """Todas las materias en las que estás matriculado, con la nota de cada lapso
    (null si aún no se ha cargado), el promedio de cada una y el promedio general."""
    subjects = await student_grades.student_subjects(db, student.id)
    return StudentOverview(
        subjects=[_to_schema(s) for s in subjects],
        average=student_grades.overall_average(subjects),
    )


@router.get("/subjects/{subject_id}", summary="Detalle de una materia")
async def my_subject(subject_id: int, student: Student, db: DbSession) -> StudentSubjectGrades:
    return _to_schema(await student_grades.student_subject(db, student.id, subject_id))


# --- Chat con el asistente ---------------------------------------------------

Llm = Annotated[LLMClient, Depends(get_llm)]


async def _conversation_for(db, student: User, conversation_id: int | None) -> Conversation:
    if conversation_id is None:
        return await conversations.create(db, student.id, ChatChannel.APP)
    return await conversations.get(db, student.id, conversation_id)


@router.post("/chat", summary="Preguntar por mis notas (respuesta completa)")
async def ask_about_my_grades(
    data: ChatRequest, student: Student, db: DbSession, llm: Llm
) -> ChatResponse:
    """La IA responde usando **solo las notas del estudiante autenticado**.

    Sin `conversation_id` se empieza una conversación nueva; con él, la IA recuerda lo
    hablado en esa conversación. Para ver la respuesta palabra por palabra usa
    `POST /student/chat/stream`.
    """
    conversation = await _conversation_for(db, student, data.conversation_id)
    answer = await chat.ask(db, llm, student, conversation, data.message)
    return ChatResponse(conversation_id=conversation.id, answer=answer)


@router.post(
    "/chat/stream",
    response_class=EventSourceResponse,
    summary="Preguntar por mis notas (streaming, palabra por palabra)",
)
async def ask_about_my_grades_stream(
    data: ChatRequest, student: Student, db: DbSession, llm: Llm
) -> AsyncIterator[ServerSentEvent]:
    """Respuesta en **Server-Sent Events** (`text/event-stream`). Eventos, en orden:

    - `conversation`: `{"id", "title"}` (guárdalo para continuar la conversación)
    - `delta`: `{"text"}` fragmento de la respuesta; concaténalos según llegan
    - `done`: `{}` la respuesta terminó (ya está guardada)
    - `error`: `{"detail"}` si algo falla (p. ej. la IA no responde)
    """
    conversation = await _conversation_for(db, student, data.conversation_id)
    yield ServerSentEvent(
        event="conversation",
        data={"id": conversation.id, "title": conversation.title or data.message[:60]},
    )
    try:
        async for text in chat.ask_stream(db, llm, student, conversation, data.message):
            yield ServerSentEvent(event="delta", data={"text": text})
    except AppError as exc:
        yield ServerSentEvent(event="error", data={"detail": exc.detail})
        return
    yield ServerSentEvent(event="done", data={})


@router.get("/conversations", summary="Mis conversaciones")
async def my_conversations(
    student: Student,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ConversationSummary]:
    """Conversaciones de la app, de la más reciente a la más antigua."""
    items, total = await conversations.list_for(db, student.id, ChatChannel.APP, limit, offset)
    return Page(
        items=[ConversationSummary.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/conversations/{conversation_id}", summary="Mensajes de una conversación")
async def my_conversation(
    conversation_id: int, student: Student, db: DbSession
) -> ConversationRead:
    conversation = await conversations.get(db, student.id, conversation_id)
    messages = await conversations.messages(db, conversation.id)
    return ConversationRead.model_validate(
        {**ConversationSummary.model_validate(conversation).model_dump(), "messages": messages}
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar una conversación",
)
async def delete_conversation(conversation_id: int, student: Student, db: DbSession) -> None:
    await conversations.remove(db, student.id, conversation_id)


# --- Telegram ----------------------------------------------------------------


@router.get("/telegram", summary="Estado de la vinculación con Telegram")
async def telegram_status(student: Student) -> TelegramStatus:
    settings = get_settings()
    return TelegramStatus(
        enabled=settings.telegram_enabled,
        linked=student.telegram_linked,
        bot_username=settings.telegram_bot_username.lstrip("@") or None,
    )


@router.post("/telegram/link", summary="Generar enlace para vincular Telegram")
async def telegram_link(student: Student, db: DbSession) -> TelegramLinkInvite:
    """Devuelve un enlace `t.me/<bot>?start=<código>` de un solo uso. Al abrirlo y pulsar
    **Iniciar** en Telegram, la cuenta queda vinculada y se pueden hacer preguntas sobre las
    notas desde allí. Generar un enlace nuevo invalida el anterior."""
    invite = await telegram_link_service.create_link(db, student)
    return TelegramLinkInvite(url=invite.url, expires_at=invite.expires_at)


@router.delete("/telegram", status_code=status.HTTP_204_NO_CONTENT, summary="Desvincular Telegram")
async def telegram_unlink(student: Student, db: DbSession) -> None:
    await telegram_link_service.unlink(db, student)
