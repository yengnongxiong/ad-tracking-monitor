"""Settings loaded from environment variables (and .env for local runs).

Every setting has a safe default for local development so a fresh clone works;
production overrides them through the environment.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from tagmonitor.browser.ssrf import SsrfPolicy, normalize_host


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://tagmonitor:tagmonitor@localhost:5433/tagmonitor"
    log_level: str = "INFO"

    # Dev/demo only: exact hostnames the SSRF guard lets through even though they resolve to
    # private addresses (the local fixture sites). Must stay empty in production.
    ssrf_allow_hosts: Annotated[list[str], NoDecode] = []
    # Dev/demo only: answer Meta/Google tag requests with local stubs (browser/tracking_stubs.py).
    tracking_stubs: bool = False
    # Mobile capture throttling profile: "none" or "slow4g" (browser/devices.py).
    capture_throttling: str = "none"

    @field_validator("ssrf_allow_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        """Accept a comma-separated string, e.g. SSRF_ALLOW_HOSTS=fixtures,localhost."""
        if isinstance(value, str):
            return [normalize_host(host) for host in value.split(",") if host.strip()]
        return value

    def ssrf_policy(self) -> SsrfPolicy:
        return SsrfPolicy(allow_hosts=frozenset(self.ssrf_allow_hosts))


@lru_cache
def get_settings() -> Settings:
    """One Settings instance per process; tests can call get_settings.cache_clear()."""
    return Settings()
