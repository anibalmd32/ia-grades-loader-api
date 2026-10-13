"""Tipos reutilizables para validar credenciales."""

import re
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator, EmailStr, Field


def _digits_only(value: Any) -> Any:
    # Acepta "12.345.678" o "12345678" y lo convierte a número plano
    if isinstance(value, str):
        cleaned = re.sub(r"[\s.\-]", "", value)
        return int(cleaned) if cleaned.isdigit() else value
    return value


CedulaNumber = Annotated[
    int,
    BeforeValidator(_digits_only),
    Field(
        gt=0,
        le=999_999_999,
        description="Número de cédula sin letras ni puntos",
        examples=[12345678],
    ),
]

Email = Annotated[EmailStr, AfterValidator(str.lower)]

NewPassword = Annotated[str, Field(min_length=8, max_length=128)]


class Page[T](BaseModel):
    """Respuesta paginada."""

    items: list[T]
    total: int
    limit: int
    offset: int
