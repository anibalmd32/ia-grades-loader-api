"""notas con decimales

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-13 19:19:41.360748

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "grades",
        "value",
        existing_type=mysql.SMALLINT(),
        type_=sa.Numeric(precision=4, scale=2),
        existing_nullable=False,
    )


def downgrade() -> None:
    # Atención: al volver a SMALLINT MySQL redondea las notas con decimales
    op.alter_column(
        "grades",
        "value",
        existing_type=sa.Numeric(precision=4, scale=2),
        type_=mysql.SMALLINT(),
        existing_nullable=False,
    )
