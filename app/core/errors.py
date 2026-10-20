"""Errores de negocio que los servicios lanzan y la app convierte en respuestas HTTP.

Todas las respuestas de error tienen la forma {"detail": ...}; las inesperadas (500) añaden
"request_id" para poder buscarlas en los logs.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
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


class InvalidDataError(AppError):
    """Datos con la forma correcta pero que incumplen una regla de negocio (p. ej. una nota
    fuera de la escala configurada)."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT


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


async def _handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Igual que el de FastAPI pero sin "input": devolvería lo que se envió, contraseñas
    # incluidas (p. ej. una contraseña nueva demasiado corta)
    errors = [{k: v for k, v in error.items() if k != "input"} for error in exc.errors()]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": jsonable_encoder(errors)},
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected)
