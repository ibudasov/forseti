from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

try:
    from pydantic import BaseSettings, Field
except Exception:  # pydantic v2 moved BaseSettings into pydantic-settings
    from pydantic import Field
    from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8"
    )

    APP_ENV: Literal["local", "cloud"] = "local"
    DB_POOL_SIZE: int = Field(default=3, gt=0)
    DB_MAX_OVERFLOW: int = Field(default=2, ge=0)
    DB_POOL_RECYCLE_SECONDS: int = Field(default=1800, gt=0)
    GOOGLE_CLOUD_PROJECT: Optional[str] = None

    DATABASE_URL: str = Field(
        default="postgresql://user:password@postgresql:5432/forseti",
        validation_alias="DATABASE_URL",
    )
    POSTGRES_USER: str = Field(default="user", validation_alias="POSTGRES_USER")
    POSTGRES_PASSWORD: str = Field(default="password", validation_alias="POSTGRES_PASSWORD")
    POSTGRES_DB: str = Field(default="forseti", validation_alias="POSTGRES_DB")
    POSTGRES_HOST: str = Field(default="postgresql", validation_alias="POSTGRES_HOST")
    POSTGRES_PORT: int = Field(default=5432, validation_alias="POSTGRES_PORT")
    ALPHA_VANTAGE_API_KEY: Optional[str] = Field(
        default=None,
        validation_alias="ALPHA_VANTAGE_API_KEY",
    )
    EDGAR_USER_AGENT: str = Field(
        default="Forseti/0.1 (gazer-flair9o@icloud.com)",
        validation_alias="EDGAR_USER_AGENT",
    )
    INGEST_ALLOW_MISSING_SOURCES: bool = Field(
        default=False,
        validation_alias="INGEST_ALLOW_MISSING_SOURCES",
    )
    INGEST_REPORT_PATH: str = Field(
        default="/tmp/forseti-ingest-report.json",
        validation_alias="INGEST_REPORT_PATH",
    )
    EARNINGS_FALLBACK_ENABLED: bool = Field(
        default=False,
        validation_alias="EARNINGS_FALLBACK_ENABLED",
    )
    INGEST_PRICE_PERIOD: str = Field(default="2y", validation_alias="INGEST_PRICE_PERIOD")
    ACCOUNT_CAPITAL_EUR: float = Field(default=10000.0, validation_alias="ACCOUNT_CAPITAL_EUR")
    RISK_PER_TRADE_PCT: float = Field(default=0.01, validation_alias="RISK_PER_TRADE_PCT")

    # RAG / vector database settings
    EMBEDDING_DIM: int = Field(default=768, validation_alias="EMBEDDING_DIM")
    EMBEDDING_MODEL: str = Field(
        default="text-embedding-004",
        validation_alias="EMBEDDING_MODEL",
    )
    CHUNK_SIZE_TOKENS: int = Field(default=800, validation_alias="CHUNK_SIZE_TOKENS")
    CHUNK_OVERLAP_TOKENS: int = Field(default=100, validation_alias="CHUNK_OVERLAP_TOKENS")
    VERTEX_AI_PROJECT: Optional[str] = Field(
        default=None, validation_alias="VERTEX_AI_PROJECT"
    )
    VERTEX_AI_LOCATION: str = Field(
        default="europe-west4", validation_alias="VERTEX_AI_LOCATION"
    )
    GEMINI_MODEL: str = Field(
        default="gemini-2.0-flash-001", validation_alias="GEMINI_MODEL"
    )
    MODEL_PRICING_USD_PER_MILLION: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        validation_alias="MODEL_PRICING_USD_PER_MILLION",
    )
    RAG_FAIL_LOUD: bool = Field(default=False, validation_alias="RAG_FAIL_LOUD")
    EARNINGS_TRANSCRIPT_URL_TEMPLATE: Optional[str] = Field(
        default=None,
        validation_alias="EARNINGS_TRANSCRIPT_URL_TEMPLATE",
    )

    # Agentic workflow (ADK) settings
    PIPELINE_MODE: str = Field(default="linear", validation_alias="PIPELINE_MODE")
    AGENT_MODEL_TEMPERATURE: float = Field(
        default=0.2, validation_alias="AGENT_MODEL_TEMPERATURE"
    )
    AGENT_TIMEOUT_SECONDS: float = Field(
        default=30.0, validation_alias="AGENT_TIMEOUT_SECONDS"
    )
    AGENT_MAX_RETRIES: int = Field(default=1, validation_alias="AGENT_MAX_RETRIES")
    FUNDAMENTAL_AGENT_MODE: str = Field(
        default="off",
        validation_alias="FUNDAMENTAL_AGENT_MODE",
    )
    TECHNICAL_AGENT_MODE: str = Field(
        default="off",
        validation_alias="TECHNICAL_AGENT_MODE",
    )
    CRITIC_MODE: str = Field(
        default="off",
        validation_alias="CRITIC_MODE",
    )
    SYNTHESIZER_CONTRACT: str = Field(
        default="legacy",
        validation_alias="SYNTHESIZER_CONTRACT",
    )
    ALLOW_PIPELINE_OVERRIDE: bool = Field(
        default=False,
        validation_alias="ALLOW_PIPELINE_OVERRIDE",
    )
    DEBUG_LLM_IO: bool = Field(default=False, validation_alias="DEBUG_LLM_IO")
    DEBUG_LLM_IO_DIR: str = Field(
        default="/tmp/forseti-llm-io",
        validation_alias="DEBUG_LLM_IO_DIR",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def validate_runtime_settings(settings: Optional[Settings] = None) -> None:
    settings = settings or get_settings()
    if settings.APP_ENV != "cloud":
        return

    errors = []
    for name in ("DEBUG_LLM_IO", "ALLOW_PIPELINE_OVERRIDE"):
        if getattr(settings, name):
            errors.append(f"{name} must be false")
    if not settings.RAG_FAIL_LOUD:
        errors.append("RAG_FAIL_LOUD must be true")
    database_url = settings.DATABASE_URL.strip()
    if not database_url or database_url == Settings.model_fields["DATABASE_URL"].default:
        errors.append("DATABASE_URL must be set and must not use the local default")
    for name in ("GOOGLE_CLOUD_PROJECT", "VERTEX_AI_PROJECT"):
        if not (getattr(settings, name) or "").strip():
            errors.append(f"{name} must be set")
    errors.extend(_invalid_agent_settings(settings))
    if errors:
        raise ValueError("Invalid cloud runtime settings: " + "; ".join(errors))


def _invalid_agent_settings(settings: Settings) -> list[str]:
    allowed_values = {
        "PIPELINE_MODE": ("linear", "agentic"),
        "FUNDAMENTAL_AGENT_MODE": ("off", "shadow", "enforced"),
        "TECHNICAL_AGENT_MODE": ("off", "shadow", "enforced"),
        "CRITIC_MODE": ("off", "shadow", "enforced"),
        "SYNTHESIZER_CONTRACT": ("legacy", "typed"),
    }
    return [
        f"{name} must be one of {allowed}"
        for name, allowed in allowed_values.items()
        if getattr(settings, name).strip().lower() not in allowed
    ]
