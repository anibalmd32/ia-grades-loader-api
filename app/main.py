import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers
from app.core.logging import REQUEST_ID_HEADER, RequestIdMiddleware, configure_logging
from app.db.session import SessionLocal
from app.mcp.server import create_mcp

logger = logging.getLogger(__name__)

DESCRIPTION = """
API para que los profesores carguen notas (manualmente o desde Excel con ayuda de IA)
y los estudiantes las consulten de forma tradicional, en lenguaje natural o por Telegram.

* Notas por **lapso**. La escala, el número de lapsos y su peso, el tipo de periodo
  (semestre, trimestre...) y la nota aprobatoria los configura el super admin en
  `/settings/academic`. Por defecto: del 1 al 20, 3 lapsos por semestre, se aprueba con 10.
* No hay registro público: el primer usuario (super admin) se crea con `POST /setup`.

### Cómo autenticarse en esta página

Swagger **no guarda solo** el token que devuelven `/setup` o `/auth/login/*`. Para usar los
endpoints protegidos:

1. Pulsa **Authorize** (arriba a la derecha).
2. En `username` escribe el email (personal) o la cédula con su tipo, p. ej. `V-12345678`
   (estudiantes); en `password`, la contraseña. Deja `client_id` y `client_secret` vacíos.
3. Desde ese momento Swagger envía el token en cada petición.

Para cerrar sesión llama a `POST /auth/logout` (revoca el token en el servidor) y luego pulsa
**Authorize → Logout** para que Swagger lo olvide.
"""

OPENAPI_TAGS = [
    {"name": "Salud", "description": "Estado del servicio."},
    {
        "name": "Instalación",
        "description": "Creación del super admin. Solo disponible mientras no exista ninguno.",
    },
    {
        "name": "Autenticación",
        "description": (
            "Personal: email y contraseña. Estudiantes: tipo de documento (V/E u otros "
            "configurados), número y contraseña."
        ),
    },
    {
        "name": "Configuración",
        "description": "Reglas académicas: periodos, lapsos y escala de notas (super admin).",
    },
    {"name": "Coordinadores", "description": "Cuentas de coordinación (solo super admin)."},
    {"name": "Profesores", "description": "Cuentas de profesores (coordinador)."},
    {"name": "Estudiantes", "description": "Cuentas de estudiantes (coordinador)."},
    {"name": "Semestres", "description": "Gestión de semestres (coordinador)."},
    {"name": "Materias", "description": "Materias de cada semestre (coordinador)."},
    {"name": "Profesor", "description": "Materias del profesor autenticado y carga de notas."},
    {"name": "Estudiante", "description": "Consulta de notas del estudiante autenticado."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    worker = None
    if settings.import_worker_embedded:
        from app.worker import ImportWorker

        worker = ImportWorker(SessionLocal)
        await worker.start()

    app.state.telegram_bot = None
    if settings.telegram_enabled and settings.telegram_bot_embedded:
        from app.telegram.bot import TelegramBot

        try:
            bot = TelegramBot(settings, SessionLocal)
            await bot.start()
            app.state.telegram_bot = bot
        except Exception:
            # Sin Telegram (p. ej. sin internet o token inválido) la API sigue funcionando
            logger.exception("No se pudo iniciar el bot de Telegram")
    try:
        # El servidor MCP montado en /mcp necesita su propio lifespan (sesiones HTTP)
        async with app.state.mcp_app.lifespan(app):
            yield
    finally:
        if app.state.telegram_bot is not None:
            await app.state.telegram_bot.stop()
        if worker is not None:
            await worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        lifespan=lifespan,
        title=settings.app_name,
        description=DESCRIPTION,
        version="0.1.0",
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        debug=settings.debug,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # Para que el front pueda leer el id de la petición y mostrarlo en los errores, y
        # cuántos segundos esperar cuando se supera un límite (429)
        expose_headers=[REQUEST_ID_HEADER, "Retry-After"],
    )
    app.add_middleware(RequestIdMiddleware)

    register_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    # Servidor MCP con las herramientas de notas del estudiante (Streamable HTTP en /mcp)
    app.state.mcp_app = create_mcp(SessionLocal).http_app(path="/")
    app.mount("/mcp", app.state.mcp_app)
    return app


app = create_app()
