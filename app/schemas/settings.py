from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, PlainSerializer, model_validator

from app.models import PeriodType
from app.schemas.common import DocumentCode
from app.services.rules import MAX_DECIMALS, MAX_GRADE_LIMIT, MAX_TERMS, GradingRules

_AsNumber = PlainSerializer(float, return_type=float, when_used="json")
ScaleNumber = Annotated[
    Decimal, Field(ge=0, le=MAX_GRADE_LIMIT, max_digits=5, decimal_places=2), _AsNumber
]
Weight = Annotated[Decimal, Field(gt=0, le=100, max_digits=5, decimal_places=2), _AsNumber]
Label = Annotated[str, AfterValidator(str.strip)]


class DocumentType(BaseModel):
    code: DocumentCode = Field(description="Letra(s) que preceden al número: V, E, P...")
    name: Label = Field(min_length=2, max_length=40, examples=["Venezolano"])


class AcademicSettingsBase(BaseModel):
    institution_name: Label = Field(
        min_length=2, max_length=150, description="Nombre de la institución", examples=["UNEFA"]
    )
    period_type: PeriodType = Field(
        description="Cómo se organiza el año académico: semester (semestres), trimester "
        "(trimestres), quadrimester (cuatrimestres) o annual (años)"
    )
    terms_count: int = Field(
        ge=1, le=MAX_TERMS, description="Evaluaciones (lapsos) en que se divide cada periodo"
    )
    term_label: Label = Field(
        min_length=2,
        max_length=30,
        description="Nombre de cada evaluación del periodo",
        examples=["Lapso", "Corte", "Parcial"],
    )
    term_weights: list[Weight] | None = Field(
        description="Porcentaje de cada lapso, en orden; deben sumar 100. null = todos pesan "
        "igual (promedio simple)",
        examples=[None, [30, 30, 40]],
    )
    min_grade: ScaleNumber = Field(description="Nota mínima de la escala")
    max_grade: ScaleNumber = Field(
        description=f"Nota máxima de la escala (hasta {MAX_GRADE_LIMIT})"
    )
    passing_grade: ScaleNumber = Field(description="Nota mínima para aprobar")
    grade_decimals: int = Field(
        ge=0, le=MAX_DECIMALS, description="Decimales admitidos en las notas"
    )
    round_final_grade: bool = Field(
        description="true: la definitiva se redondea al entero (9.5 → 10) antes de decidir si "
        "aprueba"
    )
    document_label: Label = Field(
        min_length=2,
        max_length=30,
        description="Cómo se llama el documento con que entran los estudiantes",
        examples=["Cédula", "DNI"],
    )
    document_types: list[DocumentType] = Field(
        min_length=1,
        max_length=10,
        description="Tipos de documento admitidos (el login de estudiantes los ofrece en este "
        "orden)",
    )


class AcademicSettingsUpdate(AcademicSettingsBase):
    """Reemplaza la configuración completa."""

    @model_validator(mode="after")
    def _check(self) -> "AcademicSettingsUpdate":
        if self.min_grade >= self.max_grade:
            raise ValueError("La nota mínima debe ser menor que la máxima")
        if not self.min_grade < self.passing_grade <= self.max_grade:
            raise ValueError(
                "La nota aprobatoria debe ser mayor que la mínima y no mayor que la máxima"
            )
        quantum = Decimal(1).scaleb(-self.grade_decimals)
        for label, value in (
            ("mínima", self.min_grade),
            ("máxima", self.max_grade),
            ("aprobatoria", self.passing_grade),
        ):
            if value != value.quantize(quantum):
                raise ValueError(
                    f"La nota {label} tiene más decimales de los admitidos ({self.grade_decimals})"
                )
        codes = [t.code for t in self.document_types]
        if len(codes) != len(set(codes)):
            raise ValueError("Hay tipos de documento con el mismo código")
        if self.term_weights is not None:
            if len(self.term_weights) != self.terms_count:
                raise ValueError(
                    f"Indica un porcentaje por cada lapso ({self.terms_count}) o ninguno"
                )
            if sum(self.term_weights) != 100:
                raise ValueError("Los porcentajes de los lapsos deben sumar 100")
        return self

    def to_rules(self) -> GradingRules:
        return GradingRules(
            institution_name=self.institution_name,
            period_type=self.period_type,
            terms_count=self.terms_count,
            term_label=self.term_label,
            term_weights=None if self.term_weights is None else tuple(self.term_weights),
            min_grade=self.min_grade,
            max_grade=self.max_grade,
            passing_grade=self.passing_grade,
            grade_decimals=self.grade_decimals,
            round_final_grade=self.round_final_grade,
            document_label=self.document_label,
            document_types=tuple((t.code, t.name) for t in self.document_types),
        )


