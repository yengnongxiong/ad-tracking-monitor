"""`python -m tagmonitor.capture <url>` end to end (against a fixture, not the internet)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tagmonitor.capture import app
from tagmonitor.config import get_settings
from tests.fixture_server import FixtureServer


def test_capture_cli_prints_a_summary_and_writes_json(
    fixture_server: FixtureServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The CLI uses the real resolver, so point it at the fixture server by IP literal and
    # allowlist exactly that address, the same way the dev stack allowlists its fixtures host.
    monkeypatch.setenv("SSRF_ALLOW_HOSTS", "127.0.0.1")
    monkeypatch.setenv("TRACKING_STUBS", "true")
    get_settings.cache_clear()
    out = tmp_path / "capture.json"
    try:
        url = f"http://127.0.0.1:{fixture_server.port}/meta_ok/"
        result = CliRunner().invoke(app, [url, "--out", str(out)])
    finally:
        get_settings.cache_clear()

    assert result.exit_code == 0, result.output
    assert "1 tracking hits" in result.output
    assert "facebook.com/tr/?id=111111111111111&ev=PageView" in result.output
    assert json.loads(out.read_text())["navigation"]["status"] == 200
