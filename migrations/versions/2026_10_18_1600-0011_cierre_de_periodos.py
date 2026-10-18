"""cierre de periodos

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-18 16:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("semesters") as batch:
        batch.add_column(sa.Column("is_closed", sa.Boolean(), server_default="0", nullable=False))
        batch.add_column(sa.Column("closed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("semesters") as batch:
        batch.drop_column("closed_at")
        batch.drop_column("is_closed")
