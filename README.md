# IA Grades Loader API

API para la carga y consulta de notas universitarias asistida por IA.

- Los profesores cargan las notas (una por lapso), manualmente o desde un Excel que un modelo de
  IA interpreta. Cada lapso puede tener además un **plan de evaluación** que define el profesor
  (exámenes, talleres... con su porcentaje): entonces la nota del lapso se calcula sola a partir
  de las notas de esas evaluaciones (`/api/v1/teacher/subjects/{id}/evaluations`). Las notas
  se descargan en Excel: la materia completa (`.../grades/export`) o un lapso con sus
  evaluaciones (`.../terms/{lapso}/evaluation-grades/export`).
- El super admin configura las reglas académicas: tipo de periodo (semestres, trimestres,
  cuatrimestres o años), número de lapsos y su nombre ("Lapso", "Corte"...), peso de cada lapso,
  escala de notas, nota aprobatoria, decimales y redondeo de la definitiva
  (`GET/PUT /api/v1/settings/academic`). Por defecto: semestres de 3 lapsos que pesan igual,
  notas del 1 al 20 y se aprueba con 10.
- Los estudiantes consultan sus notas de forma tradicional, en lenguaje natural o por Telegram, y
  reciben un aviso cada vez que se les carga o corrige una nota (`/api/v1/student/notifications`;
  en tiempo real por Server-Sent Events en `/api/v1/student/notifications/stream`, y la app lo
  muestra como notificación del sistema).

