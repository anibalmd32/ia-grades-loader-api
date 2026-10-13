"""Configuración de la aplicación leída desde variables de entorno / archivo .env."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me"
MIN_JWT_SECRET_LENGTH = 32


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

    # --- Telegram ---
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_bot_username: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_secret: SecretStr = SecretStr("")
    telegram_link_code_ttl_minutes: int = 10

    # --- Carga de archivos ---
    max_upload_size_mb: int = 10

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
