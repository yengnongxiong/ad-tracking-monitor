"""The verdict logic shared by the Meta Pixel, GA4 and Google Ads checks.

All three follow the same question list, in order, so their statuses mean the same thing:
1. Is the tag on the page at all?                      no  -> not_installed
2. Did its script load?                                no  -> script_blocked
3. Are all the ids the owner told us to expect there?  no  -> wrong id
4. Did it send its page-view event?                    no  -> installed_not_firing
5. Did it send that event more than once?              yes -> duplicate_pageview (warn)
                                                            -> firing
"""

from collections import Counter
from dataclasses import dataclass

from tagmonitor.checks.base import Check, CheckResult
from tagmonitor.page_capture import NetworkRequest


def loaded_ok(request: NetworkRequest) -> bool:
    """The browser got a successful response (the script can actually run)."""
    return request.failure is None and request.status is not None and request.status < 400


def was_sent(request: NetworkRequest) -> bool:
    """A tracking hit left the browser. The vendor's response code doesn't matter."""
    return request.failure is None


@dataclass
class TagObservation:
    scripts: list[NetworkRequest]  # script loads for this tag (fbevents.js, gtag.js ...)
    events: Counter[tuple[str, str]]  # (tag id, event name) -> number of hits sent
    seen_ids: set[str]  # ids seen anywhere: hits, per-id config requests, script URLs


def tag_verdict(
    check: Check,
    observation: TagObservation,
    *,
    tag_name: str,  # "Meta Pixel", "GA4"
    expected_ids: list[str],
    page_view_event: str | None,  # None: any event counts as firing (Google Ads)
    wrong_id_code: str,
) -> CheckResult:
    seen = sorted(observation.seen_ids)
    details = {
        "ids": seen,
        "expected_ids": expected_ids,
        "events": [
            {"id": tag_id, "event": event, "count": count}
            for (tag_id, event), count in sorted(observation.events.items())
        ],
    }
    any_event_sent = bool(observation.events)

    if not observation.scripts and not any_event_sent:
        if expected_ids:
            return check.result(
                "fail",
                "not_installed",
                f"No {tag_name} on this page, but we expected {', '.join(expected_ids)}.",
                **details,
            )
        return check.result("info", "not_installed", f"No {tag_name} on this page.", **details)

    if not any(loaded_ok(s) for s in observation.scripts) and not any_event_sent:
        failure = next(
            (s.failure or f"HTTP {s.status}" for s in observation.scripts), "unknown error"
        )
        return check.result(
            "fail",
            "script_blocked",
            f"The {tag_name} script failed to load ({failure}).",
            **details,
        )

    missing = [tag_id for tag_id in expected_ids if tag_id not in observation.seen_ids]
    if missing:
        found = ", ".join(seen) or "none"
        return check.result(
            "fail",
            wrong_id_code,
            f"{tag_name} {', '.join(missing)} is not on this page (found: {found}).",
            missing_ids=missing,
            **details,
        )

    relevant_ids = expected_ids or seen
    if page_view_event is None:
        firing_ids = sorted({tag_id for tag_id, _ in observation.events if tag_id in relevant_ids})
        if not firing_ids:
            return check.result(
                "fail",
                "installed_not_firing",
                f"The {tag_name} tag loads but never sends a hit.",
                **details,
            )
        return check.result(
            "pass", "firing", f"{tag_name} {', '.join(firing_ids)} is firing.", **details
        )

    page_views = {tag_id: observation.events[(tag_id, page_view_event)] for tag_id in relevant_ids}
    if not any(page_views.values()):
        return check.result(
            "fail",
            "installed_not_firing",
            f"The {tag_name} loads but never sends a {page_view_event} event.",
            **details,
        )
    duplicated = {tag_id: n for tag_id, n in page_views.items() if n > 1}
    if duplicated:
        tag_id, count = next(iter(duplicated.items()))
        return check.result(
            "warn",
            "duplicate_pageview",
            f"{tag_name} {tag_id} sends {page_view_event} {count} times per visit, "
            "which inflates your numbers.",
            **details,
        )
    firing = sorted(tag_id for tag_id, n in page_views.items() if n)
    return check.result(
        "pass",
        "firing",
        f"{tag_name} {', '.join(firing)} fired {page_view_event}.",
        **details,
    )
