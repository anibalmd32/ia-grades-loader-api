"""Reglas académicas configurables por el super admin (periodos, lapsos, escala de notas).

Todo el cálculo de notas (validación, promedios, definitiva, proyección) pasa por
GradingRules, de modo que cambiar la configuración cambia a la vez la carga, la consulta,
la importación con IA y el asistente.
"""

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError
from app.models import AcademicSettings, Grade, PeriodType
from app.models.settings import SETTINGS_ID

# Límites de lo configurable (también los usa la validación de entrada)
MAX_TERMS = 10
MAX_GRADE_LIMIT = Decimal(100)
MAX_DECIMALS = 2

PERIOD_LABELS: dict[PeriodType, tuple[str, str]] = {
    PeriodType.SEMESTER: ("Semestre", "Semestres"),
    PeriodType.TRIMESTER: ("Trimestre", "Trimestres"),
    PeriodType.QUADRIMESTER: ("Cuatrimestre", "Cuatrimestres"),
    PeriodType.ANNUAL: ("Año", "Años"),
}


def fmt(value: Decimal) -> str:
    """10.00 -> "10"; 9.50 -> "9.5" (para textos)."""
    text = f"{value:f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


@dataclass(frozen=True)
class GradingRules:
    institution_name: str = "Universidad"
    period_type: PeriodType = PeriodType.SEMESTER
    terms_count: int = 3
    term_label: str = "Lapso"
    # Porcentajes por lapso (suman 100); None = todos pesan igual
    term_weights: tuple[Decimal, ...] | None = None
    min_grade: Decimal = Decimal(1)
    max_grade: Decimal = Decimal(20)
    passing_grade: Decimal = Decimal(10)
    grade_decimals: int = 2
    round_final_grade: bool = False

    # --- Textos ---------------------------------------------------------------

    @property
    def period_label(self) -> str:
        return PERIOD_LABELS[self.period_type][0]

    @property
    def period_label_plural(self) -> str:
        return PERIOD_LABELS[self.period_type][1]

    @property
    def term_label_plural(self) -> str:
        label = self.term_label
        if label.endswith("ón"):  # Evaluación -> Evaluaciones
            return label[:-2] + "ones"
        return label + ("s" if label[-1:].lower() in "aeiou" else "es")

    def term_name(self, term: int) -> str:
        return f"{self.term_label} {term}"

    @property
    def scale_text(self) -> str:
        return f"del {fmt(self.min_grade)} al {fmt(self.max_grade)}"

    @property
    def weights_text(self) -> str:
        if self.term_weights is None:
            return f"todos los {self.term_label_plural.lower()} pesan igual"
        parts = [
            f"{self.term_name(t)}: {fmt(w)} %"
            for t, w in zip(self.terms, self.term_weights, strict=True)
        ]
        return "cada uno pesa distinto (" + ", ".join(parts) + ")"

    @property
    def final_grade_text(self) -> str:
        kind = "promedio simple" if self.term_weights is None else "promedio ponderado"
        rounding = ", redondeado al entero (9.5 → 10)" if self.round_final_grade else ""
        return (
            f"La definitiva es el {kind} de los {self.terms_count} "
            f"{self.term_label_plural.lower()}{rounding} y se aprueba con "
            f"{fmt(self.passing_grade)} o más"
        )

    # --- Lapsos y pesos -------------------------------------------------------

    @property
    def terms(self) -> range:
        return range(1, self.terms_count + 1)

    @property
    def weights(self) -> list[Decimal]:
        """Peso relativo de cada lapso, en orden. Sin pesos configurados valen 1 (así el
        promedio simple sale exacto, sin arrastrar 33.333...)."""
        if self.term_weights is not None:
            return list(self.term_weights)
        return [Decimal(1)] * self.terms_count

    # --- Notas ------------------------------------------------------------------

    @property
    def quantum(self) -> Decimal:
        return Decimal(1).scaleb(-self.grade_decimals)

    def quantize(self, value: Decimal) -> Decimal:
        """Redondeo escolar (la mitad hacia arriba) a los decimales admitidos."""
        return value.quantize(self.quantum, rounding=ROUND_HALF_UP)

    def term_error(self, term: int) -> str | None:
        if 1 <= term <= self.terms_count:
            return None
        return f"El {self.term_label.lower()} debe estar entre 1 y {self.terms_count}"

    def grade_error(self, value: Decimal) -> str | None:
        """Motivo por el que la nota no es válida, o None si lo es."""
        if not self.min_grade <= value <= self.max_grade:
            return f"La nota debe estar {self.scale_text}"
        if value != self.quantize(value):
            return f"La nota admite {self.grade_decimals} decimales como máximo"
        return None

    def average(self, grades: dict[int, Decimal | None]) -> Decimal | None:
        """Promedio ponderado de los lapsos cargados (los pesos se reparten entre ellos)."""
        loaded = [
            (w, grades[t])
            for t, w in zip(self.terms, self.weights, strict=True)
            if grades.get(t) is not None
        ]
        if not loaded:
            return None
        total_weight = sum((w for w, _ in loaded), Decimal(0))
        return self.quantize(sum((w * v for w, v in loaded), Decimal(0)) / total_weight)

    def final_grade(self, grades: dict[int, Decimal | None]) -> Decimal | None:
        """Definitiva: solo con todos los lapsos cargados (redondeada al entero si se pide)."""
        if any(grades.get(t) is None for t in self.terms):
            return None
        final = self.average(grades)
        if final is not None and self.round_final_grade:
            final = final.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return final

    def passes(self, final: Decimal) -> bool:
        return final >= self.passing_grade

    @property
    def passing_threshold(self) -> Decimal:
        """Promedio sin redondear a partir del cual se aprueba."""
        if not self.round_final_grade:
            return self.passing_grade
        # Con redondeo al entero (la mitad hacia arriba) basta con llegar a X.5 por debajo
        return self.passing_grade.to_integral_value(rounding=ROUND_CEILING) - Decimal("0.5")

    def needed_in_pending(self, grades: dict[int, Decimal | None]) -> Decimal | None:
        """Nota mínima que hay que sacar en CADA lapso pendiente para aprobar (redondeada
        hacia arriba: sacar exactamente esa nota garantiza aprobar). None si no falta ninguno.
        """
        weighted = list(zip(self.terms, self.weights, strict=True))
        pending_weight = sum((w for t, w in weighted if grades.get(t) is None), Decimal(0))
        if not pending_weight:
            return None
        loaded = sum((w * grades[t] for t, w in weighted if grades.get(t) is not None), Decimal(0))
        total_weight = sum(self.weights, Decimal(0))
        needed = (self.passing_threshold * total_weight - loaded) / pending_weight
        return needed.quantize(self.quantum, rounding=ROUND_CEILING)


