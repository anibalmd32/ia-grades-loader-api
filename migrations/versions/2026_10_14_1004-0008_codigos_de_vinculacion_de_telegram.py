"""codigos de vinculacion de telegram

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-14 10:04:13.190295

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_link_codes",
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["users.id"],
            name=op.f("fk_telegram_link_codes_student_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("code_hash", name=op.f("pk_telegram_link_codes")),
    )
    op.create_index(
        op.f("ix_telegram_link_codes_student_id"),
        "telegram_link_codes",
        ["student_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_telegram_link_codes_student_id"), table_name="telegram_link_codes")
    op.drop_table("telegram_link_codes")
