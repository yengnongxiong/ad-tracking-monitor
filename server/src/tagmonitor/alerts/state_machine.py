"""The per-(site, check) alert state machine (PRD §13). Pure: no database, no clock.

    healthy  --fail-->  suspect   (+ enqueue a confirmation check)
    suspect  --fail-->  alerting  (+ failure alert)
    suspect  --ok---->  healthy   (a flake: no alert)
    alerting --fail-->  alerting  (+ reminder if the last alert is 24 h old)
    alerting --ok---->  healthy   (+ recovery alert)

"ok" is any result that isn't a failure: pass, warn (warnings are shown, never alerted) and
info. "error" means the check couldn't be evaluated (page down, check crashed); it says
nothing about the check, so it changes nothing.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from tagmonitor.checks.base import Status

type State = Literal["healthy", "suspect", "alerting"]
type AlertKind = Literal["failure", "reminder", "recovery"]

REMINDER_AFTER = timedelta(hours=24)


@dataclass(frozen=True)
class CheckState:
    state: State
    consecutive_fails: int
    state_entered_at: datetime
    last_alerted_at: datetime | None = None
    last_pass_at: datetime | None = None


@dataclass(frozen=True)
class Transition:
    new_state: CheckState
    alert: AlertKind | None = None
    enqueue_confirm: bool = False
    flake: bool = False


def transition(
    current: CheckState | None,
    observed: Status,
    now: datetime,
    *,
    reminder_after: timedelta = REMINDER_AFTER,
) -> Transition | None:
    """What happens when a check reports `observed`. None means "nothing changes"."""
    if observed == "error":
        return None
    state = current or CheckState(state="healthy", consecutive_fails=0, state_entered_at=now)
    failed = observed == "fail"

    if state.state == "healthy":
        if not failed:
            return Transition(replace(state, consecutive_fails=0, last_pass_at=now))
        return Transition(
            CheckState(
                state="suspect",
                consecutive_fails=1,
                state_entered_at=now,
                last_alerted_at=state.last_alerted_at,
                last_pass_at=state.last_pass_at,
            ),
            enqueue_confirm=True,
        )

    if state.state == "suspect":
        if not failed:
            return Transition(_healthy_again(state, now), flake=True)
        return Transition(
            replace(
                state,
                state="alerting",
                consecutive_fails=state.consecutive_fails + 1,
                state_entered_at=now,
                last_alerted_at=now,
            ),
            alert="failure",
        )

    # alerting
    if not failed:
        return Transition(_healthy_again(state, now), alert="recovery")
    still_failing = replace(state, consecutive_fails=state.consecutive_fails + 1)
    if state.last_alerted_at is None or now - state.last_alerted_at >= reminder_after:
        return Transition(replace(still_failing, last_alerted_at=now), alert="reminder")
    return Transition(still_failing)


def _healthy_again(state: CheckState, now: datetime) -> CheckState:
    return replace(
        state, state="healthy", consecutive_fails=0, state_entered_at=now, last_pass_at=now
    )
