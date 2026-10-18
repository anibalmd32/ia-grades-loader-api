from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from app.models.enrollment import GRADE_DECIMALS, MAX_GRADE, MIN_GRADE, TERMS_PER_SEMESTER
from app.schemas.academic import SemesterRead, SubjectRead
from app.services.student_grades import Standing

Term = Annotated[int, Field(ge=1, le=TERMS_PER_SEMESTER, description="Lapso (1 a 3)")]

# Decimal exacto internamente; en JSON viaja como número (15.75), no como texto
_AsNumber = PlainSerializer(float, return_type=float, when_used="json")
GradeValue = Annotated[
    Decimal,
    Field(
        ge=MIN_GRADE,
        le=MAX_GRADE,
        max_digits=2 + GRADE_DECIMALS,
        decimal_places=GRADE_DECIMALS,
        description=f"Nota del {MIN_GRADE} al {MAX_GRADE}, hasta {GRADE_DECIMALS} decimales",
        examples=[15.75],
    ),
    _AsNumber,
]
GradeNumber = Annotated[Decimal, _AsNumber]


class TeacherSubject(SubjectRead):
    students_count: int
    grades_count: dict[Term, int] = Field(
        description="Notas cargadas en cada lapso (claves 1, 2 y 3; siempre las tres)",
        examples=[{"1": 30, "2": 12, "3": 0}],
    )


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
    average: GradeNumber | None = Field(
        description="Promedio de las notas cargadas (2 decimales, redondeo hacia arriba en .005)"
    )


class SubjectGrades(BaseModel):
    subject: SubjectRead
    students: list[StudentGrades]


class GradeEntry(BaseModel):
    student_id: int
    term: Term
    value: GradeValue | None = Field(description="null borra la nota de ese lapso")


class GradesUpdate(BaseModel):
    grades: list[GradeEntry] = Field(min_length=1)
    import_job_id: str | None = Field(
        default=None,
        description="Si las notas vienen de una importación de Excel, su id: la importación "
        "queda marcada como confirmada (`confirmed_at`)",
    )

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
    value: GradeNumber | None = Field(description="Nota a guardar (1-20) si es válida")
    current_value: GradeNumber | None = Field(
        description="Nota actual en el sistema (se sobrescribirá)"
    )
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


class SubjectProjection(BaseModel):
    """Situación de la materia: definitiva = promedio simple de los 3 lapsos."""

    standing: Standing = Field(
        description="passed / failed = materia cerrada; secured = aprueba aunque saque la "
        "nota mínima en lo que falta; in_progress = aún depende de los lapsos pendientes; "
        "impossible = ni con la nota máxima llega a la aprobatoria"
    )
    final_grade: GradeNumber | None = Field(description="Definitiva (solo con los 3 lapsos)")
    pending_terms: list[int] = Field(description="Lapsos sin nota")
    needed_average: GradeNumber | None = Field(
        description="Nota mínima (promedio) que necesita en los lapsos pendientes para aprobar"
    )


class StudentSubjectGrades(BaseModel):
    subject: SubjectRead
    teachers: list[str] = Field(description="Profesores que imparten la materia")
    grades: list[TermGrade] = Field(description="Siempre un elemento por lapso, en orden")
    average: GradeNumber | None = Field(description="Promedio de las notas cargadas")
    projection: SubjectProjection


class SemesterAverage(BaseModel):
    semester: SemesterRead
    average: GradeNumber | None = Field(
        description="Promedio del semestre: media de los promedios de sus materias con notas"
    )
    subjects_count: int = Field(description="Materias matriculadas en el semestre")
    passed_count: int = Field(description="Materias aprobadas (los 3 lapsos cargados)")
    failed_count: int = Field(description="Materias reprobadas (los 3 lapsos cargados)")


class StudentOverview(BaseModel):
    subjects: list[StudentSubjectGrades]
    average: GradeNumber | None = Field(
        description="Promedio general: media de los promedios de las materias con notas"
    )
    semesters: list[SemesterAverage] = Field(
        description="Promedio y materias aprobadas/reprobadas de cada semestre, en el mismo "
        "orden que `subjects`"
    )
