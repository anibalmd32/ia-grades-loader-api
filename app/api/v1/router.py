from fastapi import APIRouter

from app.api.v1.routes import (
    assignments,
    auth,
    enrollments,
    health,
    semesters,
    setup,
    subjects,
    teacher,
    users,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(setup.router)
api_router.include_router(auth.router)
api_router.include_router(users.coordinators_router)
api_router.include_router(users.teachers_router)
api_router.include_router(users.students_router)
api_router.include_router(semesters.router)
api_router.include_router(subjects.router)
api_router.include_router(assignments.router)
api_router.include_router(enrollments.router)
api_router.include_router(teacher.router)
