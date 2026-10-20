"""Middlewares de seguridad: cabeceras, tamaño máximo de las peticiones y errores 500.

Son ASGI puros (no BaseHTTPMiddleware) para no romper las respuestas en streaming (chat y
avisos en tiempo real).
"""

import logging

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

logger = logging.getLogger(__name__)

# Rutas que sirven HTML (Swagger y ReDoc cargan scripts de un CDN): sin CSP restrictiva
_DOCS_PATHS = ("/docs", "/redoc")


class SecurityHeadersMiddleware:
    """Cabeceras de seguridad en todas las respuestas.

    La API solo devuelve JSON (y Excel): no se puede incrustar en otra página, el navegador no
    debe adivinar el tipo de contenido y, por llevar datos personales, nada se guarda en
    cachés salvo que la ruta diga otra cosa.
    """

    def __init__(self, app: ASGIApp, hsts: bool = False):
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_docs = scope["path"].startswith(_DOCS_PATHS)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}

                def add(name: str, value: str) -> None:
                    if name.encode() not in present:
                        headers.append((name.encode(), value.encode()))

                add("x-content-type-options", "nosniff")
                add("x-frame-options", "DENY")
                add("referrer-policy", "no-referrer")
                add("cross-origin-opener-policy", "same-origin")
                add("permissions-policy", "camera=(), microphone=(), geolocation=()")
                if not is_docs:
                    add("content-security-policy", "default-src 'none'; frame-ancestors 'none'")
                    add("cache-control", "no-store")
                if self.hsts:
                    add("strict-transport-security", "max-age=31536000; includeSubDomains")
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _BodyTooLargeError(Exception):
    pass


class BodySizeLimitMiddleware:
    """Rechaza con 413 las peticiones más grandes que el límite *antes* de leerlas enteras.

    Sin esto, FastAPI recibe y guarda en disco un multipart completo (de cualquier tamaño)
    antes de comprobar siquiera el token.
    """

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > self.max_bytes):
            await self._reject(scope, receive, send)
            return

        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _BodyTooLargeError:
            if started:
                raise
            await self._reject(scope, receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit_mb = self.max_bytes // (1024 * 1024)
        response = JSONResponse(
            status_code=413,
            content={"detail": f"La petición es demasiado grande (máximo {limit_mb} MB)."},
            headers={"Connection": "close"},
        )
        await response(scope, receive, send)


class UnexpectedErrorMiddleware:
    """Convierte cualquier excepción no controlada en un 500 genérico con el id de la petición.

    Va por dentro de CORS: así el 500 lleva sus cabeceras y el front puede leer el
    `request_id` (el manejador de errores de Starlette queda por fuera de todos los
    middlewares y su respuesta el navegador la bloquea). Nunca devuelve el detalle del error.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            if started:  # una respuesta en streaming ya empezada no se puede reemplazar
                raise
            request_id = request_id_var.get()
            logger.exception("Error inesperado en %s %s", scope["method"], scope["path"])
            response = JSONResponse(
                status_code=500,
                content={
                    "detail": "Error interno del servidor. Si persiste, informa este código.",
                    "request_id": request_id,
                },
            )  # RequestIdMiddleware añade la cabecera X-Request-ID
            await response(scope, receive, send)
