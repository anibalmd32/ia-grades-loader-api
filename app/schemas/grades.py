from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

from app.models import ChangeSource
from app.schemas.academic import SectionRead, SemesterRead, SubjectRead
from app.services.rules import MAX_DECIMALS, MAX_GRADE_LIMIT, MAX_TERMS
from app.services.student_grades import Standing

# Los límites exactos (número de lapsos, escala, decimales) dependen de la configuración
# académica y los valida el servicio (422); aquí solo se acota lo imposible.
Term = Annotated[
    int, Field(ge=1, le=MAX_TERMS, description="Lapso (de 1 al número de lapsos configurado)")
]

# Decimal exacto internamente; en JSON viaja como número (15.75), no como texto
_AsNumber = PlainSerializer(float, return_type=float, when_used="json")
GradeValue = Annotated[
    Decimal,
    Field(
        ge=0,
        le=MAX_GRADE_LIMIT,
        max_digits=3 + MAX_DECIMALS,
        decimal_places=MAX_DECIMALS,
        description="Nota dentro de la escala configurada (GET /settings/academic)",
        examples=[15.75],
    ),
    _AsNumber,
]
GradeNumber = Annotated[Decimal, _AsNumber]
# Fechas guardadas en UTC sin zona: se devuelven marcadas como UTC
UtcDatetime = Annotated[
    datetime, AfterValidator(lambda d: d.replace(tzinfo=UTC) if d.tzinfo is None else d)
]


class TeacherSubject(SubjectRead):
    """Materia que imparte el profesor en una de sus secciones (`id` es el de la materia)."""

    section: SectionRead
    students_count: int
    grades_count: dict[Term, int] = Field(
        description="Notas cargadas en cada lapso (siempre todos los lapsos configurados)",
        examples=[{"1": 30, "2": 12, "3": 0}],
    )


class StudentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    first_name: str
    last_name: str
    full_name: str
    cedula_display: str | None


ABSENT_NOTE = "Inasistente: no se presentó. Cuenta con la nota mínima de la escala (`value`)"


class TermGrade(BaseModel):
    term: Term
    value: GradeValue | None = Field(description="null si aún no se ha cargado")
    absent: bool = Field(default=False, description=ABSENT_NOTE)


class StudentGrades(BaseModel):
    student: StudentSummary
    grades: list[TermGrade] = Field(description="Siempre un elemento por lapso, en orden")
    average: GradeNumber | None = Field(
        description="Promedio (ponderado si los lapsos tienen pesos) de las notas cargadas"
    )
    final_grade: GradeNumber | None = Field(
        description="Definitiva: solo con todos los lapsos cargados (calculada por la API)"
    )
    passed: bool | None = Field(description="Si aprueba con la definitiva; null si faltan lapsos")


class SubjectGrades(BaseModel):
    subject: SubjectRead
    section: SectionRead
    computed_terms: list[int] = Field(
        default_factory=list,
        description="Lapsos con plan de evaluación: su nota se calcula con las evaluaciones y "
        "no se carga directamente (PUT /grades responde 422)",
    )
    students: list[StudentGrades]


class GradeEntry(BaseModel):
    student_id: int
    term: Term
    value: GradeValue | None = Field(
        default=None, description="null borra la nota de ese lapso (o va con `absent: true`)"
    )
    absent: bool = Field(
        default=False,
        description="Marca al estudiante como inasistente en el lapso (sin `value`: cuenta con "
        "la nota mínima). En un lapso con plan de evaluación lo marca inasistente en cada "
        "evaluación",
    )

    @model_validator(mode="after")
    def _absent_without_value(self) -> "GradeEntry":
        if self.absent and self.value is not None:
            raise ValueError("Una nota inasistente no lleva valor")
        return self


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
    value: GradeNumber | None = Field(description="Nota a guardar si es válida")
    absent: bool = Field(
        default=False, description="La planilla lo marca como inasistente («I», «NP»...)"
    )
    current_value: GradeNumber | None = Field(
        description="Nota actual en el sistema (se sobrescribirá)"
    )
    current_absent: bool = Field(default=False, description="Hoy figura como inasistente")
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
    section: SectionRead | None = None
    rows: list[ImportRow]
    summary: ImportSummary
    warnings: list[str] = Field(description="Observaciones generales de la IA")
    students_without_grades: list[StudentSummary] = Field(
        description="Matriculados que no aparecen en el archivo"
    )


