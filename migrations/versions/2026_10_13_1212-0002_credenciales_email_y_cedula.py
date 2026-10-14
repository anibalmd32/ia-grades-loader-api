"""credenciales email y cedula con nacionalidad

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13 12:12:46.749657

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # El personal pasa a iniciar sesión con email y los estudiantes con tipo + número de cédula.
    # La tabla users está vacía en este punto, por lo que el cambio de tipo de cedula es seguro.
    op.add_column("users", sa.Column("email", sa.String(length=255), nullable=True))
    op.add_column(
        "users", sa.Column("nationality", sa.Enum("V", "E", name="nationality"), nullable=True)
    )
    op.alter_column(
        "users", "cedula", existing_type=mysql.VARCHAR(length=20), type_=sa.Integer(), nullable=True
    )
    op.drop_index(op.f("ix_users_cedula"), table_name="users")
    op.create_unique_constraint(op.f("uq_users_email"), "users", ["email"])
    op.create_unique_constraint(
        op.f("uq_users_nationality_cedula"), "users", ["nationality", "cedula"]
    )
    op.create_check_constraint(
        op.f("ck_users_cedula_positive"), "users", "cedula IS NULL OR cedula > 0"
    )
    op.create_check_constraint(
        op.f("ck_users_staff_requires_email"), "users", "role = 'student' OR email IS NOT NULL"
    )
    op.create_check_constraint(
        op.f("ck_users_student_requires_cedula"),
        "users",
        "role <> 'student' OR (cedula IS NOT NULL AND nationality IS NOT NULL)",
    )


def downgrade() -> None:
    # Solo funciona con la tabla users vacía: el personal no tiene cédula y la columna vuelve a
    # ser obligatoria. Es un cambio estructural sin vuelta atrás con datos reales.
    op.drop_constraint(op.f("ck_users_student_requires_cedula"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_staff_requires_email"), "users", type_="check")
    op.drop_constraint(op.f("ck_users_cedula_positive"), "users", type_="check")
    op.drop_constraint(op.f("uq_users_nationality_cedula"), "users", type_="unique")
    op.drop_constraint(op.f("uq_users_email"), "users", type_="unique")
    op.create_index(op.f("ix_users_cedula"), "users", ["cedula"], unique=True)
    op.alter_column(
        "users",
        "cedula",
        existing_type=sa.Integer(),
        type_=mysql.VARCHAR(length=20),
        nullable=False,
    )
    op.drop_column("users", "nationality")
    op.drop_column("users", "email")
