"""Worker process entrypoint: python -m tagmonitor.worker"""

import asyncio
import logging
import os
import signal
import socket

from tagmonitor.browser.capturer import PageCapturer
from tagmonitor.config import get_settings
from tagmonitor.db.pool import create_pool
from tagmonitor.storage import ObjectStorage
from tagmonitor.worker.capture_job import record_failed_capture, run_capture_job
from tagmonitor.worker.context import DeadHandler, Handler, WorkerContext
from tagmonitor.worker.runner import Worker

HANDLERS: dict[str, Handler] = {"capture_and_check": run_capture_job}
DEAD_HANDLERS: dict[str, DeadHandler] = {"capture_and_check": record_failed_capture}


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(name)s %(message)s")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    storage = ObjectStorage(settings)
    await storage.ensure_bucket()
    # Each slot may hold a connection for a whole capture (the domain lock), plus a few for
    # claiming, heartbeats, the scheduler and the reaper.
    pool = create_pool(settings.database_url, max_size=settings.worker_concurrency * 2 + 4)
    async with (
        pool,
        PageCapturer(
            policy=settings.ssrf_policy(),
            tracking_stubs=settings.tracking_stubs,
            throttling=settings.capture_throttling,
        ) as capturer,
    ):
        worker = Worker(
            WorkerContext(pool=pool, capturer=capturer, storage=storage, settings=settings),
            worker_id=f"{socket.gethostname()}-{os.getpid()}",
            concurrency=settings.worker_concurrency,
            handlers=HANDLERS,
            dead_handlers=DEAD_HANDLERS,
        )
        await worker.run(stop)


if __name__ == "__main__":
    asyncio.run(main())
