"""Regenerate tests/fixtures/captures/*.json from the fixture sites:

    docker compose run --rm tests python -m tests.save_fixture_captures

The saved captures let the analyzer tests run without a browser. Re-run this whenever the
fixture sites or the PageCapture schema change.
"""

import asyncio
from pathlib import Path

from tagmonitor.browser.capturer import PageCapturer
from tests.conftest import TEST_POLICY, TEST_RESOLVER
from tests.fixture_expectations import EXPECTED
from tests.fixture_server import FixtureServer

CAPTURES_DIR = Path(__file__).parent / "fixtures" / "captures"


async def main() -> None:
    server = FixtureServer()
    server.start()
    try:
        async with PageCapturer(
            policy=TEST_POLICY, resolver=TEST_RESOLVER, tracking_stubs=True
        ) as capturer:
            for site in sorted(EXPECTED):
                capture = (await capturer.capture(server.url(site), "mobile")).capture
                path = CAPTURES_DIR / f"{site}.mobile.json"
                path.write_text(capture.model_dump_json(indent=2) + "\n")
                print(f"wrote {path.name}")
    finally:
        server.stop()


if __name__ == "__main__":
    asyncio.run(main())