La especificación completa, la documentación técnica y el tablero de tareas están en Notion:
[Cargador de notas universitarios](https://app.notion.com/p/bluecode32/Cargador-de-notas-universitarios-3f7bc59f43f78088badbffa4180dce10).

## Stack

Python 3.13 · [uv](https://docs.astral.sh/uv/) · FastAPI · FastMCP · SQLAlchemy 2 (async) + asyncmy ·
MySQL · Alembic · PyJWT + pwdlib (argon2) · openpyxl · SDK de OpenAI (cualquier proveedor
compatible, por defecto DeepSeek) · python-telegram-bot.

## Puesta en marcha

```bash
uv sync                          # crea .venv e instala dependencias
cp .env.example .env             # y completa los valores
uv run fastapi dev app/main.py   # servidor con recarga en http://localhost:8000
```

La primera vez, crea el super admin con el instalador (`POST /api/v1/setup`) o carga los datos
de demostración (ver más abajo). Una vez creado, el
instalador deja de estar disponible y las demás cuentas se crean desde dentro de la aplicación:
super admin → coordinadores → profesores y estudiantes. No existe registro público.

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

Comandos útiles:

```bash
uv run pytest            # tests (SQLite en memoria, ~10 s)
uv run pytest --cov      # tests + cobertura (mínimo 90 %)
uv run ruff check .      # lint
uv run ruff format .     # formateo
uv add <paquete>         # nueva dependencia (uv add --dev <paquete> para desarrollo)
```

Tests contra **MySQL real** (misma suite; valida CHECK, DECIMAL, ENUM, bloqueos y la ida y
vuelta de todas las migraciones). Necesita una base de pruebas **distinta** de la de trabajo
cuyo nombre contenga `test`; se vacía en cada ejecución:

```bash
# CREATE DATABASE ia_grades_loader_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
TEST_DATABASE_URL="mysql+asyncmy://root:CLAVE@localhost:3306/ia_grades_loader_test" uv run pytest
# PowerShell: $env:TEST_DATABASE_URL="mysql+asyncmy://..."; uv run pytest
```

El CI de GitHub (`.github/workflows/ci.yml`) ejecuta en cada PR y push a `main`: ruff
(lint y formato), la suite con cobertura en SQLite, la misma suite contra MySQL 8.4 con
`alembic check`, y el build de la imagen Docker.

Migraciones (Alembic, leen `DATABASE_URL` del `.env`):

```bash
uv run alembic upgrade head                          # aplicar migraciones
uv run alembic revision --autogenerate -m "mensaje"  # nueva migración a partir de los modelos
uv run alembic check                                 # verificar que modelos y migraciones coinciden
uv run alembic upgrade head --sql                    # ver el SQL sin ejecutarlo
```

## Servidor MCP (consulta de notas para clientes de IA)

La API expone un servidor [MCP](https://modelcontextprotocol.io) (FastMCP, transporte
Streamable HTTP) en `http://localhost:8000/mcp/` con dos herramientas de solo lectura:
`resumen_de_notas` y `notas_de_materia`. Un cliente MCP se conecta enviando el token de un
**estudiante** (el que devuelve `POST /api/v1/auth/login/student`):

```
Authorization: Bearer <access_token>
```

Las herramientas solo devuelven las notas del dueño del token; tokens de personal,
revocados o caducados se rechazan con 401.

## Estructura

```
app/
  main.py            # creación de la app FastAPI (Swagger, CORS, routers)
  core/              # configuración, seguridad, dependencias comunes
  api/v1/            # routers HTTP versionados
  db/                # engine, sesión y base de SQLAlchemy
  models/            # modelos ORM
  schemas/           # esquemas Pydantic (entrada/salida)
  services/          # lógica de negocio
  ai/                # cliente LLM agnóstico al proveedor y prompts
  telegram/          # bot de Telegram
  mcp/               # servidor FastMCP (herramientas de consulta de notas)
tests/
```

## Variables de entorno

Todas están en [`.env.example`](.env.example). Cómo obtener las que no son obvias:

### Base de datos (`DATABASE_URL`)

Crea la base y un usuario en MySQL 8:

```sql
CREATE DATABASE ia_grades_loader_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'grades'@'%' IDENTIFIED BY 'grades';
GRANT ALL PRIVILEGES ON ia_grades_loader_db.* TO 'grades'@'%';
```

### Secretos (`JWT_SECRET_KEY`, `TELEGRAM_WEBHOOK_SECRET`)

```bash
uv run python -c "import secrets; print(secrets.token_urlsafe(64))"
```

### Modelo de IA (`LLM_*`)

El código usa el SDK de OpenAI apuntando a `LLM_BASE_URL`, así que cualquier proveedor compatible
funciona cambiando solo `LLM_BASE_URL`, `LLM_API_KEY` y `LLM_MODEL`.

Para DeepSeek:

1. Entra en https://platform.deepseek.com y crea una cuenta.
2. Recarga saldo en *Top up* (la API es de prepago).
3. Ve a *API keys* → *Create new API key*, copia la clave (solo se muestra una vez) y ponla en
   `LLM_API_KEY`.
4. Deja `LLM_BASE_URL=https://api.deepseek.com` y elige el modelo en `LLM_MODEL`
   (`deepseek-chat`; revisa la lista vigente en https://api-docs.deepseek.com).

### Telegram (`TELEGRAM_*`)

1. En Telegram, abre un chat con [@BotFather](https://t.me/BotFather).
2. Envía `/newbot`, elige un nombre visible y luego un *username* que termine en `bot`.
3. BotFather responde con el **token** (`123456789:AA...`) → `TELEGRAM_BOT_TOKEN`.
4. El *username* (sin `@`) → `TELEGRAM_BOT_USERNAME`.
5. Opcional: `/setdescription`, `/setuserpic` y `/setcommands` para personalizar el bot.
6. Webhook vs polling:
   - **Desarrollo:** deja `TELEGRAM_WEBHOOK_URL` vacío y el bot funcionará por *polling*.
   - **Producción:** pon la URL pública HTTPS del endpoint de webhook en `TELEGRAM_WEBHOOK_URL`
     y un secreto en `TELEGRAM_WEBHOOK_SECRET`; la API registra el webhook al arrancar.
     Para probar el webhook en local puedes exponer el puerto con `ngrok http 8000`.

**Cómo probar el bot de punta a punta:**

1. Levanta la API (`uv run fastapi dev app/main.py`). En los logs verás si usa polling o webhook.
2. Inicia sesión como estudiante y llama a `POST /api/v1/student/telegram/link`.
3. Abre en el móvil la `url` devuelta (`https://t.me/<bot>?start=<código>`) y pulsa **Iniciar**.
4. Escribe al bot en lenguaje natural («¿cómo voy?»). Comandos: `/nueva`, `/desvincular`, `/ayuda`.

En modo polling solo puede haber **un** proceso con el bot activo; en instancias extra de la
API usa `TELEGRAM_BOT_EMBEDDED=false`.

No hace falta ningún "chat id" en el `.env`: cada estudiante vincula su cuenta desde la app, que
genera un enlace `t.me/<bot>?start=<código>`, y el chat id se guarda en la base de datos.

## Despliegue con Docker

La imagen (`Dockerfile`, multi-etapa con uv) arranca con `APP_ENV=production`, aplica las
migraciones al iniciar (`RUN_MIGRATIONS=false` para omitirlo) y corre como usuario sin
privilegios. `docker-compose.yml` levanta la API y MySQL 8.4:

```bash
# En el .env: MYSQL_PASSWORD y MYSQL_ROOT_PASSWORD (además de las variables de siempre)
docker compose up -d --build
docker compose logs -f api
```

Worker de importaciones en un contenedor aparte (opcional): pon `IMPORT_WORKER_EMBEDDED=false`
y ejecuta `docker compose --profile worker up -d`.

### Reiniciar la base de datos (borrar todos los datos)

Borra todas las tablas y las vuelve a crear vacías con las migraciones; después el
instalador del super admin vuelve a estar disponible. Pide escribir el nombre de la base
para confirmar (`--yes` para no preguntar):

```bash
python -m app.reset_db                          # shell de Render o dentro del contenedor
docker compose exec api python -m app.reset_db  # con docker compose
uv run python -m app.reset_db                   # en local (usa el DATABASE_URL del .env)
```

### Datos de demostración (seeder)

Sobre una base **vacía** (recién creada o reiniciada) carga una universidad de ejemplo completa
para enseñar todas las pantallas al instante: configuración académica, super admin,
2 coordinadores, 6 profesores, 32 estudiantes (V y E, uno desactivado y dos con la contraseña
inicial), dos semestres (uno cerrado y otro en curso), asignaciones, matrículas, ~700 notas con
todas las situaciones (aprobada, reprobada, asegurada, en curso e imposible), importaciones de
Excel (confirmada, pendiente de revisar y fallida) y conversaciones con el asistente. Los datos
son siempre los mismos.

```bash
python -m app.seed_db                           # sobre una base vacía
python -m app.reset_db --yes --seed             # borrar todo y sembrar en un paso
docker compose exec api python -m app.reset_db --yes --seed
uv run python -m app.seed_db --password otra-clave
```

Cuentas (contraseña `demo1234` en todas):

| Rol | Usuario |
|---|---|
| Super admin | `admin@demo.edu` |
| Coordinación | `coordinacion@demo.edu` |
| Profesora con importaciones de Excel | `profesor@demo.edu` |
| Estudiante con todas las situaciones de notas | cédula `V` `12345678` |

Si la base ya tiene datos, el seeder no toca nada y avisa.

### Lista de comprobación para producción

- `APP_ENV=production` en el `.env` del servidor (docker compose usa el valor del `.env`; si
  dice `development`, el contenedor arranca en modo desarrollo).
- `JWT_SECRET_KEY` de al menos 32 caracteres (la API no arranca en producción sin él).
- `MYSQL_USER` distinto de `root` (la imagen de MySQL no lo permite).
- `CORS_ORIGINS` solo con los orígenes reales del front.
- HTTPS delante de la API con un proxy inverso (Nginx, Caddy, Traefik); la imagen ya acepta
  sus cabeceras (`--proxy-headers`).
- Telegram: `TELEGRAM_WEBHOOK_URL` con el dominio público (basta el dominio) y
  `TELEGRAM_WEBHOOK_SECRET`. Si usas polling, solo **una** instancia con el bot activo
  (`TELEGRAM_BOT_EMBEDDED=false` en las demás).
- Más de una réplica de la API: `RUN_MIGRATIONS=false` en todas menos una.
- Copias de seguridad del volumen `mysql-data` (p. ej. `mysqldump` programado).
- Revisa `CHAT_RATE_LIMIT_PER_MINUTE` según el presupuesto del proveedor de IA.
