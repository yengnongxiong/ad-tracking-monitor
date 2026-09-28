"""Table-driven tests for every alert state transition (PRD §13)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from tagmonitor.alerts.state_machine import CheckState, State, transition
from tagmonitor.checks.base import Status

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=1)


def state(name: State, fails: int = 0, alerted_hours_ago: float | None = None) -> CheckState:
    last_alerted = (
        NOW - timedelta(hours=alerted_hours_ago) if alerted_hours_ago is not None else None
    )
    return CheckState(
        state=name,
        consecutive_fails=fails,
        state_entered_at=T0,
        last_alerted_at=last_alerted,
        last_pass_at=T0,
    )


@pytest.mark.parametrize(
    ("current", "observed", "new_state", "alert", "confirm", "flake"),
    [
        # healthy
        (None, "pass", "healthy", None, False, False),  # first ever result
        (None, "fail", "suspect", None, True, False),
        (state("healthy"), "pass", "healthy", None, False, False),
        (state("healthy"), "warn", "healthy", None, False, False),  # warnings never alert
        (state("healthy"), "info", "healthy", None, False, False),
        (state("healthy"), "fail", "suspect", None, True, False),
        # suspect
        (state("suspect", 1), "fail", "alerting", "failure", False, False),
        (state("suspect", 1), "pass", "healthy", None, False, True),  # a flake
        (state("suspect", 1), "warn", "healthy", None, False, True),
        # alerting
        (state("alerting", 2, alerted_hours_ago=1), "fail", "alerting", None, False, False),
        (state("alerting", 2, alerted_hours_ago=23.9), "fail", "alerting", None, False, False),
        (state("alerting", 2, alerted_hours_ago=24), "fail", "alerting", "reminder", False, False),
        (state("alerting", 2, alerted_hours_ago=30), "fail", "alerting", "reminder", False, False),
        (state("alerting", 5, alerted_hours_ago=1), "pass", "healthy", "recovery", False, False),
        (state("alerting", 5, alerted_hours_ago=1), "info", "healthy", "recovery", False, False),
    ],
)
def test_transitions(
    current: CheckState | None,
    observed: Status,
    new_state: State,
    alert: str | None,
    confirm: bool,
    flake: bool,
) -> None:
    change = transition(current, observed, NOW)
    assert change is not None
    assert change.new_state.state == new_state
    assert change.alert == alert
    assert change.enqueue_confirm == confirm
    assert change.flake == flake


@pytest.mark.parametrize(
    "current", [None, state("healthy"), state("suspect", 1), state("alerting", 3)]
)
def test_error_results_change_nothing(current: CheckState | None) -> None:
    """ "Couldn't evaluate" (page down, check crashed) is not evidence either way."""
    assert transition(current, "error", NOW) is None


def test_counters_and_timestamps() -> None:
    suspect = transition(state("healthy"), "fail", NOW)
    assert suspect is not None
    assert suspect.new_state.consecutive_fails == 1
    assert suspect.new_state.state_entered_at == NOW
    assert suspect.new_state.last_pass_at == T0  # remembered for "last worked"

    later = NOW + timedelta(minutes=10)
    alerting = transition(suspect.new_state, "fail", later)
    assert alerting is not None
    assert alerting.new_state.consecutive_fails == 2
    assert alerting.new_state.state_entered_at == later
    assert alerting.new_state.last_alerted_at == later

    still = transition(alerting.new_state, "fail", later + timedelta(hours=1))
    assert still is not None
    assert still.new_state.consecutive_fails == 3
    assert still.new_state.state_entered_at == later  # still the same incident

    recovered = transition(still.new_state, "pass", later + timedelta(hours=2))
    assert recovered is not None
    assert recovered.new_state.consecutive_fails == 0
    assert recovered.new_state.last_pass_at == later + timedelta(hours=2)


def test_a_reminder_resets_the_reminder_clock() -> None:
    alerting = replace(state("alerting", 3), last_alerted_at=NOW - timedelta(hours=25))
    reminder = transition(alerting, "fail", NOW)
    assert reminder is not None and reminder.alert == "reminder"
    assert reminder.new_state.last_alerted_at == NOW
    next_day = transition(reminder.new_state, "fail", NOW + timedelta(hours=2))
    assert next_day is not None and next_day.alert is None
