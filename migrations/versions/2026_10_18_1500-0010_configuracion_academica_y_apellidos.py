"""configuracion academica y apellidos

- Tabla academic_settings (una fila): periodos, lapsos, pesos y escala de notas.
- users.full_name se divide en first_name + last_name.
- grades: fuera los CHECK fijos (lapso 1-3, nota 1-20) y la nota admite hasta 100.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-18 15:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERIOD_TYPES = ("semester", "trimester", "quadrimester", "annual")


def split_full_name(full_name: str) -> tuple[str, str]:
    """ "Ana María Pérez Gómez" -> ("Ana María", "Pérez Gómez"). Con un número impar de
    palabras el apellido se queda con la del medio ("Ana Pérez Gómez" -> "Ana",
    "Pérez Gómez"). Es una aproximación: se puede corregir desde la app."""
    words = full_name.split()
    if len(words) <= 1:
        return (words[0] if words else "", "")
    half = len(words) // 2
    return " ".join(words[:half]), " ".join(words[half:])


def upgrade() -> None:
    op.create_table(
        "academic_settings",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("institution_name", sa.String(150), nullable=False),
        sa.Column("period_type", sa.Enum(*PERIOD_TYPES, name="period_type"), nullable=False),
        sa.Column("terms_count", sa.SmallInteger(), nullable=False),
        sa.Column("term_label", sa.String(30), nullable=False),
        sa.Column("term_weights", sa.JSON(), nullable=True),
        sa.Column("min_grade", sa.Numeric(5, 2), nullable=False),
        sa.Column("max_grade", sa.Numeric(5, 2), nullable=False),
        sa.Column("passing_grade", sa.Numeric(5, 2), nullable=False),
        sa.Column("grade_decimals", sa.SmallInteger(), nullable=False),
        sa.Column("round_final_grade", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_academic_settings_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_academic_settings")),
    )
    # Mismos valores que las reglas fijas que había hasta ahora
    op.bulk_insert(
        sa.table(
            "academic_settings",
            sa.column("id", sa.Integer()),
            sa.column("institution_name", sa.String()),
            sa.column("period_type", sa.String()),
            sa.column("terms_count", sa.SmallInteger()),
            sa.column("term_label", sa.String()),
            sa.column("term_weights", sa.JSON()),
            sa.column("min_grade", sa.Numeric(5, 2)),
            sa.column("max_grade", sa.Numeric(5, 2)),
            sa.column("passing_grade", sa.Numeric(5, 2)),
            sa.column("grade_decimals", sa.SmallInteger()),
            sa.column("round_final_grade", sa.Boolean()),
        ),
        [
            {
                "id": 1,
                "institution_name": "Universidad",
                "period_type": "semester",
                "terms_count": 3,
                "term_label": "Lapso",
                "term_weights": None,
                "min_grade": 1,
                "max_grade": 20,
                "passing_grade": 10,
                "grade_decimals": 2,
                "round_final_grade": False,
            }
        ],
    )

    # --- Nombre y apellido -----------------------------------------------------
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("first_name", sa.String(100), nullable=False, server_default=""))
        batch.add_column(sa.Column("last_name", sa.String(100), nullable=False, server_default=""))
    users = sa.table(
        "users",
        sa.column("id", sa.Integer()),
        sa.column("full_name", sa.String()),
        sa.column("first_name", sa.String()),
        sa.column("last_name", sa.String()),
    )
    connection = op.get_bind()
    for user_id, full_name in connection.execute(sa.select(users.c.id, users.c.full_name)).all():
        first, last = split_full_name(full_name or "")
        connection.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(first_name=first[:100], last_name=last[:100])
        )
    with op.batch_alter_table("users") as batch:
        batch.alter_column("first_name", server_default=None, existing_type=sa.String(100))
        batch.alter_column("last_name", server_default=None, existing_type=sa.String(100))
        batch.drop_column("full_name")

    # --- Notas: rango según la configuración ----------------------------------
    with op.batch_alter_table("grades") as batch:
        batch.drop_constraint(op.f("ck_grades_term_range"), type_="check")
        batch.drop_constraint(op.f("ck_grades_value_range"), type_="check")
        batch.alter_column(
            "value",
            existing_type=sa.Numeric(4, 2),
            type_=sa.Numeric(5, 2),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Atención: notas fuera de 1-20 o en lapsos > 3 harían fallar los CHECK; se borran
    op.execute("DELETE FROM grades WHERE term > 3 OR value < 1 OR value > 20")
    with op.batch_alter_table("grades") as batch:
        batch.alter_column(
            "value",
            existing_type=sa.Numeric(5, 2),
            type_=sa.Numeric(4, 2),
            existing_nullable=False,
        )
        batch.create_check_constraint(op.f("ck_grades_term_range"), "term BETWEEN 1 AND 3")
        batch.create_check_constraint(op.f("ck_grades_value_range"), "value BETWEEN 1 AND 20")

    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("full_name", sa.String(150), nullable=False, server_default=""))
    users = sa.table(
        "users",
        sa.column("id", sa.Integer()),
        sa.column("full_name", sa.String()),
        sa.column("first_name", sa.String()),
        sa.column("last_name", sa.String()),
    )
    connection = op.get_bind()
    rows = connection.execute(sa.select(users.c.id, users.c.first_name, users.c.last_name)).all()
    for user_id, first, last in rows:
        connection.execute(
            users.update()
            .where(users.c.id == user_id)
            .values(full_name=f"{first} {last}".strip()[:150])
        )
    with op.batch_alter_table("users") as batch:
        batch.alter_column("full_name", server_default=None, existing_type=sa.String(150))
        batch.drop_column("first_name")
        batch.drop_column("last_name")

    op.drop_table("academic_settings")
