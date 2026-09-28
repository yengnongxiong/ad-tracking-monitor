"""Worker process entrypoint: python -m tagmonitor.worker

M0 scope: prove the container starts, reaches Postgres, and exits cleanly on SIGTERM.
Claiming and running jobs is added in M4.
"""

import asyncio
import logging
import signal

from tagmonitor.config import get_settings
from tagmonitor.db.pool import create_pool

log = logging.getLogger("tagmonitor.worker")


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with create_pool(settings.database_url, max_size=2) as pool:
        async with pool.connection() as conn:
            await conn.execute("SELECT 1")
        log.info("worker started and connected to Postgres")
        await stop.wait()
    log.info("worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
