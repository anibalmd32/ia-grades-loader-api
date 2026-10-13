from fastapi import APIRouter

from app.api.v1.routes import auth, health, semesters, setup, subjects

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(setup.router)
api_router.include_router(auth.router)
api_router.include_router(semesters.router)
api_router.include_router(subjects.router)
