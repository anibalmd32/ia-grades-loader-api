from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enrollment import MAX_GRADE, MIN_GRADE, TERMS_PER_SEMESTER
from app.schemas.academic import SubjectRead

Term = Annotated[int, Field(ge=1, le=TERMS_PER_SEMESTER, description="Lapso (1 a 3)")]
GradeValue = Annotated[int, Field(ge=MIN_GRADE, le=MAX_GRADE, description="Nota (1 a 20)")]


class TeacherSubject(SubjectRead):
    students_count: int


class StudentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    cedula_display: str | None


class TermGrade(BaseModel):
    term: Term
    value: GradeValue | None = Field(description="null si aún no se ha cargado")


class StudentGrades(BaseModel):
    student: StudentSummary
    grades: list[TermGrade] = Field(description="Siempre un elemento por lapso, en orden")
    average: float | None = Field(description="Promedio de las notas cargadas (2 decimales)")


class SubjectGrades(BaseModel):
    subject: SubjectRead
    students: list[StudentGrades]


class GradeEntry(BaseModel):
    student_id: int
    term: Term
    value: GradeValue | None = Field(description="null borra la nota de ese lapso")


class GradesUpdate(BaseModel):
    grades: list[GradeEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "GradesUpdate":
        keys = [(g.student_id, g.term) for g in self.grades]
        if len(keys) != len(set(keys)):
            raise ValueError("Hay notas repetidas para el mismo estudiante y lapso")
        return self


class ImportRow(BaseModel):
    sheet: str | None
    row: int | None = Field(description="Fila del Excel de donde sale la nota")
    raw_cedula: str | None = Field(description="Cédula tal como aparece en el archivo")
    raw_name: str | None
    raw_value: str | None
    student: StudentSummary | None = Field(description="Estudiante identificado, si se pudo")
    term: int | None
    value: int | None = Field(description="Nota a guardar (entero 1-20) si es válida")
    current_value: int | None = Field(description="Nota actual en el sistema (se sobrescribirá)")
    status: Literal["ok", "warning", "error"] = Field(
        description="ok = lista; warning = revisar (p. ej. identificado por nombre); "
        "error = no se puede guardar"
    )
    issues: list[str]


class ImportSummary(BaseModel):
    ok: int
    warning: int
    error: int


class ImportPreview(BaseModel):
    subject: SubjectRead
    rows: list[ImportRow]
    summary: ImportSummary
    warnings: list[str] = Field(description="Observaciones generales de la IA")
    students_without_grades: list[StudentSummary] = Field(
        description="Matriculados que no aparecen en el archivo"
    )
