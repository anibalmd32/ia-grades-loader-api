"""Configuración de la aplicación leída desde variables de entorno / archivo .env."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me"
MIN_JWT_SECRET_LENGTH = 32
# Esquemas de MySQL sin driver asíncrono que se convierten a mysql+asyncmy
SYNC_MYSQL_SCHEMES = {"mysql", "mysql+mysqldb", "mysql+pymysql", "mariadb"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Aplicación ---
    app_name: str = "IA Grades Loader API"
    app_env: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    # DEBUG, INFO, WARNING o ERROR
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    # Orígenes permitidos separados por coma (front Tauri, Vite dev server, etc.)
    cors_origins: str = "http://localhost:1420,tauri://localhost,http://tauri.localhost"

    # --- Base de datos ---
    database_url: str = "mysql+asyncmy://root:@localhost:3306/ia_grades_loader_db"
    database_echo: bool = False

    # --- Autenticación (JWT) ---
    jwt_secret_key: SecretStr = SecretStr(DEFAULT_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    # Longitud de la contraseña generada para profesores y estudiantes nuevos
    generated_password_length: int = 10

    # --- Modelo de IA (cualquier proveedor compatible con la API de OpenAI) ---
    llm_provider: str = "deepseek"
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "deepseek-chat"
    llm_temperature: float = 0.0
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    # Mensajes previos de la conversación que se reenvían al modelo en el chat
    chat_max_history: int = 10
    # Preguntas a la IA por minuto y estudiante (app + Telegram). 0 = sin límite
    chat_rate_limit_per_minute: int = 10

    # --- Telegram ---
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_bot_username: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_secret: SecretStr = SecretStr("")
    telegram_link_code_ttl_minutes: int = 10
    # false para levantar la API sin el bot (p. ej. una segunda instancia: en modo polling
    # solo puede haber un proceso recibiendo mensajes)
    telegram_bot_embedded: bool = True

    # --- Carga de archivos ---
    max_upload_size_mb: int = 10

    # --- Cola de importación de notas (Excel + IA) ---
    # true: el worker corre dentro del proceso de la API. false: hay que lanzarlo aparte
    # con `uv run python -m app.worker` (se pueden lanzar varios).
    import_worker_embedded: bool = True
    # Trabajos procesados en paralelo por cada worker
    import_worker_concurrency: int = 2
    # Cada cuánto busca trabajos nuevos un worker sin nada que hacer (segundos)
    import_worker_poll_seconds: float = 2.0
    # Un trabajo "processing" más viejo que esto se considera huérfano y se reintenta
    import_job_stale_minutes: int = 10
    import_job_max_attempts: int = 3
    # Trabajos pendientes o en proceso que puede tener un profesor a la vez
    import_max_pending_per_teacher: int = 5

    @field_validator("database_url")
    @classmethod
    def _use_async_mysql_driver(cls, url: str) -> str:
        """Los proveedores de MySQL (Render, Railway, Aiven...) dan la URL como mysql://...
        Sin driver, SQLAlchemy usaría MySQLdb, que no está instalado: se fuerza asyncmy."""
        url = url.strip()
        scheme, sep, rest = url.partition("://")
        if sep and scheme.lower() in SYNC_MYSQL_SCHEMES:
            return f"mysql+asyncmy://{rest}"
        return url

    @model_validator(mode="after")
    def _check_jwt_secret(self) -> "Settings":
        secret = self.jwt_secret_key.get_secret_value()
        weak = secret == DEFAULT_JWT_SECRET or len(secret) < MIN_JWT_SECRET_LENGTH
        if weak and self.app_env != "development":
            raise ValueError(
                f"JWT_SECRET_KEY debe tener al menos {MIN_JWT_SECRET_LENGTH} caracteres "
                f"y no ser el valor por defecto cuando APP_ENV={self.app_env}"
            )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key.get_secret_value())

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
