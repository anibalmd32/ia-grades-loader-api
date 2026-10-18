"""confirmacion de importaciones

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-18 11:29:57.440315

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("import_jobs", sa.Column("confirmed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("import_jobs", "confirmed_at")
