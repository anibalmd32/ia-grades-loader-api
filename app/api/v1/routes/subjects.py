from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, require_roles
from app.models import Role
from app.schemas.academic import SectionTeacher, SubjectCreate, SubjectRead, SubjectUpdate
from app.schemas.common import Page
from app.schemas.grades import GradeChangeRead
from app.services import academic, assignments, grades

router = APIRouter(
    prefix="/subjects",
    tags=["Materias"],
    dependencies=[Depends(require_roles(Role.COORDINATOR))],
)


@router.get("", summary="Listar materias")
async def list_subjects(
    db: DbSession,
    semester_id: int | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Busca en código o nombre")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[SubjectRead]:
    items, total = await academic.list_subjects(
        db, semester_id=semester_id, q=q, limit=limit, offset=offset
    )
    counts = await academic.subject_counts(db, [s.id for s in items])
    teachers = await assignments.section_teachers(db, items)
    return Page(
        items=[_with_counts(s, counts, teachers) for s in items],
        total=total,
        limit=limit,
        offset=offset,
    )


def _with_counts(subject, counts: dict[int, tuple[int, int]], by_section) -> SubjectRead:
    teachers, students = counts[subject.id]
    section_teachers = [
        SectionTeacher(
            section_id=section.id,
            section_name=section.name,
            teacher_id=None if teacher is None else teacher.id,
            teacher_name=None if teacher is None else teacher.full_name,
        )
        for section, teacher in by_section[subject.id]
    ]
    return SubjectRead.model_validate(subject).model_copy(
        update={
            "teachers_count": teachers,
            "students_count": students,
            "section_teachers": section_teachers,
        }
    )


@router.post("", status_code=status.HTTP_201_CREATED, summary="Crear materia")
async def create_subject(data: SubjectCreate, db: DbSession) -> SubjectRead:
    subject = await academic.create_subject(db, data.semester_id, data.code, data.name)
    return SubjectRead.model_validate(subject)


@router.get("/{subject_id}", summary="Ver materia")
async def get_subject(subject_id: int, db: DbSession) -> SubjectRead:
    subject = await academic.get_subject(db, subject_id)
    return _with_counts(
        subject,
        await academic.subject_counts(db, [subject.id]),
        await assignments.section_teachers(db, [subject]),
    )


@router.patch("/{subject_id}", summary="Modificar materia")
async def update_subject(subject_id: int, data: SubjectUpdate, db: DbSession) -> SubjectRead:
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    return SubjectRead.model_validate(await academic.update_subject(db, subject_id, changes))


@router.delete(
    "/{subject_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar materia (solo si no tiene estudiantes matriculados)",
)
async def delete_subject(subject_id: int, db: DbSession) -> None:
    await academic.delete_subject(db, subject_id)


@router.get("/{subject_id}/grade-history", summary="Historial de cambios de notas de la materia")
async def subject_grade_history(
    subject_id: int,
    db: DbSession,
    student_id: Annotated[int | None, Query(description="Solo los de este estudiante")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[GradeChangeRead]:
    """Altas, correcciones y borrados de notas, del más reciente al más antiguo, con quién
    los hizo y si fueron a mano o desde una importación de Excel."""
    subject = await academic.get_subject(db, subject_id)
    items, total = await grades.history(
        db, subject.id, student_id=student_id, limit=limit, offset=offset
    )
    return Page(
        items=[GradeChangeRead.from_row(*row) for row in items],
        total=total,
        limit=limit,
        offset=offset,
    )
