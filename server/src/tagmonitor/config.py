"""Settings loaded from environment variables (and .env for local runs).

Every setting has a safe default for local development so a fresh clone works;
production overrides them through the environment.
"""

from functools import lru_cache
from typing import Annotated, Literal

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

    # Object storage (MinIO locally). The public endpoint is what browsers use for
    # presigned screenshot links; it defaults to the internal one.
    s3_endpoint_url: str | None = "http://localhost:9000"
    s3_public_endpoint_url: str | None = None
    s3_access_key: str = "tagmonitor"
    # Local-dev default matching docker-compose.yml's MinIO; production must set S3_SECRET_KEY.
    s3_secret_key: str = "tagmonitor-dev-secret"  # noqa: S105
    s3_region: str = "us-east-1"
    s3_bucket: str = "tagmonitor"

    # Capture slots per worker process (PRD §12).
    worker_concurrency: int = 3

    # Alerts (PRD §13). The confirmation re-check runs this long after a first failure.
    confirm_delay_seconds: int = 600
    # Used for the "See details" link in emails.
    app_base_url: str = "http://localhost:3001"
    email_backend: Literal["console", "smtp", "resend"] = "console"
    email_from: str = "tag-monitor <alerts@tagmonitor.local>"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = False
    resend_api_key: str | None = None

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
