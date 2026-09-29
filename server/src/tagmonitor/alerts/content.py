"""What alert emails say. Pure: everything it needs is passed in (PRD §13, "Email content").

Written for a non-technical owner: what we saw, when it last worked, why it matters, what to
do, and a link to the details. Plain text plus a simple HTML version.
"""

import html
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from tagmonitor.alerts.state_machine import AlertKind
from tagmonitor.checks.explanations import explain

# What broke, as the start of a subject line. {title} is the check's name, e.g. "Meta Pixel".
HEADLINES = {
    "installed_not_firing": "Your {title} stopped firing",
    "not_installed": "Your {title} is missing",
    "script_blocked": "Your {title} is blocked from loading",
    "wrong_pixel": "Your {title} has the wrong ID",
    "wrong_id": "Your {title} has the wrong ID",
    "navigation_failed": "Your landing page is down",
    "http_error": "Your landing page shows an error",
    "not_https": "Your landing page isn't secure",
    "slow": "Your landing page is slow on phones",
    "missing_viewport": "Your landing page isn't mobile-friendly",
    "poor_match": "Your landing page doesn't match your ad",
}


@dataclass(frozen=True)
class AlertFacts:
    kind: AlertKind
    site_name: str
    site_url: str
    check_key: str
    check_title: str
    code: str  # the outcome code of the result that triggered the alert
    summary: str  # that result's one-sentence summary
    checked_at: datetime
    last_worked_at: datetime | None
    failing_since: datetime | None
    dashboard_url: str
    confirm_delay: timedelta


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text: str
    html: str


def format_time(moment: datetime) -> str:
    return moment.strftime("%a %d %b %Y, %H:%M UTC")


def format_duration(span: timedelta) -> str:
    minutes = int(span.total_seconds() // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    parts = [(days, "day"), (hours, "hour")] if days else [(hours, "hour"), (minutes, "minute")]
    words = [f"{n} {unit}{'s' if n != 1 else ''}" for n, unit in parts if n]
    return " ".join(words) or "less than a minute"


def headline(facts: AlertFacts) -> str:
    template = HEADLINES.get(facts.code, "{title} needs attention")
    return template.format(title=facts.check_title)


def render_alert(facts: AlertFacts) -> RenderedEmail:
    domain = urlsplit(facts.site_url).hostname or facts.site_url
    if facts.kind == "recovery":
        subject = f"Resolved: {facts.check_title} on {domain}"
        opening = [f"Good news: the {facts.check_title} problem on {facts.site_name} is resolved."]
        if facts.failing_since:
            span = format_duration(facts.checked_at - facts.failing_since)
            opening.append(f"It had been failing for {span}.")
        paragraphs = [
            " ".join(opening),
            f"What we see now: {facts.summary}",
        ]
    else:
        prefix = "Still broken: " if facts.kind == "reminder" else ""
        subject = f"{prefix}{headline(facts)} on {domain}"
        paragraphs = [
            f"We checked {facts.site_name} ({facts.site_url}) on "
            f"{format_time(facts.checked_at)} and found a problem:",
            facts.summary,
        ]
        explanation = explain(facts.check_key, facts.code)
        if explanation:
            paragraphs += [
                f"Why it matters: {explanation.why_it_matters}",
                f"What to do: {explanation.how_to_fix}",
            ]
        history = [
            f"Last worked: {format_time(facts.last_worked_at)}."
            if facts.last_worked_at
            else "We haven't seen it working since we started monitoring.",
        ]
        if facts.failing_since:
            history.append(f"Failing since: {format_time(facts.failing_since)}.")
        paragraphs.append(" ".join(history))
        if facts.kind == "failure":
            delay = format_duration(facts.confirm_delay)
            paragraphs.append(
                f"We re-checked {delay} later and it was still broken, so this isn't a "
                "one-off glitch. We'll email you again when it's fixed."
            )
        else:
            paragraphs.append("We'll keep checking and email you when it's fixed.")

    footer = f"You're getting this because you monitor {domain} with tag-monitor."
    text = "\n\n".join(["Hi,", *paragraphs, f"See details: {facts.dashboard_url}", footer]) + "\n"
    body = "".join(f"<p>{html.escape(p)}</p>" for p in ["Hi,", *paragraphs])
    link = html.escape(facts.dashboard_url, quote=True)
    html_body = (
        '<div style="font-family: system-ui, sans-serif; max-width: 560px; line-height: 1.5">'
        f"{body}"
        f'<p><a href="{link}" style="display: inline-block; padding: 10px 16px; '
        'background: #111827; color: #ffffff; border-radius: 6px; text-decoration: none">'
        "See details</a></p>"
        f'<p style="color: #6b7280; font-size: 12px">{html.escape(footer)}</p>'
        "</div>"
    )
    return RenderedEmail(subject=subject, text=text, html=html_body)
