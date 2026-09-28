"""What job handlers get to work with, and how they report failure."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tagmonitor.browser.capturer import PageCapturer
from tagmonitor.config import Settings
from tagmonitor.db.pool import Pool
from tagmonitor.queue.jobs import Job
from tagmonitor.storage import ObjectStorage


@dataclass
class WorkerContext:
    pool: Pool
    capturer: PageCapturer
    storage: ObjectStorage
    settings: Settings


class JobError(Exception):
    """A handler failure the runner should record. `transient` decides retry vs dead."""

    def __init__(
        self, code: str, message: str, *, transient: bool, device: str | None = None
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.transient = transient
        self.device = device


class DomainBusy(Exception):
    """Another worker is loading a page on the same domain right now; try again shortly."""


# A handler does the job's work, and completes the job itself (so completion can share a
# transaction with the job's writes). Raising means the runner fails or releases the job.
type Handler = Callable[[Job, WorkerContext], Awaitable[None]]
# Called once when a job goes dead, to leave a visible trace (e.g. an error run for the UI).
type DeadHandler = Callable[[Job, WorkerContext, JobError], Awaitable[None]]
