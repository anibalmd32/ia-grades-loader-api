"""reglas congeladas por periodo

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-18 16:10:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("semesters") as batch:
        batch.add_column(sa.Column("rules", sa.JSON(none_as_null=True), nullable=True))
    # Los periodos ya cerrados se evaluaron con la configuración actual: se congela
    settings = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT terms_count, term_label, term_weights, min_grade, max_grade, "
                "passing_grade, grade_decimals, round_final_grade FROM academic_settings"
            )
        )
        .first()
    )
    if settings is None:
        return
    import json

    weights = settings.term_weights
    if isinstance(weights, str):
        weights = json.loads(weights)
    snapshot = {
        "terms_count": settings.terms_count,
        "term_label": settings.term_label,
        "term_weights": None if weights is None else [str(w) for w in weights],
        "min_grade": str(settings.min_grade),
        "max_grade": str(settings.max_grade),
        "passing_grade": str(settings.passing_grade),
        "grade_decimals": settings.grade_decimals,
        "round_final_grade": bool(settings.round_final_grade),
    }
    semesters = sa.table(
        "semesters", sa.column("is_closed", sa.Boolean()), sa.column("rules", sa.JSON())
    )
    op.get_bind().execute(
        semesters.update().where(semesters.c.is_closed.is_(True)).values(rules=snapshot)
    )


def downgrade() -> None:
    with op.batch_alter_table("semesters") as batch:
        batch.drop_column("rules")
