"""Modelos ORM. Importarlos aquí registra todas las tablas en Base.metadata (Alembic)."""

from app.models.academic import Semester, Subject, TeacherSubject
from app.models.enrollment import Enrollment, Grade
from app.models.installation import Installation
from app.models.user import Role, User

__all__ = [
    "Enrollment",
    "Grade",
    "Installation",
    "Role",
    "Semester",
    "Subject",
    "TeacherSubject",
    "User",
]
