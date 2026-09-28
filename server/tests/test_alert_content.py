"""What alert emails say."""

from datetime import UTC, datetime, timedelta

from tagmonitor.alerts.content import AlertFacts, format_duration, render_alert

CHECKED = datetime(2026, 9, 28, 14, 30, tzinfo=UTC)


def facts(**overrides: object) -> AlertFacts:
    values: dict[str, object] = {
        "kind": "failure",
        "site_name": "Bean There Coffee",
        "site_url": "https://beanthere.example.com/spring-sale",
        "check_key": "meta_pixel",
        "check_title": "Meta Pixel",
        "code": "installed_not_firing",
        "summary": "The Meta Pixel loads but never sends a PageView event.",
        "checked_at": CHECKED,
        "last_worked_at": CHECKED - timedelta(days=1, hours=2),
        "failing_since": CHECKED - timedelta(minutes=10),
        "dashboard_url": "http://localhost:3001/sites/abc",
        "confirm_delay": timedelta(minutes=10),
    }
    return AlertFacts(**(values | overrides))  # type: ignore[arg-type]


def test_failure_email() -> None:
    email = render_alert(facts())
    assert email.subject == "Your Meta Pixel stopped firing on beanthere.example.com"
    text = email.text
    assert "Bean There Coffee (https://beanthere.example.com/spring-sale)" in text
    assert "The Meta Pixel loads but never sends a PageView event." in text
    assert "Why it matters: Ad platforms learn who converts" in text
    assert "What to do: Look for a pixel snippet" in text
    assert "Last worked: Sun 27 Sep 2026, 12:30 UTC." in text
    assert "Failing since: Mon 28 Sep 2026, 14:20 UTC." in text
    assert "We re-checked 10 minutes later and it was still broken" in text
    assert "See details: http://localhost:3001/sites/abc" in text


def test_reminder_email() -> None:
    email = render_alert(facts(kind="reminder"))
    assert email.subject == "Still broken: Your Meta Pixel stopped firing on beanthere.example.com"
    assert "We'll keep checking" in email.text


def test_recovery_email() -> None:
    email = render_alert(
        facts(
            kind="recovery",
            code="firing",
            summary="Meta Pixel 123 fired PageView.",
            failing_since=CHECKED - timedelta(days=2, hours=3),
        )
    )
    assert email.subject == "Resolved: Meta Pixel on beanthere.example.com"
    assert "It had been failing for 2 days 3 hours." in email.text
    assert "What we see now: Meta Pixel 123 fired PageView." in email.text


def test_page_down_subject_and_never_worked() -> None:
    email = render_alert(
        facts(
            check_key="page_health",
            check_title="Page health",
            code="navigation_failed",
            last_worked_at=None,
        )
    )
    assert email.subject == "Your landing page is down on beanthere.example.com"
    assert "We haven't seen it working since we started monitoring." in email.text


def test_html_is_escaped() -> None:
    email = render_alert(facts(site_name='<script>alert("x")</script>'))
    assert "<script>" not in email.html
    assert "&lt;script&gt;" in email.html
    assert 'href="http://localhost:3001/sites/abc"' in email.html


def test_format_duration() -> None:
    assert format_duration(timedelta(seconds=20)) == "less than a minute"
    assert format_duration(timedelta(minutes=1)) == "1 minute"
    assert format_duration(timedelta(hours=1, minutes=5)) == "1 hour 5 minutes"
    assert format_duration(timedelta(days=1, minutes=5)) == "1 day"
    assert format_duration(timedelta(days=3, hours=1)) == "3 days 1 hour"
