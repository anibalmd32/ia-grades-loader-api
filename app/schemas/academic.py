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


class SemesterRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


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
