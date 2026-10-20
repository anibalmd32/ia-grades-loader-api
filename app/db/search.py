"""Búsquedas de texto con LIKE."""

LIKE_ESCAPE = "\\"


def contains_pattern(term: str) -> str:
    """Patrón LIKE "contiene" con los comodines del usuario (% y _) tratados como texto.

    Úsalo con `columna.ilike(contains_pattern(texto), escape=LIKE_ESCAPE)`.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
