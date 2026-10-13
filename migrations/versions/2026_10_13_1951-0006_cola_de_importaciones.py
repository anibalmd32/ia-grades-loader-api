"""cola de importaciones

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-13 19:51:37.434570

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "processing", "done", "failed", name="import_job_status"),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column(
            "file_content",
            sa.LargeBinary().with_variant(mysql.MEDIUMBLOB(), "mysql"),
            nullable=True,
        ),
        sa.Column("default_term", sa.SmallInteger(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_import_jobs_subject_id_subjects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["users.id"],
            name=op.f("fk_import_jobs_teacher_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_jobs")),
    )
    op.create_index(
        "ix_import_jobs_status_created_at", "import_jobs", ["status", "created_at"], unique=False
    )
    op.create_index(op.f("ix_import_jobs_teacher_id"), "import_jobs", ["teacher_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_import_jobs_teacher_id"), table_name="import_jobs")
    op.drop_index("ix_import_jobs_status_created_at", table_name="import_jobs")
    op.drop_table("import_jobs")
