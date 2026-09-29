"""The worker loop: claim jobs, run them concurrently, keep leases alive, shut down gracefully.

Each worker process runs WORKER_CONCURRENCY job slots on one asyncio loop (captures are
I/O-bound: waiting on the browser and the network). Scale out by running more processes;
they coordinate only through Postgres. Background tickers in every worker send heartbeats,
run the scheduler and run the reaper; advisory locks keep the last two to one worker at a time.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from tagmonitor.queue.jobs import Job, LostLease, claim, fail, heartbeat, reap, release
from tagmonitor.scheduler import schedule_due_sites, schedule_retention
from tagmonitor.worker.context import DeadHandler, DomainBusy, Handler, JobError, WorkerContext

log = logging.getLogger(__name__)

REAPER_LOCK = (7311, 2)  # advisory lock key: one reaper tick at a time across workers


@dataclass(frozen=True)
class Timings:
    poll_s: float = 1.0  # how often an idle worker looks for new jobs
    heartbeat_s: float = 15.0
    scheduler_s: float = 30.0
    reaper_s: float = 60.0
    stale_after_s: float = 120.0  # no heartbeat for this long = the worker died
    domain_busy_delay_s: float = 20.0
    shutdown_grace_s: float = 75.0  # docker compose's stop_grace_period must be longer


class Worker:
    def __init__(
        self,
        ctx: WorkerContext,
        *,
        worker_id: str,
        concurrency: int,
        handlers: dict[str, Handler],
        dead_handlers: dict[str, DeadHandler] | None = None,
        timings: Timings = Timings(),  # noqa: B008 (immutable)
        run_scheduler: bool = True,
    ) -> None:
        self.ctx = ctx
        self.worker_id = worker_id
        self.concurrency = concurrency
        self.handlers = handlers
        self.dead_handlers = dead_handlers or {}
        self.timings = timings
        self.run_scheduler = run_scheduler
        self._in_flight: dict[asyncio.Task[None], Job] = {}
        self._wake = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        tickers = [self._every(self.timings.heartbeat_s, self._heartbeat)]
        tickers.append(self._every(self.timings.reaper_s, self._reap))
        if self.run_scheduler:
            tickers.append(self._every(self.timings.scheduler_s, self._schedule))
        background = [asyncio.create_task(t) for t in tickers]
        stop_watcher = asyncio.create_task(self._wake_on(stop))
        log.info("worker %s started with %d slots", self.worker_id, self.concurrency)
        try:
            while not stop.is_set():
                free = self.concurrency - len(self._in_flight)
                claimed = await self._claim(free) if free > 0 else []
                for job in claimed:
                    task = asyncio.create_task(self._execute(job))
                    self._in_flight[task] = job
                    task.add_done_callback(self._on_done)
                if len(claimed) < free or free == 0:
                    await self._sleep(self.timings.poll_s)  # idle or full: wait for news
        finally:
            await self._drain()
            for task in [*background, stop_watcher]:
                task.cancel()
            await asyncio.gather(*background, stop_watcher, return_exceptions=True)
            log.info("worker %s stopped", self.worker_id)

    # -- loop plumbing ------------------------------------------------------------------------

    def _on_done(self, task: asyncio.Task[None]) -> None:
        self._in_flight.pop(task, None)
        self._wake.set()  # a slot freed up: claim again without waiting for the poll

    async def _wake_on(self, stop: asyncio.Event) -> None:
        await stop.wait()
        self._wake.set()

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), seconds)
        self._wake.clear()

    async def _every(self, seconds: float, action: Callable[[], Awaitable[None]]) -> None:
        while True:
            await asyncio.sleep(seconds)
            try:
                await action()
            except Exception:
                log.exception("background task %s failed", action.__name__)

    async def _claim(self, limit: int) -> list[Job]:
        try:
            async with self.ctx.pool.connection() as conn:
                return await claim(conn, self.worker_id, limit)
        except Exception:
            log.exception("claiming jobs failed")
            return []

    async def _drain(self) -> None:
        """Graceful shutdown: we've stopped claiming. Give in-flight jobs time to finish, then
        cancel the rest; cancelled jobs release themselves so another worker picks them up."""
        if not self._in_flight:
            return
        log.info(
            "waiting up to %.0f s for %d job(s)",
            self.timings.shutdown_grace_s,
            len(self._in_flight),
        )
        _, pending = await asyncio.wait(self._in_flight, timeout=self.timings.shutdown_grace_s)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    # -- running one job ------------------------------------------------------------------------

    async def _execute(self, job: Job) -> None:
        started = time.monotonic()
        handler = self.handlers.get(job.type)
        try:
            if handler is None:
                raise JobError("unknown_job_type", job.type, transient=False)
            await handler(job, self.ctx)
            log.info("job %d %s done in %.1f s", job.id, job.type, time.monotonic() - started)
        except asyncio.CancelledError:
            await self._release(job, delay_s=0, reason="worker shut down before finishing")
            raise
        except DomainBusy as exc:
            await self._release(
                job, delay_s=self.timings.domain_busy_delay_s, reason=f"domain busy: {exc}"
            )
        except LostLease:
            log.warning("job %d was taken over by another worker; dropping our result", job.id)
        except JobError as exc:
            await self._fail(job, exc)
        except Exception as exc:
            # Unknown errors (a database blip, storage hiccup) are worth retrying.
            log.exception("job %d %s crashed", job.id, job.type)
            await self._fail(job, JobError("internal_error", repr(exc), transient=True))

    async def _release(self, job: Job, *, delay_s: float, reason: str) -> None:
        try:
            async with self.ctx.pool.connection() as conn:
                await release(conn, job, delay_s=delay_s, reason=reason)
        except LostLease:
            pass

    async def _fail(self, job: Job, error: JobError) -> None:
        try:
            async with self.ctx.pool.connection() as conn:
                status = await fail(conn, job, str(error), transient=error.transient)
        except LostLease:
            return
        log.warning("job %d %s failed (%s), now %s", job.id, job.type, error, status)
        on_dead = self.dead_handlers.get(job.type)
        if status == "dead" and on_dead is not None:
            await on_dead(job, self.ctx, error)

    # -- background tickers ---------------------------------------------------------------------

    async def _heartbeat(self) -> None:
        jobs = list(self._in_flight.values())
        if jobs:
            async with self.ctx.pool.connection() as conn:
                await heartbeat(conn, jobs)

    async def _schedule(self) -> None:
        async with self.ctx.pool.connection() as conn:
            enqueued = await schedule_due_sites(conn)
            if await schedule_retention(conn):
                log.info("scheduler enqueued the daily retention job")
        if enqueued:
            log.info("scheduler enqueued %d capture job(s)", enqueued)

    async def _reap(self) -> None:
        async with self.ctx.pool.connection() as conn, conn.transaction():
            cursor = await conn.execute(
                "SELECT pg_try_advisory_xact_lock(%s, %s) AS locked", REAPER_LOCK
            )
            row = await cursor.fetchone()
            if row is None or not row["locked"]:
                return
            counts = await reap(conn, self.timings.stale_after_s)
        if counts["queued"] or counts["dead"]:
            log.warning("reaper recovered jobs from dead workers: %s", counts)
