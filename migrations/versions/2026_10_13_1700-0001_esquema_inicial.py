"""esquema inicial

Revision ID: 0001
Revises:
Create Date: 2026-10-13 17:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("super_admin", "coordinator", "teacher", "student")


def timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cedula", sa.String(20), nullable=False),
        sa.Column("full_name", sa.String(150), nullable=False),
        sa.Column("role", sa.Enum(*ROLES, name="user_role"), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("must_change_password", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("telegram_chat_id", name=op.f("uq_users_telegram_chat_id")),
    )
    op.create_index(op.f("ix_users_cedula"), "users", ["cedula"], unique=True)

    op.create_table(
        "semesters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_semesters")),
        sa.UniqueConstraint("name", name=op.f("uq_semesters_name")),
    )

    op.create_table(
        "subjects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("semester_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["semester_id"],
            ["semesters.id"],
            name=op.f("fk_subjects_semester_id_semesters"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subjects")),
        sa.UniqueConstraint("code", name=op.f("uq_subjects_code")),
    )

    op.create_table(
        "teacher_subjects",
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["teacher_id"],
            ["users.id"],
            name=op.f("fk_teacher_subjects_teacher_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_teacher_subjects_subject_id_subjects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("teacher_id", "subject_id", name=op.f("pk_teacher_subjects")),
    )

    op.create_table(
        "enrollments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["student_id"],
            ["users.id"],
            name=op.f("fk_enrollments_student_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_enrollments_subject_id_subjects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_enrollments")),
        sa.UniqueConstraint(
            "student_id", "subject_id", name=op.f("uq_enrollments_student_id_subject_id")
        ),
    )

    op.create_table(
        "grades",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("enrollment_id", sa.Integer(), nullable=False),
        sa.Column("term", sa.SmallInteger(), nullable=False),
        sa.Column("value", sa.SmallInteger(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        *timestamps(),
        sa.CheckConstraint("term BETWEEN 1 AND 3", name=op.f("ck_grades_term_range")),
        sa.CheckConstraint("value BETWEEN 1 AND 20", name=op.f("ck_grades_value_range")),
        sa.ForeignKeyConstraint(
            ["enrollment_id"],
            ["enrollments.id"],
            name=op.f("fk_grades_enrollment_id_enrollments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["users.id"],
            name=op.f("fk_grades_updated_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_grades")),
        sa.UniqueConstraint("enrollment_id", "term", name=op.f("uq_grades_enrollment_id_term")),
    )

    op.create_table(
        "installation",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("super_admin_id", sa.Integer(), nullable=False),
        sa.Column(
            "installed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_installation_singleton")),
        sa.ForeignKeyConstraint(
            ["super_admin_id"],
            ["users.id"],
            name=op.f("fk_installation_super_admin_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_installation")),
    )


def downgrade() -> None:
    op.drop_table("installation")
    op.drop_table("grades")
    op.drop_table("enrollments")
    op.drop_table("teacher_subjects")
    op.drop_table("subjects")
    op.drop_table("semesters")
    op.drop_index(op.f("ix_users_cedula"), table_name="users")
    op.drop_table("users")