DEFAULT_RULES = GradingRules()


def _from_row(row: AcademicSettings) -> GradingRules:
    return GradingRules(
        institution_name=row.institution_name,
        period_type=row.period_type,
        terms_count=row.terms_count,
        term_label=row.term_label,
        term_weights=None
        if row.term_weights is None
        else tuple(Decimal(str(w)) for w in row.term_weights),
        min_grade=Decimal(row.min_grade),
        max_grade=Decimal(row.max_grade),
        passing_grade=Decimal(row.passing_grade),
        grade_decimals=row.grade_decimals,
        round_final_grade=row.round_final_grade,
    )


async def get_rules(db: AsyncSession) -> GradingRules:
    """Reglas vigentes. Se leen en cada uso (es una fila): así todos los procesos (API,
    workers, bot) ven los cambios al instante."""
    row = await db.get(AcademicSettings, SETTINGS_ID)
    return DEFAULT_RULES if row is None else _from_row(row)


async def _check_existing_grades(db: AsyncSession, new: GradingRules) -> None:
    """Las notas ya cargadas tienen que seguir siendo válidas con las reglas nuevas."""
    label = new.term_label_plural.lower()
    out_of_terms = await db.scalar(
        select(func.count()).select_from(Grade).where(Grade.term > new.terms_count)
    )
    if out_of_terms:
        raise ConflictError(
            f"Hay {out_of_terms} notas cargadas en {label} posteriores al {new.terms_count}. "
            f"Bórralas antes de reducir el número de {label}."
        )
    out_of_scale = await db.scalar(
        select(func.count())
        .select_from(Grade)
        .where(or_(Grade.value < new.min_grade, Grade.value > new.max_grade))
    )
    if out_of_scale:
        raise ConflictError(
            f"Hay {out_of_scale} notas cargadas fuera de la escala {new.scale_text}. "
            "Corrígelas antes de cambiar la escala."
        )
    if new.grade_decimals < MAX_DECIMALS:
        values = await db.scalars(select(Grade.value).distinct())
        too_precise = next((v for v in values if new.grade_error(Decimal(v))), None)
        if too_precise is not None:
            raise ConflictError(
                f"Hay notas cargadas con más de {new.grade_decimals} decimales (p. ej. "
                f"{fmt(Decimal(too_precise))}). Corrígelas antes de reducir los decimales."
            )


async def update_rules(db: AsyncSession, new: GradingRules) -> GradingRules:
    await _check_existing_grades(db, new)
    row = await db.get(AcademicSettings, SETTINGS_ID)
    if row is None:
        row = AcademicSettings(id=SETTINGS_ID)
        db.add(row)
    row.institution_name = new.institution_name
    row.period_type = new.period_type
    row.terms_count = new.terms_count
    row.term_label = new.term_label
    row.term_weights = None if new.term_weights is None else [float(w) for w in new.term_weights]
    row.min_grade = new.min_grade
    row.max_grade = new.max_grade
    row.passing_grade = new.passing_grade
    row.grade_decimals = new.grade_decimals
    row.round_final_grade = new.round_final_grade
    await db.commit()
    return _from_row(row)
