from __future__ import annotations

from functools import lru_cache
from typing import Optional

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
        default="Forseti/0.1 (forseti-dev@example.com)",
        validation_alias="EDGAR_USER_AGENT",
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
        default="us-central1", validation_alias="VERTEX_AI_LOCATION"
    )
    GEMINI_MODEL: str = Field(
        default="gemini-2.0-flash-001", validation_alias="GEMINI_MODEL"
    )
    RAG_FAIL_LOUD: bool = Field(default=False, validation_alias="RAG_FAIL_LOUD")

    # Agentic workflow (ADK) settings
    PIPELINE_MODE: str = Field(default="linear", validation_alias="PIPELINE_MODE")
    AGENT_MODEL_TEMPERATURE: float = Field(
        default=0.2, validation_alias="AGENT_MODEL_TEMPERATURE"
    )
    AGENT_TIMEOUT_SECONDS: float = Field(
        default=30.0, validation_alias="AGENT_TIMEOUT_SECONDS"
    )
    AGENT_MAX_RETRIES: int = Field(default=1, validation_alias="AGENT_MAX_RETRIES")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
