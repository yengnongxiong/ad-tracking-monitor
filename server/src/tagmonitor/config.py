"""Settings loaded from environment variables (and .env for local runs).

Every setting has a safe default for local development so a fresh clone works;
production overrides them through the environment.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://tagmonitor:tagmonitor@localhost:5433/tagmonitor"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """One Settings instance per process; tests can call get_settings.cache_clear()."""
    return Settings()
