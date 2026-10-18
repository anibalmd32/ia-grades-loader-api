"""historial de notas

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-18 16:20:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grade_changes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enrollment_id", sa.Integer(), nullable=False),
        sa.Column("term", sa.SmallInteger(), nullable=False),
        sa.Column("old_value", sa.Numeric(5, 2), nullable=True),
        sa.Column("new_value", sa.Numeric(5, 2), nullable=True),
        sa.Column("changed_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "source", sa.Enum("manual", "import", name="grade_change_source"), nullable=False
        ),
        sa.Column("import_job_id", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["enrollment_id"],
            ["enrollments.id"],
            name=op.f("fk_grade_changes_enrollment_id_enrollments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_id"],
            ["users.id"],
            name=op.f("fk_grade_changes_changed_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_grade_changes_import_job_id_import_jobs"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_grade_changes")),
    )
    op.create_index(
        "ix_grade_changes_enrollment_id_created_at",
        "grade_changes",
        ["enrollment_id", "created_at"],
    )
    # Punto de partida: cada nota existente como alta hecha por quien la cargó por última vez
    op.execute(
        "INSERT INTO grade_changes "
        "(enrollment_id, term, old_value, new_value, changed_by_id, source, created_at) "
        "SELECT enrollment_id, term, NULL, value, updated_by_id, 'manual', updated_at "
        "FROM grades"
    )


def downgrade() -> None:
    op.drop_table("grade_changes")
