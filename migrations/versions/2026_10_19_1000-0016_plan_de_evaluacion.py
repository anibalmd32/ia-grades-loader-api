"""plan de evaluación por lapso

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-19 10:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_SOURCES = sa.Enum("manual", "import", name="grade_change_source")
NEW_SOURCES = sa.Enum("manual", "import", "evaluations", name="grade_change_source")


def upgrade() -> None:
    op.create_table(
        "evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("term", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("weight", sa.Numeric(5, 2), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_evaluations_subject_id_subjects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluations")),
        sa.UniqueConstraint(
            "subject_id", "term", "name", name=op.f("uq_evaluations_subject_id_term_name")
        ),
    )
    op.create_table(
        "evaluation_grades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("evaluation_id", sa.Integer(), nullable=False),
        sa.Column("enrollment_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Numeric(5, 2), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["evaluation_id"],
            ["evaluations.id"],
            name=op.f("fk_evaluation_grades_evaluation_id_evaluations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["enrollment_id"],
            ["enrollments.id"],
            name=op.f("fk_evaluation_grades_enrollment_id_enrollments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_evaluation_grades_updated_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_grades")),
        sa.UniqueConstraint(
            "evaluation_id",
            "enrollment_id",
            name=op.f("uq_evaluation_grades_evaluation_id_enrollment_id"),
        ),
    )
    with op.batch_alter_table("grade_changes") as batch:
        batch.add_column(sa.Column("evaluation_name", sa.String(100), nullable=True))
        batch.alter_column(
            "source", existing_type=OLD_SOURCES, type_=NEW_SOURCES, existing_nullable=False
        )


def downgrade() -> None:
    op.execute("UPDATE grade_changes SET source = 'manual' WHERE source = 'evaluations'")
    with op.batch_alter_table("grade_changes") as batch:
        batch.alter_column(
            "source", existing_type=NEW_SOURCES, type_=OLD_SOURCES, existing_nullable=False
        )
        batch.drop_column("evaluation_name")
    op.drop_table("evaluation_grades")
    op.drop_table("evaluations")
