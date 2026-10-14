"""Configuración de logs con el identificador de cada petición (X-Request-ID)."""

import logging
import logging.config
import re
import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Librerías que en INFO registran cada petición HTTP saliente. httpx, además, escribe la URL
# completa, y la de Telegram contiene el token del bot: nunca deben quedar en los logs.
_NOISY_LOGGERS = ("httpx", "httpcore", "telegram", "openai", "apscheduler", "mcp")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str = "INFO") -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"request_id": {"()": _RequestIdFilter}},
            "formatters": {
                "default": {
                    "format": "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"
                }
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                    "filters": ["request_id"],
                }
            },
            "root": {"level": level.upper(), "handlers": ["console"]},
            "loggers": {name: {"level": "WARNING"} for name in _NOISY_LOGGERS},
        }
    )


class RequestIdMiddleware:
    """Asigna un id a cada petición (o respeta el que envíe el cliente si es válido), lo pone
    en los logs y lo devuelve en la cabecera X-Request-ID para poder rastrear errores."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(REQUEST_ID_HEADER.lower().encode(), b"").decode()
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request_id_var.set(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
            await send(message)

        await self.app(scope, receive, send_with_id)
