"""Consulta de notas por el estudiante autenticado."""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from fastapi.sse import EventSourceResponse, ServerSentEvent, format_sse_event
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import LLMClient, get_llm
from app.api.deps import DbSession, oauth2_scheme, require_roles
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.rate_limit import RateLimitedError
from app.db.session import get_db
from app.models import ChatChannel, Conversation, Role, User
from app.schemas.academic import SemesterRead, SubjectRead
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    ConversationRead,
    ConversationSummary,
)
from app.schemas.common import Page
from app.schemas.grades import (
    SemesterAverage,
    StudentEvaluation,
    StudentOverview,
    StudentSubjectGrades,
    SubjectProjection,
    TermGrade,
)
from app.schemas.notifications import (
    NotificationPage,
    NotificationRead,
    NotificationsRead,
    UnreadCount,
)
from app.schemas.telegram import TelegramLinkInvite, TelegramStatus
from app.services import auth as auth_service
from app.services import chat, conversations, notifications, rules, student_grades
from app.services import telegram_link as telegram_link_service
from app.services.notification_stream import broker
from app.services.student_grades import Standing, SubjectGrades

router = APIRouter(prefix="/student", tags=["Estudiante"])

Student = Annotated[User, Depends(require_roles(Role.STUDENT))]


def _to_schema(item: SubjectGrades) -> StudentSubjectGrades:
    return StudentSubjectGrades(
        subject=SubjectRead.model_validate(item.subject),
        teachers=item.teachers,
        grades=[TermGrade(term=t, value=v) for t, v in item.grades.items()],
        evaluations=[
            StudentEvaluation(
                term=e.term, name=e.name, weight=e.weight, due_date=e.due_date, value=value
            )
            for e, value in item.evaluations
        ],
        average=item.average,
        projection=SubjectProjection.model_validate(item.projection, from_attributes=True),
    )


@router.get("/subjects", summary="Mis materias y notas")
async def my_subjects(student: Student, db: DbSession) -> StudentOverview:
    """Todas las materias en las que estás matriculado, con la nota de cada lapso
    (null si aún no se ha cargado), el promedio de cada una, el de cada semestre y el
    promedio general."""
    subjects = await student_grades.student_subjects(db, student.id, await rules.get_rules(db))
    return StudentOverview(
        subjects=[_to_schema(s) for s in subjects],
        average=student_grades.overall_average(subjects),
        semesters=[
            SemesterAverage(
                semester=SemesterRead.model_validate(group.semester),
                average=group.average,
                subjects_count=len(group.subjects),
                passed_count=group.count(Standing.PASSED),
                failed_count=group.count(Standing.FAILED),
            )
            for group in student_grades.by_semester(subjects)
        ],
    )


@router.get("/subjects/{subject_id}", summary="Detalle de una materia")
async def my_subject(subject_id: int, student: Student, db: DbSession) -> StudentSubjectGrades:
    item = await student_grades.student_subject(
        db, student.id, subject_id, await rules.get_rules(db)
    )
    return _to_schema(item)


# --- Avisos ----------------------------------------------------------------


