from functools import lru_cache

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
    ALPHA_VANTAGE_API_KEY: str | None = Field(
        default=None,
        validation_alias="ALPHA_VANTAGE_API_KEY",
    )
    EDGAR_USER_AGENT: str = Field(
        default="Forseti/0.1 (forseti-dev@example.com)",
        validation_alias="EDGAR_USER_AGENT",
    )
    INGEST_PRICE_PERIOD: str = Field(default="2y", validation_alias="INGEST_PRICE_PERIOD")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
