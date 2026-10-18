"""Ayudas para construir datos de prueba."""


def names(full_name: str) -> dict[str, str]:
    """ "Ana Pérez" -> {"first_name": "Ana", "last_name": "Pérez"}.

    Con una sola palabra el apellido es "Test" (la API exige ambos), así que esos usuarios se
    identifican en los tests por su first_name.
    """
    first, _, last = full_name.partition(" ")
    return {"first_name": first, "last_name": last or "Test"}
