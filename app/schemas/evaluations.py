from datetime import date
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, model_validator

from app.schemas.academic import SubjectRead
from app.schemas.grades import GradeNumber, GradeValue, StudentSummary, Term
from app.schemas.settings import Weight

EvaluationName = Annotated[
    str, AfterValidator(str.strip), Field(min_length=1, max_length=100, examples=["Parcial 1"])
]
Percent = Annotated[Weight, Field(description="Porcentaje de la nota del lapso", examples=[25])]


class EvaluationCreate(BaseModel):
    term: Term
    name: EvaluationName
    weight: Percent
    due_date: date | None = Field(default=None, description="Fecha prevista (opcional)")


class EvaluationUpdate(BaseModel):
    """Solo los campos enviados; `due_date: null` quita la fecha. El lapso no se cambia."""

    name: EvaluationName | None = None
    weight: Percent | None = None
    due_date: date | None = None


class EvaluationRead(BaseModel):
    id: int
    term: int
    name: str
    weight: GradeNumber = Field(description="Porcentaje de la nota del lapso")
    due_date: date | None
    graded_count: int = Field(description="Estudiantes ya calificados en la evaluación")


class TermPlan(BaseModel):
    term: int
    weights_total: GradeNumber = Field(description="Suma de los porcentajes del lapso")
    evaluations_count: int
    complete: bool = Field(
        description="El plan suma 100 %: la nota del lapso se calcula con las evaluaciones"
    )


class EvaluationPlan(BaseModel):
    subject: SubjectRead
    terms: list[TermPlan] = Field(
        description="Un elemento por lapso. Los lapsos sin evaluaciones se cargan directamente"
    )
    evaluations: list[EvaluationRead] = Field(description="Por lapso y en orden de creación")


class EvaluationValue(BaseModel):
    evaluation_id: int
    value: GradeValue | None = Field(description="null si aún no se ha calificado")


class StudentEvaluationGrades(BaseModel):
    student: StudentSummary
    grades: list[EvaluationValue] = Field(description="Una por evaluación del lapso, en orden")
    accumulated: GradeNumber | None = Field(
        description="Puntos acumulados en el lapso: suma de cada nota por su porcentaje"
    )
    term_grade: GradeNumber | None = Field(
        description="Nota del lapso: solo con el plan al 100 % y todo calificado"
    )


class TermEvaluationGrades(BaseModel):
    subject: SubjectRead
    plan: TermPlan
    evaluations: list[EvaluationRead]
    students: list[StudentEvaluationGrades]


class EvaluationGradeEntry(BaseModel):
    student_id: int
    evaluation_id: int
    value: GradeValue | None = Field(description="null borra la nota")


class EvaluationGradesUpdate(BaseModel):
    grades: list[EvaluationGradeEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "EvaluationGradesUpdate":
        keys = [(g.student_id, g.evaluation_id) for g in self.grades]
        if len(keys) != len(set(keys)):
            raise ValueError("Hay notas repetidas para el mismo estudiante y evaluación")
        return self
