"""
Application configuration.

Uses pydantic-settings to read environment variables and .env files into a
strongly-typed Settings model. Settings are cached via lru_cache so we only
parse the env once per process.
"""

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Placeholder secrets shipped in .env.example / docker-compose defaults. The
# app refuses to boot in production if SECRET_KEY is still one of these.
_DEFAULT_SECRETS = {
    "change-me-in-production",
    "change-me-please-use-a-real-random-32-byte-secret",
}


class Settings(BaseSettings):
    """All runtime configuration. Override via env vars or .env file."""

    model_config = SettingsConfigDict(
        # Look for .env in the project root (one level above backend/)
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        case_sensitive=True,
        # Ignore unknown env vars rather than failing - lets us share .env with frontend
        extra="ignore",
    )

    # --- Project metadata ----------------------------------------------------
    PROJECT_NAME: str = "ChatLens"
    API_V1_PREFIX: str = "/api/v1"
    VERSION: str = "0.1.0"

    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # --- Security ------------------------------------------------------------
    SECRET_KEY: str = Field(
        default="change-me-in-production",
        description="Used for signing JWTs and other tokens. MUST be changed in prod.",
    )
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day

    # CORS_ORIGINS may arrive as a comma-separated string ("a,b") OR a JSON
    # array ('["a","b"]') from .env / compose. `NoDecode` disables pydantic-
    # settings' eager JSON decoding of complex (list) env vars so the raw
    # string reaches the validator below — without it, a comma-separated value
    # raises SettingsError at startup before the validator ever runs.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="Allowed CORS origins. Accepts a comma-separated string or a JSON array.",
    )

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_cors(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            s = v.strip()
            # Tolerate a JSON-array form (what the dev compose override + the
            # checked-in .env happen to use) as well as a plain comma list.
            if s.startswith("["):
                try:
                    parsed = json.loads(s)
                except json.JSONDecodeError:
                    pass
                else:
                    if isinstance(parsed, list):
                        return [str(o).strip() for o in parsed if str(o).strip()]
            return [origin.strip() for origin in s.split(",") if origin.strip()]
        return v

    # --- Database ------------------------------------------------------------
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://chatlens:chatlens_dev_password@localhost:5432/chatlens",
        description="Async SQLAlchemy URL. MUST use the +asyncpg driver.",
    )
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_POOL_PRE_PING: bool = True  # detects dropped connections (e.g. db restarts)

    # --- Redis / Celery ------------------------------------------------------
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # --- Uploads -------------------------------------------------------------
    UPLOAD_DIR: str = "/app/uploads"
    MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024  # 50 MiB
    # .csv is included because CSV is a first-class supported platform; omitting
    # it would make the upload route reject a documented export format.
    ALLOWED_UPLOAD_EXTENSIONS: set[str] = {".json", ".txt", ".zip", ".csv"}

    # --- AI / LLM ------------------------------------------------------------
    LLM_PROVIDER: Literal["anthropic", "openai"] = "anthropic"
    LLM_MODEL: str = "claude-sonnet-4-6"
    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""

    # --- Embeddings ----------------------------------------------------------
    EMBEDDING_PROVIDER: Literal["openai", "voyage", "local"] = "openai"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS: int = 1536

    # --- Helpers -------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"

    @model_validator(mode="after")
    def _forbid_default_secret_in_prod(self) -> "Settings":
        """Fail fast in production rather than silently run with a known secret.

        A misconfigured prod deploy (compose falls back to the placeholder
        SECRET_KEY if the env var is unset) would otherwise sign tokens with a
        value that's public in this repo. Refuse to boot instead.
        """
        if self.ENVIRONMENT == "production" and self.SECRET_KEY in _DEFAULT_SECRETS:
            raise ValueError(
                "SECRET_KEY is still the default placeholder. Set a real secret "
                "in production (e.g. `python -c \"import secrets; "
                'print(secrets.token_urlsafe(32))"`).'
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached accessor - prevents re-parsing env on every dependency injection."""
    return Settings()


# Module-level singleton for convenience. Tests can override via
# get_settings.cache_clear() + monkeypatching env vars.
settings = get_settings()
