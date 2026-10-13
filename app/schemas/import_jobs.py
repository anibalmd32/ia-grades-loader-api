from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from app.models import JobStatus
from app.schemas.grades import ImportPreview

# Las fechas de la cola se guardan en UTC sin zona: se devuelven marcadas como UTC
UtcDatetime = Annotated[
    datetime, AfterValidator(lambda d: d.replace(tzinfo=UTC) if d.tzinfo is None else d)
]


class ImportJobSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    subject_id: int
    file_name: str
    status: JobStatus = Field(
        description="pending = en cola; processing = la IA lo está leyendo; "
        "done = previsualización lista; failed = ver `error`"
    )
    error: str | None
    created_at: UtcDatetime
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None


class ImportJobRead(ImportJobSummary):
    result: ImportPreview | None = Field(description="Previsualización, cuando status = done")
