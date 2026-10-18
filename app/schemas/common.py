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

# Tipo de documento (V, E... los configura el super admin en /settings/academic)
DocumentCode = Annotated[
    str,
    AfterValidator(lambda v: v.strip().upper()),
    Field(
        pattern=r"^\s*[A-Za-z]{1,3}\s*$",
        description="Tipo de documento (p. ej. V = venezolano, E = extranjero); los válidos "
        "están en GET /settings/academic",
        examples=["V"],
    ),
]

NewPassword = Annotated[str, Field(min_length=8, max_length=128)]


class Page[T](BaseModel):
    """Respuesta paginada."""

    items: list[T]
    total: int
    limit: int
    offset: int
