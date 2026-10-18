"""Errores de negocio que los servicios lanzan y la app convierte en respuestas HTTP.

Todas las respuestas de error tienen la forma {"detail": ...}; las inesperadas (500) añaden
"request_id" para poder buscarlas en los logs.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.logging import request_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail

    @property
    def headers(self) -> dict[str, str] | None:
        """Cabeceras extra de la respuesta (p. ej. Retry-After en los 429)."""
        return None


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT


async def _handle_app_error(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_var.get()
    logger.exception("Error inesperado en %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Error interno del servidor. Si persiste, informa este código.",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(Exception, _handle_unexpected)
