"""Modelos ORM. Importarlos aquí registra todas las tablas en Base.metadata (Alembic)."""

from app.models.academic import Semester, Subject, TeacherSubject
from app.models.conversation import ChatChannel, Conversation, ConversationMessage, MessageRole
from app.models.enrollment import Enrollment, Grade
from app.models.import_job import ImportJob, JobStatus
from app.models.installation import Installation
from app.models.revoked_token import RevokedToken
from app.models.user import STAFF_ROLES, Nationality, Role, User

__all__ = [
    "STAFF_ROLES",
    "ChatChannel",
    "Conversation",
    "ConversationMessage",
    "Enrollment",
    "Grade",
    "ImportJob",
    "Installation",
    "JobStatus",
    "MessageRole",
    "Nationality",
    "RevokedToken",
    "Role",
    "Semester",
    "Subject",
    "TeacherSubject",
    "User",
]
