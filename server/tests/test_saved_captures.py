"""The analyzers against saved real captures of the fixture sites: fast, no browser."""

from pathlib import Path

import pytest

from tagmonitor.checks.registry import run_checks
from tagmonitor.page_capture import CAPTURE_SCHEMA_VERSION, PageCapture
from tests.fixture_expectations import EXPECTED, expected_for, site_config

CAPTURES_DIR = Path(__file__).parent / "fixtures" / "captures"


def load(site: str) -> PageCapture:
    return PageCapture.model_validate_json((CAPTURES_DIR / f"{site}.mobile.json").read_text())


@pytest.mark.parametrize("site", sorted(EXPECTED))
def test_saved_capture_statuses(site: str) -> None:
    capture = load(site)
    assert capture.capture_schema_version == CAPTURE_SCHEMA_VERSION
    results = run_checks(capture, site_config(site, capture.url))
    assert {r.check_key: (r.status, r.code) for r in results} == expected_for(site)


def test_every_fixture_has_a_saved_capture() -> None:
    saved = {path.name.removesuffix(".mobile.json") for path in CAPTURES_DIR.glob("*.json")}
    assert saved == set(EXPECTED), "run: python -m tests.save_fixture_captures"
