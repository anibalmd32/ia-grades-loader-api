import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class JobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


ACTIVE_STATUSES = (JobStatus.PENDING, JobStatus.PROCESSING)


def utcnow() -> datetime:
    """UTC sin zona horaria: así se guardan las fechas de la cola en BD."""
    return datetime.now(UTC).replace(tzinfo=None)


class ImportJob(Base):
    """Importación de notas desde Excel, procesada en segundo plano por un worker.

    El archivo se guarda en la propia tabla para que cualquier worker (en este u otro
    proceso o máquina) pueda procesarlo; se borra al terminar.
    """

    __tablename__ = "import_jobs"
    __table_args__ = (Index("ix_import_jobs_status_created_at", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("subjects.id", ondelete="CASCADE"))
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="import_job_status", values_callable=lambda e: [s.value for s in e]),
        default=JobStatus.PENDING,
    )
    file_name: Mapped[str] = mapped_column(String(255))
    file_content: Mapped[bytes | None] = mapped_column(
        LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql")
    )
    default_term: Mapped[int | None] = mapped_column(SmallInteger)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # Previsualización resultante (mismo formato que ImportPreview)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Cuándo guardó el profesor las notas de esta previsualización (null = sin confirmar)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)
