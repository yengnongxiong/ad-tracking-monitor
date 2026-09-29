"""Click through the demo in a real browser: sign up -> add a page -> first check ->
break the pixel -> confirmation -> alert email -> fix -> recovery email.

Run it against the local stack (`make dev`), from the repo root:

    cd server && uv run playwright install chromium   # once
    uv run python ../scripts/demo_walkthrough.py --video ../demo-video

It saves screenshots to docs/img/ and, with --video, a .webm recording of the browser,
which is what the README's demo GIF is made from (see docs/build-log.md for the command).
"""

import argparse
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

REPO = Path(__file__).resolve().parents[1]
BROKEN_FLAG = REPO / "demo" / "state" / "pixel-broken"
SHOTS = REPO / "docs" / "img"
DEMO_URL = "http://beanthere.demo/"


def wait_for_email(mailpit: str, to: str, subject: str, timeout_s: float = 240) -> str:
    """Poll Mailpit until a message to `to` with this subject arrives; return its id."""
    query = urllib.parse.quote(f'to:"{to}" subject:"{subject}"')
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with urllib.request.urlopen(f"{mailpit}/api/v1/search?query={query}") as response:  # noqa: S310
            messages = json.load(response)["messages"]
        if messages:
            return str(messages[0]["ID"])
        time.sleep(2)
    raise TimeoutError(f"no email with subject {subject!r} after {timeout_s:.0f} s")


def check_now(page: Page) -> None:
    """Click "Check now", waiting out a running check or the per-site cooldown like a person."""
    for _ in range(10):
        button = page.get_by_role("button", name="Check now")
        button.wait_for(timeout=180_000)  # shows "Checking…" while a check is running
        button.click()
        cooldown = page.get_by_role("alert").filter(has_text="Try again in")
        try:
            cooldown.wait_for(timeout=2000)
        except PlaywrightTimeout:
            return  # the check started
        seconds = int(cooldown.inner_text().split("Try again in ")[1].split(" ")[0])
        time.sleep(seconds + 1)
    raise RuntimeError("could not start a check")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:3001")
    parser.add_argument("--mailpit", default="http://localhost:8025")
    parser.add_argument("--video", type=Path, help="directory to save a .webm recording in")
    parser.add_argument("--chromium", help="path to a Chromium binary (optional)")
    args = parser.parse_args()
    SHOTS.mkdir(parents=True, exist_ok=True)
    BROKEN_FLAG.unlink(missing_ok=True)  # start with a working pixel

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium)
        context = browser.new_context(
            viewport={"width": 1280, "height": 860},
            record_video_dir=str(args.video) if args.video else None,
            record_video_size={"width": 1280, "height": 860},
        )
        page = context.new_page()
        page.set_default_timeout(180_000)

        # 1. Sign up.
        page.goto(f"{args.base_url}/signup")
        owner = f"owner+{int(time.time())}@beanthere.demo"
        page.get_by_label("Email").fill(owner)
        page.get_by_label("Password").fill("correct horse battery")
        page.get_by_role("button", name="Create account").click()
        page.wait_for_url("**/dashboard")

        # 2. Add the landing page.
        page.get_by_role("link", name="Add a page").click()
        page.get_by_label("Landing page URL").fill(DEMO_URL)
        page.get_by_label("Name").fill("Bean There Coffee")
        page.get_by_role("button", name="Add page and run the first check").click()
        page.wait_for_url("**/sites/*")

        # 3. The first check completes: the pixel is firing.
        page.get_by_text("Meta Pixel 1234567890123456 fired PageView.").wait_for()
        page.wait_for_timeout(1500)  # let screenshots and the chart render
        page.screenshot(path=SHOTS / "site-healthy.png", full_page=True)
        page.goto(f"{args.base_url}/dashboard")
        page.get_by_role("link", name="Bean There Coffee").wait_for()
        page.screenshot(path=SHOTS / "dashboard.png")
        page.go_back()

        # 4. Break the pixel (the page still loads it, but never sends PageView), check now.
        BROKEN_FLAG.parent.mkdir(exist_ok=True)
        BROKEN_FLAG.touch()
        check_now(page)

        # 5-6. The first failure is only "suspect"; the confirmation re-check confirms it and
        #      the alert email goes out.
        failure = wait_for_email(
            args.mailpit, owner, "Your Meta Pixel stopped firing on beanthere.demo"
        )
        page.reload()
        page.get_by_text("The Meta Pixel loads but never sends a PageView event.").wait_for()
        page.wait_for_timeout(1500)
        page.screenshot(path=SHOTS / "site-broken.png", full_page=True)
        mail = context.new_page()
        mail.goto(f"{args.mailpit}/view/{failure}")
        mail.wait_for_timeout(2000)
        mail.screenshot(path=SHOTS / "alert-email.png")
        mail.close()

        # 7-8. Fix the pixel, check now, and the recovery email arrives.
        BROKEN_FLAG.unlink(missing_ok=True)
        check_now(page)
        wait_for_email(args.mailpit, owner, "Resolved: Meta Pixel on beanthere.demo")
        page.goto(f"{args.base_url}/alerts")
        page.get_by_text("Resolved: Meta Pixel on beanthere.demo").wait_for()
        page.screenshot(path=SHOTS / "alerts.png")

        context.close()  # finishes writing the video
        browser.close()
    print("demo complete; screenshots in", SHOTS)


if __name__ == "__main__":
    main()
