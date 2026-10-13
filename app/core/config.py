"""Configuración de la aplicación leída desde variables de entorno / archivo .env."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    database_url: str = "mysql+asyncmy://grades:grades@localhost:3306/ia_grades_loader"
    database_echo: bool = False

    # --- Autenticación (JWT) ---
    jwt_secret_key: SecretStr = SecretStr("change-me")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    # Longitud de la contraseña generada para estudiantes nuevos
    generated_password_length: int = 10

    # Coordinador inicial (se crea al ejecutar el seed si no existe)
    first_coordinator_cedula: str = ""
    first_coordinator_password: SecretStr = SecretStr("")
    first_coordinator_name: str = "Coordinador"

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
