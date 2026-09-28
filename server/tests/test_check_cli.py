"""`python -m tagmonitor.check <url>` end to end (against a fixture, not the internet)."""

import pytest
from typer.testing import CliRunner

from tagmonitor.check import app
from tagmonitor.config import get_settings
from tests.fixture_server import FixtureServer


def test_check_cli_prints_statuses_and_how_to_fix(
    fixture_server: FixtureServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSRF_ALLOW_HOSTS", "127.0.0.1")
    monkeypatch.setenv("TRACKING_STUBS", "true")
    get_settings.cache_clear()
    try:
        url = f"http://127.0.0.1:{fixture_server.port}/meta_wrong_id/"
        result = CliRunner().invoke(app, [url, "--meta-pixel-id", "111111111111111"])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 0, result.output
    assert "Meta Pixel 111111111111111 is not on this page" in result.output
    assert "How to fix it: In Meta Events Manager" in result.output
