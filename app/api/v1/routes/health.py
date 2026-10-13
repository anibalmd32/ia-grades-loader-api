from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(tags=["Salud"])


class HealthResponse(BaseModel):
    status: str
    app: str
    env: str
    llm_enabled: bool
    telegram_enabled: bool


@router.get("/health", summary="Estado del servicio")
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        env=settings.app_env,
        llm_enabled=settings.llm_enabled,
        telegram_enabled=settings.telegram_enabled,
    )
