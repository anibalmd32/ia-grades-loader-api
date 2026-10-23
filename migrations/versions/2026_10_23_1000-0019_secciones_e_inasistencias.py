"""secciones por periodo (un profesor por materia y sección) e inasistencias

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-23 10:00:00

Datos existentes: cada periodo recibe una sección «A» con todos sus estudiantes matriculados,
y cada materia queda con un solo profesor en esa sección (el de menor id si tenía varios: los
demás se desasignan). Los planes de evaluación y las importaciones pasan a la sección «A».
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FIRST_SECTION = "A"

# Sección «A» del periodo de la materia de cada fila (evaluaciones, importaciones)
SECTION_OF_SUBJECT = (
    "SELECT sections.id FROM sections JOIN subjects "
    "ON subjects.semester_id = sections.semester_id "
    "WHERE subjects.id = {table}.subject_id AND sections.name = '" + FIRST_SECTION + "'"
)


def timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def _fk(table: str, column: str, referred: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{referred}.id"],
        name=op.f(f"fk_{table}_{column}_{referred}"),
        ondelete=ondelete,
    )


def _create_teacher_subjects(with_sections: bool) -> None:
    if with_sections:
        columns = [
            sa.Column("subject_id", sa.Integer(), nullable=False),
            sa.Column("section_id", sa.Integer(), nullable=False),
            sa.Column("teacher_id", sa.Integer(), nullable=False),
            _fk("teacher_subjects", "subject_id", "subjects", "CASCADE"),
            _fk("teacher_subjects", "section_id", "sections", "CASCADE"),
            _fk("teacher_subjects", "teacher_id", "users", "CASCADE"),
            sa.PrimaryKeyConstraint("subject_id", "section_id", name=op.f("pk_teacher_subjects")),
        ]
    else:
        columns = [
            sa.Column("teacher_id", sa.Integer(), nullable=False),
            sa.Column("subject_id", sa.Integer(), nullable=False),
            _fk("teacher_subjects", "teacher_id", "users", "CASCADE"),
            _fk("teacher_subjects", "subject_id", "subjects", "CASCADE"),
            sa.PrimaryKeyConstraint("teacher_id", "subject_id", name=op.f("pk_teacher_subjects")),
        ]
    op.create_table("teacher_subjects", *columns)
    if with_sections:
        op.create_index(op.f("ix_teacher_subjects_teacher_id"), "teacher_subjects", ["teacher_id"])


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("semester_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(20), nullable=False),
        *timestamps(),
        _fk("sections", "semester_id", "semesters", "CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sections")),
        sa.UniqueConstraint("semester_id", "name", name=op.f("uq_sections_semester_id_name")),
    )
    op.create_table(
        "student_sections",
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("semester_id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        _fk("student_sections", "student_id", "users", "CASCADE"),
        _fk("student_sections", "semester_id", "semesters", "CASCADE"),
        _fk("student_sections", "section_id", "sections", "CASCADE"),
        sa.PrimaryKeyConstraint("student_id", "semester_id", name=op.f("pk_student_sections")),
    )
    op.create_index(op.f("ix_student_sections_section_id"), "student_sections", ["section_id"])

    # Sección «A» en cada periodo, con todos los estudiantes que cursan materias en él
    bind.execute(
        sa.text("INSERT INTO sections (semester_id, name) SELECT id, :name FROM semesters"),
        {"name": FIRST_SECTION},
    )
    bind.execute(
        sa.text(
            "INSERT INTO student_sections (student_id, semester_id, section_id) "
            "SELECT DISTINCT enrollments.student_id, subjects.semester_id, sections.id "
            "FROM enrollments "
            "JOIN subjects ON subjects.id = enrollments.subject_id "
            "JOIN sections ON sections.semester_id = subjects.semester_id"
        )
    )

    # Un solo profesor por materia y sección: se conserva el de menor id
    assignments = bind.execute(
        sa.text(
            "SELECT teacher_subjects.subject_id, sections.id, MIN(teacher_subjects.teacher_id) "
            "FROM teacher_subjects "
            "JOIN subjects ON subjects.id = teacher_subjects.subject_id "
            "JOIN sections ON sections.semester_id = subjects.semester_id "
            "GROUP BY teacher_subjects.subject_id, sections.id"
        )
    ).all()
    op.drop_table("teacher_subjects")
    _create_teacher_subjects(with_sections=True)
    if assignments:
        bind.execute(
            sa.text(
                "INSERT INTO teacher_subjects (subject_id, section_id, teacher_id) "
                "VALUES (:subject, :section, :teacher)"
            ),
            [{"subject": s, "section": sec, "teacher": t} for s, sec, t in assignments],
        )

    # Planes de evaluación e importaciones: de la sección «A»
    for table in ("evaluations", "import_jobs"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("section_id", sa.Integer(), nullable=True))
        bind.execute(
            sa.text(f"UPDATE {table} SET section_id = ({SECTION_OF_SUBJECT.format(table=table)})")
        )
        with op.batch_alter_table(table) as batch:
            batch.alter_column("section_id", existing_type=sa.Integer(), nullable=False)
            batch.create_foreign_key(
                op.f(f"fk_{table}_section_id_sections"),
                "sections",
                ["section_id"],
                ["id"],
                ondelete="CASCADE",
            )
    with op.batch_alter_table("evaluations") as batch:
        # Primero el nuevo: también empieza por subject_id y sirve de índice a su FK (MySQL)
        batch.create_unique_constraint(
            op.f("uq_evaluations_subject_id_section_id_term_name"),
            ["subject_id", "section_id", "term", "name"],
        )
        batch.drop_constraint(op.f("uq_evaluations_subject_id_term_name"), type_="unique")

    # Inasistencias
    for table in ("grades", "evaluation_grades"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("absent", sa.Boolean(), server_default="0", nullable=False))
    with op.batch_alter_table("grade_changes") as batch:
        batch.add_column(sa.Column("old_absent", sa.Boolean(), server_default="0", nullable=False))
        batch.add_column(sa.Column("new_absent", sa.Boolean(), server_default="0", nullable=False))


def downgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("grade_changes") as batch:
        batch.drop_column("new_absent")
        batch.drop_column("old_absent")
    for table in ("evaluation_grades", "grades"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("absent")

    # Se conservan los planes de una sola sección por materia (la de menor id)
    kept = dict(
        bind.execute(
            sa.text("SELECT subject_id, MIN(section_id) FROM evaluations GROUP BY subject_id")
        ).all()
    )
    for subject_id, section_id in kept.items():
        bind.execute(
            sa.text(
                "DELETE FROM evaluations WHERE subject_id = :subject AND section_id <> :section"
            ),
            {"subject": subject_id, "section": section_id},
        )
    with op.batch_alter_table("evaluations") as batch:
        batch.drop_constraint(op.f("fk_evaluations_section_id_sections"), type_="foreignkey")
        batch.create_unique_constraint(
            op.f("uq_evaluations_subject_id_term_name"), ["subject_id", "term", "name"]
        )
        batch.drop_constraint(
            op.f("uq_evaluations_subject_id_section_id_term_name"), type_="unique"
        )
        batch.drop_column("section_id")
    with op.batch_alter_table("import_jobs") as batch:
        batch.drop_constraint(op.f("fk_import_jobs_section_id_sections"), type_="foreignkey")
        batch.drop_column("section_id")

    assignments = bind.execute(
        sa.text("SELECT DISTINCT teacher_id, subject_id FROM teacher_subjects")
    ).all()
    op.drop_table("teacher_subjects")
    _create_teacher_subjects(with_sections=False)
    if assignments:
        bind.execute(
            sa.text(
                "INSERT INTO teacher_subjects (teacher_id, subject_id) VALUES (:teacher, :subject)"
            ),
            [{"teacher": t, "subject": s} for t, s in assignments],
        )
    op.drop_table("student_sections")
    op.drop_table("sections")
