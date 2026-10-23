"""Avisos a los estudiantes cuando se cargan o corrigen notas de las materias que cursan."""

from collections import defaultdict

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, Subject
from app.models.import_job import utcnow
from app.services.evaluations import GradeUpdate
from app.services.notification_stream import broker
from app.services.rules import GradingRules, fmt

MAX_BODY = 500


def _describe(update: GradeUpdate, rules: GradingRules) -> str:
    term = rules.term_name(update.term)
    where = term if update.evaluation_name is None else f"{update.evaluation_name} ({term})"
    return f"{where}: {'inasistente' if update.absent else fmt(update.value)}"


async def notify_grade_updates(
    db: AsyncSession, subject: Subject, updates: list[GradeUpdate], rules: GradingRules
) -> list[Notification]:
    """Un aviso por estudiante con sus notas nuevas o corregidas (los borrados no se
    avisan). Devuelve los avisos creados: hay que confirmar la sesión y después enviarlos a
    los estudiantes conectados con `publish`."""
    per_student: dict[int, list[GradeUpdate]] = defaultdict(list)
    for update_ in updates:
        if update_.value is not None:
            per_student[update_.student_id].append(update_)
    created = []
    for student_id, items in per_student.items():
        title = f"{'Nueva nota' if len(items) == 1 else 'Nuevas notas'} en {subject.name}"
        body = " · ".join(_describe(item, rules) for item in items)
        if len(body) > MAX_BODY:
            body = body[: MAX_BODY - 1] + "…"
        notification = Notification(
            user_id=student_id, subject_id=subject.id, title=title, body=body
        )
        db.add(notification)
        created.append(notification)
    return created


async def commit_and_publish(db: AsyncSession, notifications: list[Notification]) -> None:
    """Guarda los avisos y los envía en tiempo real a los estudiantes conectados."""
    await db.commit()
    broker.publish(notifications)


async def list_notifications(
    db: AsyncSession, user_id: int, *, unread_only: bool, limit: int, offset: int
) -> tuple[list[Notification], int, int]:
    """Avisos del usuario, del más reciente al más antiguo, con el total y los no leídos."""
    query = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    items = await db.scalars(query.order_by(Notification.id.desc()).limit(limit).offset(offset))
    return list(items), total or 0, await unread_count(db, user_id)


async def unread_count(db: AsyncSession, user_id: int) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
    )
    return count or 0


async def mark_read(db: AsyncSession, user_id: int, ids: list[int] | None) -> int:
    """Marca como leídos los avisos indicados (o todos) y devuelve los que quedan sin leer."""
    query = update(Notification).where(
        Notification.user_id == user_id, Notification.read_at.is_(None)
    )
    if ids is not None:
        query = query.where(Notification.id.in_(ids))
    await db.execute(query.values(read_at=utcnow()))
    await db.commit()
    return await unread_count(db, user_id)
