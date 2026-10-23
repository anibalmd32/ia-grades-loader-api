from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from app.schemas.settings import GradingRulesRead
from app.schemas.user import UserRead
from app.services.rules import DEFAULT_RULES, from_snapshot

Name = Annotated[str, AfterValidator(str.strip), Field(min_length=1, max_length=150)]
SemesterName = Annotated[str, AfterValidator(str.strip), Field(min_length=1, max_length=50)]
# Nombres de sección normalizados: sin espacios alrededor y en mayúsculas (p. ej. A, 01)
SectionName = Annotated[
    str,
    AfterValidator(lambda v: v.strip().upper()),
    Field(min_length=1, max_length=20, examples=["A"]),
]
# Códigos de materia normalizados: sin espacios alrededor y en mayúsculas (p. ej. MAT-101)
SubjectCode = Annotated[
    str,
    AfterValidator(lambda v: v.strip().upper()),
    Field(min_length=1, max_length=20, examples=["MAT-101"]),
]


class SemesterCreate(BaseModel):
    name: SemesterName = Field(examples=["2026-I"])


class SemesterUpdate(BaseModel):
    name: SemesterName


# Los recuentos solo se calculan en el listado y el detalle del propio recurso; dentro de
# otras respuestas (p. ej. la materia de una nota) van en null para no hacer más consultas.
COUNT_NOTE = " Solo en el listado y el detalle; null dentro de otras respuestas."


class SemesterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_closed: bool = Field(
        description="Cerrado: sus notas, matrículas y materias ya no se pueden modificar"
    )
    closed_at: Annotated[
        datetime | None,
        AfterValidator(lambda d: d.replace(tzinfo=UTC) if d and d.tzinfo is None else d),
    ] = None
    rules: Annotated[
        GradingRulesRead | None,
        BeforeValidator(
            lambda v: (
                GradingRulesRead.from_rules(from_snapshot(DEFAULT_RULES, v))
                if isinstance(v, dict) and "summary" not in v
                else v
            )
        ),
    ] = Field(
        default=None,
        description="Reglas de evaluación congeladas al cerrar el periodo (se conservan si se "
        "reabre). null = el periodo usa la configuración académica vigente",
    )
    subjects_count: int | None = Field(default=None, description="Materias." + COUNT_NOTE)


class SubjectCreate(BaseModel):
    semester_id: int
    code: SubjectCode
    name: Name = Field(examples=["Matemática I"])


class SubjectUpdate(BaseModel):
    semester_id: int | None = None
    code: SubjectCode | None = None
    name: Name | None = None


class SubjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    semester: SemesterRead
    teachers_count: int | None = Field(
        default=None, description="Profesores asignados." + COUNT_NOTE
    )
    students_count: int | None = Field(
        default=None, description="Estudiantes matriculados." + COUNT_NOTE
    )


class SectionCreate(BaseModel):
    name: SectionName


class SectionUpdate(BaseModel):
    name: SectionName


class SectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    semester_id: int
    students_count: int | None = Field(
        default=None, description="Estudiantes asignados." + COUNT_NOTE
    )


class StudentSectionRead(SectionRead):
    semester: SemesterRead


class SectionStudentsRequest(BaseModel):
    student_ids: list[int] = Field(
        min_length=1,
        description="Estudiantes a asignar. Si ya estaban en otra sección del periodo, se "
        "cambian (salvo que ya tengan notas en el periodo: 409)",
    )


class SectionStudentsResult(BaseModel):
    added: int = Field(description="Asignados por primera vez en el periodo")
    moved: int = Field(description="Cambiados desde otra sección del periodo")
    already_in_section: int = Field(description="Ya estaban en la sección")


class TeacherAssignment(BaseModel):
    subject_id: int
    section_id: int


class TeacherSubjectsUpdate(BaseModel):
    assignments: list[TeacherAssignment] = Field(
        description="Conjunto completo de (materia, sección) que imparte el profesor (reemplaza "
        "el anterior). Cada materia tiene un solo profesor por sección: si otro ya la imparte "
        "en esa sección responde 409"
    )


class TeacherAssignmentRead(BaseModel):
    subject: SubjectRead
    section: SectionRead


class SubjectTeacherRead(BaseModel):
    section: SectionRead
    teacher: UserRead


class EnrollSubjectsRequest(BaseModel):
    subject_ids: list[int] = Field(min_length=1, description="Materias a matricular")


class EnrollStudentsRequest(BaseModel):
    student_ids: list[int] = Field(min_length=1, description="Estudiantes a matricular")


class EnrollmentResult(BaseModel):
    added: int = Field(description="Matrículas nuevas")
    already_enrolled: int = Field(description="Ya existían y se ignoraron")
