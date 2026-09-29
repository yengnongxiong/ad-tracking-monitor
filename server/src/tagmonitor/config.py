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

    # API (PRD §17).
    session_cookie_secure: bool = False  # True in production (HTTPS)
    # Read the client IP from X-Forwarded-For (last hop). Only when the API is reachable
    # solely through our own proxy (the Next.js server), or clients could spoof their IP.
    trust_proxy_headers: bool = False
    check_now_cooldown_seconds: int = 300

    # LLM message match (PRD §15). Without an API key the check reports that it's turned off.
    anthropic_api_key: str | None = None
    llm_model: str = "claude-haiku-4-5-20251001"
    # The prompt monitoring uses (evals/message_match/prompts/<version>.md). Switch it only
    # after the new version has been measured on the eval set.
    llm_prompt_version: str = "v1"
    # All calls, monitoring and evals together, per UTC day. Cache hits don't count.
    llm_max_calls_per_day: int = 500
    # The API has deprecated sampling settings: models released after Claude Opus 4.6 reject
    # any temperature but 1.0. Set LLM_TEMPERATURE to an empty value to not send one.
    llm_temperature: float | None = 0.0
    llm_max_tokens: int = 1024
    llm_timeout_seconds: float = 30
    # The SDK retries rate limits (429), overload and 5xx errors with backoff this many times.
    llm_max_retries: int = 3

    @field_validator("llm_temperature", "anthropic_api_key", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

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
