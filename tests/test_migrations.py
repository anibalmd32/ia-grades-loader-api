import importlib.util
from pathlib import Path

import pytest

MIGRATION = next(Path(__file__).resolve().parent.parent.glob("migrations/versions/*-0010_*.py"))


@pytest.fixture(scope="module")
def split_full_name():
    spec = importlib.util.spec_from_file_location("migration_0010", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.split_full_name


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("Ana María Pérez Gómez", ("Ana María", "Pérez Gómez")),
        ("Ana Pérez Gómez", ("Ana", "Pérez Gómez")),
        ("  Ana   Pérez ", ("Ana", "Pérez")),
        ("Admin", ("Admin", "")),
        ("", ("", "")),
    ],
)
def test_separa_nombre_y_apellido_de_los_usuarios_existentes(split_full_name, full_name, expected):
    assert split_full_name(full_name) == expected
