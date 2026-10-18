"""tipos de documento configurables

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-18 16:30:00

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_TYPES = [{"code": "V", "name": "Venezolano"}, {"code": "E", "name": "Extranjero"}]


def upgrade() -> None:
    # El tipo de documento deja de ser un ENUM fijo (V/E): código de 1 a 3 letras
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "nationality",
            existing_type=sa.Enum("V", "E", name="nationality"),
            type_=sa.String(3),
            existing_nullable=True,
        )
    with op.batch_alter_table("academic_settings") as batch:
        batch.add_column(
            sa.Column("document_label", sa.String(30), nullable=False, server_default="Cédula")
        )
        batch.add_column(sa.Column("document_types", sa.JSON(), nullable=True))
    op.execute(
        sa.text("UPDATE academic_settings SET document_types = :types").bindparams(
            types=json.dumps(DEFAULT_TYPES)
        )
    )
    with op.batch_alter_table("academic_settings") as batch:
        batch.alter_column("document_types", existing_type=sa.JSON(), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("academic_settings") as batch:
        batch.drop_column("document_types")
        batch.drop_column("document_label")
    # Atención: los estudiantes con otros tipos de documento se pasan a V
    op.execute("UPDATE users SET nationality = 'V' WHERE nationality NOT IN ('V', 'E')")
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "nationality",
            existing_type=sa.String(3),
            type_=sa.Enum("V", "E", name="nationality"),
            existing_nullable=True,
        )
