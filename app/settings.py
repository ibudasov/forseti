from functools import lru_cache

try:
    from pydantic import BaseSettings, Field
except Exception:  # pydantic v2 moved BaseSettings into pydantic-settings
    from pydantic import Field
    from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = Field(
        default="postgresql://user:password@postgresql:5432/forseti",
        env="DATABASE_URL",
    )
    POSTGRES_USER: str = Field(default="user", env="POSTGRES_USER")
    POSTGRES_PASSWORD: str = Field(default="password", env="POSTGRES_PASSWORD")
    POSTGRES_DB: str = Field(default="forseti", env="POSTGRES_DB")
    POSTGRES_HOST: str = Field(default="postgresql", env="POSTGRES_HOST")
    POSTGRES_PORT: int = Field(default=5432, env="POSTGRES_PORT")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