class GradingRulesRead(BaseModel):
    """Reglas de evaluación congeladas de un periodo cerrado."""

    terms_count: int
    term_label: str
    term_label_plural: str
    term_weights: list[Annotated[Decimal, _AsNumber]] | None
    term_percentages: list[Annotated[Decimal, _AsNumber]]
    min_grade: Annotated[Decimal, _AsNumber]
    max_grade: Annotated[Decimal, _AsNumber]
    passing_grade: Annotated[Decimal, _AsNumber]
    grade_decimals: int
    round_final_grade: bool
    summary: str

    @classmethod
    def from_rules(cls, rules: GradingRules) -> "GradingRulesRead":
        return cls(
            terms_count=rules.terms_count,
            term_label=rules.term_label,
            term_label_plural=rules.term_label_plural,
            term_weights=None if rules.term_weights is None else list(rules.term_weights),
            term_percentages=_percentages(rules),
            min_grade=rules.min_grade,
            max_grade=rules.max_grade,
            passing_grade=rules.passing_grade,
            grade_decimals=rules.grade_decimals,
            round_final_grade=rules.round_final_grade,
            summary=rules.final_grade_text,
        )


def _percentages(rules: GradingRules) -> list[Decimal]:
    if rules.term_weights is not None:
        return list(rules.term_weights)
    equal = (Decimal(100) / rules.terms_count).quantize(Decimal("0.01"))
    return [equal] * rules.terms_count


class AcademicSettingsRead(AcademicSettingsBase):
    period_label: str = Field(description="Nombre del periodo en singular", examples=["Semestre"])
    period_label_plural: str = Field(examples=["Semestres"])
    term_label_plural: str = Field(examples=["Lapsos"])
    term_percentages: list[Annotated[Decimal, _AsNumber]] = Field(
        description="Porcentaje efectivo de cada lapso (con pesos iguales, 100 / lapsos "
        "redondeado a 2 decimales)"
    )
    summary: str = Field(
        description="Regla de la definitiva en una frase, lista para mostrar",
        examples=["La definitiva es el promedio simple de los 3 lapsos y se aprueba con 10 o más"],
    )

    @classmethod
    def from_rules(cls, rules: GradingRules) -> "AcademicSettingsRead":
        percentages = _percentages(rules)
        return cls(
            institution_name=rules.institution_name,
            period_type=rules.period_type,
            terms_count=rules.terms_count,
            term_label=rules.term_label,
            term_weights=None if rules.term_weights is None else list(rules.term_weights),
            min_grade=rules.min_grade,
            max_grade=rules.max_grade,
            passing_grade=rules.passing_grade,
            grade_decimals=rules.grade_decimals,
            round_final_grade=rules.round_final_grade,
            document_label=rules.document_label,
            document_types=[DocumentType(code=c, name=n) for c, n in rules.document_types],
            period_label=rules.period_label,
            period_label_plural=rules.period_label_plural,
            term_label_plural=rules.term_label_plural,
            term_percentages=percentages,
            summary=rules.final_grade_text,
        )