class SubjectProjection(BaseModel):
    """Situación de la materia según la configuración académica."""

    standing: Standing = Field(
        description="passed / failed = materia cerrada; secured = aprueba aunque saque la "
        "nota mínima en lo que falta; in_progress = aún depende de los lapsos pendientes; "
        "impossible = ni con la nota máxima llega a la aprobatoria"
    )
    final_grade: GradeNumber | None = Field(description="Definitiva (solo con todos los lapsos)")
    pending_terms: list[int] = Field(description="Lapsos sin nota")
    needed_average: GradeNumber | None = Field(
        description="Nota mínima (promedio) que necesita en los lapsos pendientes para aprobar"
    )


class StudentEvaluation(BaseModel):
    """Una evaluación vista por el estudiante, con su nota."""

    term: int
    name: str
    weight: GradeNumber = Field(description="Porcentaje de la nota del lapso")
    due_date: date | None
    value: GradeNumber | None = Field(description="null si aún no se ha calificado")
    absent: bool = Field(default=False, description=ABSENT_NOTE)


class StudentSubjectGrades(BaseModel):
    subject: SubjectRead
    section: str | None = Field(description="Sección en la que cursa el periodo")
    teacher: str | None = Field(
        description="Profesor de la materia en su sección (null si aún no tiene)"
    )
    grades: list[TermGrade] = Field(description="Siempre un elemento por lapso, en orden")
    evaluations: list[StudentEvaluation] = Field(
        default_factory=list,
        description="Plan de evaluación de la materia (por lapso) con la nota de cada una. Los "
        "lapsos con evaluaciones calculan su nota a partir de ellas",
    )
    average: GradeNumber | None = Field(description="Promedio de las notas cargadas")
    projection: SubjectProjection


class SemesterAverage(BaseModel):
    semester: SemesterRead
    average: GradeNumber | None = Field(
        description="Promedio del semestre: media de los promedios de sus materias con notas"
    )
    subjects_count: int = Field(description="Materias matriculadas en el semestre")
    passed_count: int = Field(description="Materias aprobadas (todos los lapsos cargados)")
    failed_count: int = Field(description="Materias reprobadas (todos los lapsos cargados)")


class StudentOverview(BaseModel):
    subjects: list[StudentSubjectGrades]
    average: GradeNumber | None = Field(
        description="Promedio general: media de los promedios de las materias con notas"
    )
    semesters: list[SemesterAverage] = Field(
        description="Promedio y materias aprobadas/reprobadas de cada semestre, en el mismo "
        "orden que `subjects`"
    )


class GradeChangeRead(BaseModel):
    """Un cambio en el historial de notas."""

    id: int
    student: StudentSummary
    term: int
    evaluation_name: str | None = Field(
        description="Evaluación cuya nota cambió; null = la nota del lapso"
    )
    old_value: GradeNumber | None = Field(description="Nota anterior; null = no tenía nota")
    new_value: GradeNumber | None = Field(description="Nota nueva; null = se borró")
    old_absent: bool = Field(description="Antes figuraba como inasistente")
    new_absent: bool = Field(description="Queda como inasistente")
    changed_by: str | None = Field(description="Quién hizo el cambio (null si ya no existe)")
    source: ChangeSource = Field(
        description="manual = en la planilla; import = desde un Excel; evaluations = nota del "
        "lapso calculada con sus evaluaciones"
    )
    import_job_id: str | None
    created_at: UtcDatetime

    @classmethod
    def from_row(cls, change, student, changed_by: str | None) -> "GradeChangeRead":
        return cls(
            id=change.id,
            student=StudentSummary.model_validate(student),
            term=change.term,
            evaluation_name=change.evaluation_name,
            old_value=change.old_value,
            new_value=change.new_value,
            old_absent=change.old_absent,
            new_absent=change.new_absent,
            changed_by=changed_by,
            source=change.source,
            import_job_id=change.import_job_id,
            created_at=change.created_at,
        )
