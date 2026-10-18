from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.user import UserRead


class StudentImportRow(BaseModel):
    row: int = Field(description="Fila del Excel")
    first_name: str
    last_name: str
    nationality: str | None
    cedula: int | None
    subject_codes: list[str] = Field(description="Materias en las que se matricula")
    status: Literal["new", "existing", "error"] = Field(
        description="new = se crea; existing = ya existe y solo se matricula; error = se omite"
    )
    issues: list[str]
    user: UserRead | None = Field(description="Cuenta creada o existente (solo al confirmar)")
    generated_password: str | None = Field(
        description="Contraseña inicial de las cuentas nuevas. Solo se muestra al confirmar, "
        "esta única vez"
    )


class StudentImportSummary(BaseModel):
    new: int
    existing: int
    error: int


class StudentImportResult(BaseModel):
    dry_run: bool
    rows: list[StudentImportRow]
    summary: StudentImportSummary
