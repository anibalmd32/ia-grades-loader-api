"""Cola de importaciones de notas persistida en la base de datos.

Flujo: la API encola (enqueue) y responde al instante; un worker reclama el trabajo
(claim_next), lo procesa (process_job) y guarda la previsualización en `result`.
"""

import logging
from datetime import timedelta

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.client import LLMClient
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.models import ImportJob, JobStatus, Subject, TeacherSubject
from app.models.import_job import ACTIVE_STATUSES, utcnow
from app.services import grade_import

logger = logging.getLogger(__name__)

# El archivo .xlsx es un zip: empieza por "PK". Permite rechazar al instante otros formatos.
_XLSX_MAGIC = b"PK"


class TooManyJobsError(AppError):
    status_code = 429


class InvalidUploadError(AppError):
    status_code = 400


async def enqueue(
    db: AsyncSession,
    *,
    teacher_id: int,
    subject_id: int,
    file_name: str,
    content: bytes,
    default_term: int | None,
) -> ImportJob:
    if not content.startswith(_XLSX_MAGIC):
        raise InvalidUploadError(
            "El archivo no es un Excel válido (.xlsx). Si es .xls, guárdalo como .xlsx"
        )
    limit = get_settings().import_max_pending_per_teacher
    active = await db.scalar(
        select(func.count())
        .select_from(ImportJob)
        .where(ImportJob.teacher_id == teacher_id, ImportJob.status.in_(ACTIVE_STATUSES))
    )
    if active >= limit:
        raise TooManyJobsError(
            f"Ya tienes {active} importaciones en curso; espera a que terminen (máximo {limit})"
        )
    job = ImportJob(
        teacher_id=teacher_id,
        subject_id=subject_id,
        file_name=file_name[:255],
        file_content=content,
        default_term=default_term,
    )
    db.add(job)
    await db.commit()
    return job


def _still_assigned(teacher_id: int):
    """El profesor sigue asignado a la materia de la importación: si se la quitan, deja de
    ver sus importaciones (traen la lista de estudiantes y sus notas)."""
    return exists().where(
        TeacherSubject.teacher_id == teacher_id,
        TeacherSubject.subject_id == ImportJob.subject_id,
    )


async def get_job(db: AsyncSession, teacher_id: int, job_id: str) -> ImportJob:
    job = await db.scalar(
        select(ImportJob).where(
            ImportJob.id == job_id,
            ImportJob.teacher_id == teacher_id,
            _still_assigned(teacher_id),
        )
    )
    if job is None:
        raise NotFoundError("Importación no encontrada")
    return job


async def mark_confirmed(
    db: AsyncSession, teacher_id: int, subject_id: int, job_id: str
) -> ImportJob:
    """Marca la importación como confirmada. No hace commit: se guarda junto con las notas
    (si guardar las notas falla, la importación sigue sin confirmar)."""
    job = await get_job(db, teacher_id, job_id)
    if job.subject_id != subject_id:
        raise ConflictError("La importación es de otra materia")
    if job.status != JobStatus.DONE:
        raise ConflictError("La importación aún no tiene una previsualización lista")
    job.confirmed_at = utcnow()
    return job


async def list_jobs(
    db: AsyncSession, teacher_id: int, subject_id: int | None, limit: int
) -> list[ImportJob]:
    query = select(ImportJob).where(ImportJob.teacher_id == teacher_id, _still_assigned(teacher_id))
    if subject_id is not None:
        query = query.where(ImportJob.subject_id == subject_id)
    query = query.order_by(ImportJob.created_at.desc()).limit(limit)
    return list(await db.scalars(query))


async def claim_next(db: AsyncSession) -> str | None:
    """Reclama el trabajo pendiente más antiguo. Seguro con varios workers a la vez:
    SKIP LOCKED evita esperar por filas ya tomadas y el UPDATE condicional garantiza
    que solo un worker gana cada trabajo."""
    while True:
        job_id = await db.scalar(
            select(ImportJob.id)
            .where(ImportJob.status == JobStatus.PENDING)
            .order_by(ImportJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job_id is None:
            await db.commit()
            return None
        claimed = await db.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id, ImportJob.status == JobStatus.PENDING)
            .values(
                status=JobStatus.PROCESSING,
                started_at=utcnow(),
                attempts=ImportJob.attempts + 1,
            )
        )
        await db.commit()
        if claimed.rowcount == 1:
            return job_id


async def _finish(
    sessionmaker: async_sessionmaker[AsyncSession],
    job_id: str,
    *,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    async with sessionmaker() as db:
        await db.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id)
            .values(
                status=JobStatus.FAILED if error else JobStatus.DONE,
                result=result,
                error=error,
                finished_at=utcnow(),
                file_content=None,  # ya no hace falta guardar el archivo
            )
        )
        await db.commit()


async def process_job(
    sessionmaker: async_sessionmaker[AsyncSession], llm: LLMClient, job_id: str
) -> None:
    """Procesa un trabajo ya reclamado. Nunca lanza: los errores quedan en el trabajo."""
    try:
        async with sessionmaker() as db:
            job = await db.get(ImportJob, job_id)
            subject = await db.get(Subject, job.subject_id)
            preview = await grade_import.preview_import(
                db, llm, subject, job.file_content or b"", job.default_term
            )
            result = grade_import.to_schema(subject, preview).model_dump(mode="json")
    except AppError as exc:
        logger.info("Importación %s fallida: %s", job_id, exc.detail)
        await _finish(sessionmaker, job_id, error=exc.detail)
    except Exception:
        logger.exception("Error inesperado procesando la importación %s", job_id)
        await _finish(sessionmaker, job_id, error="Error inesperado procesando el archivo")
    else:
        await _finish(sessionmaker, job_id, result=result)


async def recover_stale(db: AsyncSession) -> int:
    """Devuelve a la cola los trabajos cuyo worker murió a mitad (o los da por fallidos
    si ya agotaron los intentos). Devuelve cuántos se recuperaron."""
    settings = get_settings()
    cutoff = utcnow() - timedelta(minutes=settings.import_job_stale_minutes)
    stale = (ImportJob.status == JobStatus.PROCESSING) & (ImportJob.started_at < cutoff)
    await db.execute(
        update(ImportJob)
        .where(stale, ImportJob.attempts >= settings.import_job_max_attempts)
        .values(
            status=JobStatus.FAILED,
            error="El procesamiento se interrumpió varias veces",
            finished_at=utcnow(),
            file_content=None,
        )
    )
    requeued = await db.execute(
        update(ImportJob).where(stale).values(status=JobStatus.PENDING, started_at=None)
    )
    await db.commit()
    return requeued.rowcount