@router.get("/notifications", summary="Mis avisos")
async def my_notifications(
    student: Student,
    db: DbSession,
    unread_only: Annotated[bool, Query(description="Solo los no leídos")] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationPage:
    """Avisos de notas nuevas o corregidas en tus materias, del más reciente al más
    antiguo. La app los consulta cada cierto tiempo y muestra como notificación del sistema
    los que tienen un `id` mayor que el último que mostró."""
    items, total, unread = await notifications.list_notifications(
        db, student.id, unread_only=unread_only, limit=limit, offset=offset
    )
    return NotificationPage(
        items=[NotificationRead.model_validate(n) for n in items],
        total=total,
        limit=limit,
        offset=offset,
        unread=unread,
    )


# Ping para que proxies y navegadores no corten la conexión por inactividad, y duración
# máxima: la app vuelve a conectar sola (y así se revalida la sesión)
STREAM_PING_SECONDS = 20.0
STREAM_MAX_SECONDS = 30 * 60.0


async def _stream_student(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User:
    """Como `Student`, pero la sesión de BD se cierra al validar el token: la conexión de
    tiempo real dura minutos y no debe retener una conexión a la base de datos."""
    session = await auth_service.user_from_token(db, token)
    if session is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "No autenticado o sesión expirada",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = session[1]
    if user.role != Role.STUDENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permiso para esta acción")
    return user


async def _notification_events(request: Request, user_id: int) -> AsyncIterator[bytes]:
    with broker.subscribe(user_id) as queue:
        # retry: si se corta, el cliente vuelve a conectar a los 5 s
        yield format_sse_event(event="ready", data_str="{}", retry=5000)
        deadline = time.monotonic() + STREAM_MAX_SECONDS
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                payload = await asyncio.wait_for(
                    queue.get(), timeout=min(STREAM_PING_SECONDS, remaining)
                )
            except TimeoutError:
                if await request.is_disconnected():
                    return
                yield format_sse_event(comment="ping")
                continue
            yield format_sse_event(event="notification", data_str=json.dumps(payload))


@router.get(
    "/notifications/stream",
    summary="Mis avisos en tiempo real (Server-Sent Events)",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def notifications_stream(
    request: Request,
    student: Annotated[User, Depends(_stream_student, scope="function")],
) -> StreamingResponse:
    """Conexión abierta (`text/event-stream`) por la que llega cada aviso nuevo **del
    estudiante autenticado** en cuanto se guarda la nota. Eventos: `ready` al conectar,
    `notification` (mismo formato que `GET /student/notifications`) y comentarios `ping`
    cada 20 s. Se cierra a los 30 min; el cliente debe volver a conectar. Los avisos
    siguen en `GET /student/notifications` por si se pierde alguno sin conexión."""
    return StreamingResponse(
        _notification_events(request, student.id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/notifications/read", summary="Marcar avisos como leídos")
async def read_notifications(
    data: NotificationsRead, student: Student, db: DbSession
) -> UnreadCount:
    """Sin `ids` marca todos. Devuelve cuántos quedan sin leer."""
    return UnreadCount(unread=await notifications.mark_read(db, student.id, data.ids))


# --- Chat con el asistente ---------------------------------------------------

Llm = Annotated[LLMClient, Depends(get_llm)]


async def _conversation_for(db, student: User, conversation_id: int | None) -> Conversation:
    await chat.ensure_enabled(db, ChatChannel.APP)
    if conversation_id is None:
        return await conversations.create(db, student.id, ChatChannel.APP)
    return await conversations.get(db, student.id, conversation_id)


RATE_LIMITED_RESPONSE = {
    429: {
        "description": "Se superó el límite de preguntas por minuto",
        "headers": {
            "Retry-After": {
                "description": "Segundos que hay que esperar antes de volver a preguntar",
                "schema": {"type": "integer"},
            }
        },
    }
}


@router.post(
    "/chat",
    summary="Preguntar por mis notas (respuesta completa)",
    responses=RATE_LIMITED_RESPONSE,
)
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
    - `error`: `{"detail", "retry_after"}` si algo falla (p. ej. la IA no responde).
      `retry_after` son los segundos que hay que esperar cuando se superó el límite de
      preguntas por minuto; en cualquier otro error es `null`.
    """
    try:
        conversation = await _conversation_for(db, student, data.conversation_id)
    except AppError as exc:
        # Ya es un stream: los errores (p. ej. asistente desactivado) van como evento
        yield ServerSentEvent(event="error", data={"detail": exc.detail, "retry_after": None})
        return
    yield ServerSentEvent(
        event="conversation",
        data={"id": conversation.id, "title": conversation.title or data.message[:60]},
    )
    try:
        async for text in chat.ask_stream(db, llm, student, conversation, data.message):
            yield ServerSentEvent(event="delta", data={"text": text})
    except AppError as exc:
        retry_after = exc.retry_after if isinstance(exc, RateLimitedError) else None
        yield ServerSentEvent(
            event="error", data={"detail": exc.detail, "retry_after": retry_after}
        )
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
async def telegram_status(student: Student, db: DbSession) -> TelegramStatus:
    settings = get_settings()
    current = await rules.get_rules(db)
    return TelegramStatus(
        enabled=settings.telegram_enabled
        and current.telegram_enabled
        and current.assistant_enabled,
        linked=student.telegram_linked,
        bot_username=settings.telegram_bot_username.lstrip("@") or None,
    )


@router.post("/telegram/link", summary="Generar enlace para vincular Telegram")
async def telegram_link(student: Student, db: DbSession) -> TelegramLinkInvite:
    """Devuelve un enlace `t.me/<bot>?start=<código>` de un solo uso. Al abrirlo y pulsar
    **Iniciar** en Telegram, la cuenta queda vinculada y se pueden hacer preguntas sobre las
    notas desde allí. Generar un enlace nuevo invalida el anterior."""
    current = await rules.get_rules(db)
    if not (current.telegram_enabled and current.assistant_enabled):
        raise chat.AssistantDisabledError("La institución desactivó el asistente por Telegram")
    invite = await telegram_link_service.create_link(db, student)
    return TelegramLinkInvite(url=invite.url, expires_at=invite.expires_at)


@router.delete("/telegram", status_code=status.HTTP_204_NO_CONTENT, summary="Desvincular Telegram")
async def telegram_unlink(student: Student, db: DbSession) -> None:
    await telegram_link_service.unlink(db, student)
