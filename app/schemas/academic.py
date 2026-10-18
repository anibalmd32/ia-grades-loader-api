from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

Name = Annotated[str, AfterValidator(str.strip), Field(min_length=1, max_length=150)]
SemesterName = Annotated[str, AfterValidator(str.strip), Field(min_length=1, max_length=50)]
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


class TeacherSubjectsUpdate(BaseModel):
    subject_ids: list[int] = Field(
        description="Conjunto completo de materias del profesor (reemplaza el anterior)"
    )


class EnrollSubjectsRequest(BaseModel):
    subject_ids: list[int] = Field(min_length=1, description="Materias a matricular")


class EnrollStudentsRequest(BaseModel):
    student_ids: list[int] = Field(min_length=1, description="Estudiantes a matricular")


class EnrollmentResult(BaseModel):
    added: int = Field(description="Matrículas nuevas")
    already_enrolled: int = Field(description="Ya existían y se ignoraron")
