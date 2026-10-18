"""activar asistente y telegram

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-18 16:40:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("academic_settings") as batch:
        batch.add_column(
            sa.Column("assistant_enabled", sa.Boolean(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column("telegram_enabled", sa.Boolean(), nullable=False, server_default="1")
        )


def downgrade() -> None:
    with op.batch_alter_table("academic_settings") as batch:
        batch.drop_column("telegram_enabled")
        batch.drop_column("assistant_enabled")
