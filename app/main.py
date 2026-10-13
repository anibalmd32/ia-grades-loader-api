from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings

DESCRIPTION = """
API para que los profesores carguen notas (manualmente o desde Excel con ayuda de IA)
y los estudiantes las consulten de forma tradicional, en lenguaje natural o por Telegram.

* Notas del **1 al 20**, una por **lapso** (3 lapsos por semestre).
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
            "Personal: email y contraseña. Estudiantes: tipo (V/E), cédula y contraseña."
        ),
    },
]


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
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
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
