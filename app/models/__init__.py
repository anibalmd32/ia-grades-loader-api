"""Modelos ORM. Importarlos aquí registra todas las tablas en Base.metadata (Alembic)."""

from app.models.academic import Section, Semester, StudentSection, Subject, TeacherSubject
from app.models.biometric_device import BiometricDevice
from app.models.conversation import ChatChannel, Conversation, ConversationMessage, MessageRole
from app.models.enrollment import Enrollment, Grade
from app.models.evaluation import Evaluation, EvaluationGrade
from app.models.grade_change import ChangeSource, GradeChange
from app.models.import_job import ImportJob, JobStatus
from app.models.installation import Installation
from app.models.notification import Notification
from app.models.revoked_token import RevokedToken
from app.models.settings import AcademicSettings, PeriodType
from app.models.telegram_link import TelegramLinkCode
from app.models.user import STAFF_ROLES, Nationality, Role, User

__all__ = [
    "AcademicSettings",
    "BiometricDevice",
    "STAFF_ROLES",
    "ChangeSource",
    "ChatChannel",
    "Conversation",
    "ConversationMessage",
    "Enrollment",
    "Evaluation",
    "EvaluationGrade",
    "Grade",
    "GradeChange",
    "ImportJob",
    "Installation",
    "JobStatus",
    "MessageRole",
    "Nationality",
    "Notification",
    "PeriodType",
    "RevokedToken",
    "Role",
    "Section",
    "Semester",
    "StudentSection",
    "Subject",
    "TeacherSubject",
    "TelegramLinkCode",
    "User",
]
