from fastapi import APIRouter

from app.api.v1.routes import (
    assignments,
    auth,
    enrollments,
    evaluations,
    health,
    sections,
    semesters,
    settings,
    setup,
    student,
    student_import,
    subjects,
    teacher,
    telegram,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(setup.router)
api_router.include_router(settings.router)
api_router.include_router(auth.router)
api_router.include_router(users.coordinators_router)
api_router.include_router(users.teachers_router)
api_router.include_router(student_import.router)
api_router.include_router(users.students_router)
api_router.include_router(semesters.router)
api_router.include_router(sections.router)
api_router.include_router(subjects.router)
api_router.include_router(assignments.router)
api_router.include_router(enrollments.router)
api_router.include_router(teacher.router)
api_router.include_router(evaluations.router)
api_router.include_router(student.router)
api_router.include_router(telegram.router)
