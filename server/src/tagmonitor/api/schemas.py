"""Request and response bodies. Validation lives here so routers stay about behavior."""

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from tagmonitor.checks.tracking_patterns import normalize_ads_id

# Deliberately simple: the real test of an address is whether mail arrives. This only
# catches typos like a missing "@" or domain.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
META_PIXEL_ID = re.compile(r"^\d{5,20}$")
GA4_ID = re.compile(r"^G-[A-Z0-9]{4,20}$")
ADS_ID = re.compile(r"^AW-\d{5,15}$")

type Interval = Literal[60, 360, 1440]


def _email(value: str) -> str:
    value = value.strip()
    if len(value) > 254 or not EMAIL_PATTERN.match(value):
        raise ValueError("enter a valid email address")
    return value


def _ids(values: list[str], pattern: re.Pattern[str], example: str) -> list[str]:
    cleaned = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        if not pattern.match(value):
            raise ValueError(f"{value!r} doesn't look like an ID such as {example}")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


# -- auth -----------------------------------------------------------------------------------


class Credentials(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=200)

    @field_validator("email")
    @classmethod
    def _check_email(cls, value: str) -> str:
        return _email(value)


class UserOut(BaseModel):
    id: UUID
    email: str
    is_admin: bool


# -- sites ------------------------------------------------------------------------------------


class SiteFields(BaseModel):
    """Fields shared by create and update. None on update means "leave unchanged"."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    check_interval_minutes: Interval | None = None
    alert_email: str | None = None
    expected_meta_pixel_ids: list[str] | None = Field(default=None, max_length=10)
    expected_ga4_ids: list[str] | None = Field(default=None, max_length=10)
    expected_google_ads_ids: list[str] | None = Field(default=None, max_length=10)
    ad_headline: str | None = Field(default=None, max_length=200)
    ad_primary_text: str | None = Field(default=None, max_length=2000)
    ad_cta: str | None = Field(default=None, max_length=100)

    @field_validator("alert_email")
    @classmethod
    def _check_alert_email(cls, value: str | None) -> str | None:
        return None if value is None else _email(value)

    @field_validator("expected_meta_pixel_ids")
    @classmethod
    def _check_meta(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else _ids(values, META_PIXEL_ID, "1234567890123456")

    @field_validator("expected_ga4_ids")
    @classmethod
    def _check_ga4(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return _ids([v.strip().upper() for v in values], GA4_ID, "G-ABC123XYZ")

    @field_validator("expected_google_ads_ids")
    @classmethod
    def _check_ads(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        return _ids([normalize_ads_id(v) for v in values if v.strip()], ADS_ID, "AW-123456789")


class SiteCreate(SiteFields):
    url: str = Field(min_length=1, max_length=2000)


class SiteUpdate(SiteFields):
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    paused: bool | None = None


class CheckStatusOut(BaseModel):
    status: str
    code: str
    summary: str


class ActiveJobOut(BaseModel):
    id: int
    status: str


class SiteOut(BaseModel):
    id: UUID
    name: str
    url: str
    check_interval_minutes: int
    paused: bool
    alert_email: str
    expected_meta_pixel_ids: list[str]
    expected_ga4_ids: list[str]
    expected_google_ads_ids: list[str]
    ad_headline: str | None
    ad_primary_text: str | None
    ad_cta: str | None
    created_at: datetime
    next_check_at: datetime
    last_checked_at: datetime | None
    statuses: dict[str, CheckStatusOut]  # latest result per check key
    active_job: ActiveJobOut | None


class ExplanationOut(BaseModel):
    meaning: str
    why_it_matters: str
    how_to_fix: str


class ResultOut(BaseModel):
    check_key: str
    title: str
    status: str
    code: str
    summary: str
    details: dict[str, Any]
    explanation: ExplanationOut | None


class LatestResultOut(ResultOut):
    device: str
    run_id: int
    checked_at: datetime


class SiteDetailOut(SiteOut):
    latest_results: list[LatestResultOut]


class CheckNowOut(BaseModel):
    job_id: int


# -- runs, jobs, trends, alerts ----------------------------------------------------------------


class RunResultBrief(BaseModel):
    check_key: str
    status: str
    code: str


class RunBase(BaseModel):
    id: int
    device: str
    started_at: datetime
    status: str
    error_code: str | None
    http_status: int | None
    final_url: str | None


class RunSummaryOut(RunBase):
    results: list[RunResultBrief]


class RunsPage(BaseModel):
    runs: list[RunSummaryOut]
    next_cursor: str | None


class RunDetailOut(RunBase):
    site_id: UUID
    duration_ms: int | None
    screenshot_url: str | None  # presigned, valid for 10 minutes
    capture_url: str | None
    results: list[ResultOut]


class JobOut(BaseModel):
    id: int
    type: str
    status: str
    reason: str | None
    attempts: int
    last_error: str | None
    created_at: datetime
    finished_at: datetime | None


class TrendPoint(BaseModel):
    at: datetime
    value: float


class TrendOut(BaseModel):
    metric: str
    unit: str
    points: list[TrendPoint]


class AlertOut(BaseModel):
    id: int
    site_id: UUID
    site_name: str
    check_key: str
    kind: str
    subject: str
    created_at: datetime
    sent_at: datetime | None
