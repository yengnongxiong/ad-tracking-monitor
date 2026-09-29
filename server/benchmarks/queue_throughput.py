"""Queue throughput benchmark (PRD M10): 500 site checks with 1, 2, 4 and 8 worker processes.

Each site check is a real capture_and_check job: Chromium loads a fixture page on mobile and
desktop, the capture and screenshot go to MinIO, the checks run, and the results and alert
states are saved in one transaction. The numbers are site checks per minute, and the proof
that no job ran twice: every job has exactly one mobile and one desktop run, and one attempt.

Everything stays on this machine. Each site gets its own hostname (bench-001.test, ...) that a
static resolver maps to the local fixture server, so the per-domain politeness lock doesn't
serialize the run, and nothing can reach the internet. It uses its own `<db>_bench`
database and `tagmonitor-bench` bucket.

    make bench        # writes docs/performance.md and docs/performance/throughput.png
"""

import argparse
import asyncio
import json
import multiprocessing
import os
import platform
import statistics
import time
from datetime import UTC, datetime
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # after matplotlib.use()
from psycopg.conninfo import conninfo_to_dict

from tagmonitor.alerts.senders import ConsoleEmailSender
from tagmonitor.browser.capturer import PageCapturer
from tagmonitor.browser.ssrf import SsrfPolicy
from tagmonitor.config import get_settings
from tagmonitor.db.migrate import migrate
from tagmonitor.db.pool import Pool, create_pool
from tagmonitor.queue.jobs import PRIORITY_SCHEDULED, enqueue
from tagmonitor.storage import ObjectStorage
from tagmonitor.worker.capture_job import run_capture_job
from tagmonitor.worker.context import WorkerContext
from tagmonitor.worker.runner import Worker
from tests.conftest import create_database
from tests.fixture_server import FixtureServer, StaticResolver

# Fixture pages that load in normal time. slow_lcp (a deliberate 5 s image), the redirect
# and error fixtures would measure the fixtures, not the queue.
FIXTURES = [
    "meta_ok",
    "ga4_ok",
    "gtm_ga4_ok",
    "google_ads_ok",
    "no_tags",
    "meta_duplicate_pageview",
    "mobile_overflow",
]
BUCKET = "tagmonitor-bench"
SLOTS_PER_WORKER = 3  # WORKER_CONCURRENCY's default


def hostnames(sites: int) -> list[str]:
    return [f"bench-{i:03d}.test" for i in range(sites)]


# -- one worker process ------------------------------------------------------------------------


def worker_process(
    index: int, database_url: str, hosts: list[str], ready: ProcessEvent, stop: ProcessEvent
) -> None:
    asyncio.run(_worker_main(index, database_url, hosts, ready, stop))


async def _worker_main(
    index: int, database_url: str, hosts: list[str], ready: ProcessEvent, stop: ProcessEvent
) -> None:
    # The confirmation re-check is pushed a day out, so the benchmark measures the 500 checks
    # and nothing else.
    settings = get_settings().model_copy(
        update={"database_url": database_url, "confirm_delay_seconds": 86_400}
    )
    storage = ObjectStorage(settings, bucket=BUCKET)
    stopping = asyncio.Event()
    async with (
        create_pool(database_url, max_size=SLOTS_PER_WORKER * 2 + 4) as pool,
        PageCapturer(
            policy=SsrfPolicy(allow_hosts=frozenset(hosts)),
            resolver=StaticResolver({host: ["127.0.0.1"] for host in hosts}),
            tracking_stubs=True,
        ) as capturer,
    ):
        worker = Worker(
            WorkerContext(pool, capturer, storage, ConsoleEmailSender(), settings),
            worker_id=f"bench-worker-{index}",
            concurrency=SLOTS_PER_WORKER,
            handlers={"capture_and_check": run_capture_job},
            run_scheduler=False,
        )

        async def watch_stop() -> None:
            await asyncio.to_thread(stop.wait)  # the parent's cross-process stop signal
            stopping.set()

        ready.set()
        await asyncio.gather(worker.run(stopping), watch_stop())


# -- one configuration -------------------------------------------------------------------------


def cpu_times() -> tuple[int, int]:
    """(busy, total) jiffies since boot across all CPUs, from /proc/stat. On this machine
    that includes Postgres and MinIO, which run in their own containers on the same host."""
    fields = [int(v) for v in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
    idle = fields[3] + fields[4]  # idle + iowait
    return sum(fields) - idle, sum(fields)


async def reset(pool: Pool) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "TRUNCATE users, sites, jobs, check_runs, check_results, site_check_states, alerts "
            "RESTART IDENTITY CASCADE"
        )


async def create_sites(pool: Pool, server: FixtureServer, hosts: list[str]) -> list[str]:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO users (email, password_hash) VALUES ('bench@example.com', 'x') "
            "RETURNING id"
        )
        user = await cursor.fetchone()
        assert user is not None
        site_ids = []
        for i, host in enumerate(hosts):
            url = f"http://{host}:{server.port}/{FIXTURES[i % len(FIXTURES)]}/"
            cursor = await conn.execute(
                "INSERT INTO sites (user_id, name, url, normalized_url, registrable_domain, "
                "alert_email, next_check_at) "
                "VALUES (%s, %s, %s, %s, %s, 'bench@example.com', now() + interval '1 day') "
                "RETURNING id",
                (user["id"], host, url, url, host),
            )
            row = await cursor.fetchone()
            assert row is not None
            site_ids.append(str(row["id"]))
    return site_ids


async def enqueue_checks(pool: Pool, site_ids: list[str]) -> tuple[list[int], datetime]:
    async with pool.connection() as conn, conn.transaction():
        job_ids = []
        for site_id in site_ids:
            job_id = await enqueue(
                conn,
                "capture_and_check",
                {"site_id": site_id, "reason": "scheduled"},
                priority=PRIORITY_SCHEDULED,
                dedupe_key=f"site:{site_id}",
            )
            assert job_id is not None
            job_ids.append(job_id)
        cursor = await conn.execute("SELECT now() AS t")
        row = await cursor.fetchone()
    assert row is not None
    return job_ids, row["t"]  # jobs become visible to workers when this commits


async def wait_until_done(pool: Pool, job_ids: list[int], timeout_s: float) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT count(*) AS n FROM jobs WHERE id = ANY(%s) AND status <> 'succeeded'",
                (job_ids,),
            )
            row = await cursor.fetchone()
        if row is not None and row["n"] == 0:
            return
        await asyncio.sleep(0.5)
    raise TimeoutError(f"jobs not done after {timeout_s:.0f} s")


async def measure(pool: Pool, job_ids: list[int], started: datetime) -> dict[str, Any]:
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "SELECT id, status, attempts, locked_by, "
            "extract(epoch FROM finished_at - locked_at) AS seconds, finished_at "
            "FROM jobs WHERE id = ANY(%s)",
            (job_ids,),
        )
        jobs = await cursor.fetchall()
        cursor = await conn.execute(
            "SELECT job_id, count(*) AS runs, count(DISTINCT device) AS devices "
            "FROM check_runs WHERE job_id = ANY(%s) GROUP BY job_id",
            (job_ids,),
        )
        runs = {row["job_id"]: row for row in await cursor.fetchall()}
    finished = max(job["finished_at"] for job in jobs)
    elapsed = (finished - started).total_seconds()
    durations = sorted(float(job["seconds"]) for job in jobs)
    per_worker: dict[str, int] = {}
    for job in jobs:
        per_worker[job["locked_by"]] = per_worker.get(job["locked_by"], 0) + 1
    return {
        "jobs": len(jobs),
        "succeeded": sum(job["status"] == "succeeded" for job in jobs),
        "elapsed_s": elapsed,
        "checks_per_min": len(jobs) / elapsed * 60,
        "job_p50_s": statistics.median(durations),
        "job_p95_s": durations[int(0.95 * len(durations)) - 1],
        # Proof of "no job ran twice": one attempt each, exactly one run per device.
        "jobs_with_more_than_one_attempt": sum(job["attempts"] > 1 for job in jobs),
        "duplicate_runs": sum(max(0, r["runs"] - 2) for r in runs.values()),
        "jobs_missing_runs": sum(
            1 for job in jobs if job["id"] not in runs or runs[job["id"]]["devices"] < 2
        ),
        "jobs_per_worker": dict(sorted(per_worker.items())),
    }


async def run_config(
    workers: int, database_url: str, server: FixtureServer, hosts: list[str]
) -> dict[str, Any]:
    async with create_pool(database_url) as pool:
        await reset(pool)
        site_ids = await create_sites(pool, server, hosts)
        spawn = multiprocessing.get_context("spawn")
        stop = spawn.Event()
        readies = [spawn.Event() for _ in range(workers)]
        processes = [
            spawn.Process(target=worker_process, args=(i, database_url, hosts, readies[i], stop))
            for i in range(workers)
        ]
        for process in processes:
            process.start()
        for ready in readies:
            if not ready.wait(timeout=120):
                raise TimeoutError("a worker process didn't start")
        print(f"{workers} worker(s) ready; enqueuing {len(site_ids)} checks", flush=True)
        busy_before, total_before = cpu_times()
        job_ids, started = await enqueue_checks(pool, site_ids)
        try:
            await wait_until_done(pool, job_ids, timeout_s=3600)
            busy_after, total_after = cpu_times()
        finally:
            stop.set()
            for process in processes:
                process.join(timeout=120)
        result = await measure(pool, job_ids, started)
    result |= {
        "workers": workers,
        "slots": workers * SLOTS_PER_WORKER,
        "cpu_busy": (busy_after - busy_before) / max(1, total_after - total_before),
    }
    print(json.dumps(result), flush=True)
    return result


# -- report ------------------------------------------------------------------------------------


def machine() -> dict[str, Any]:
    model = ""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
        mem_kb = next(
            int(line.split()[1])
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemTotal")
        )
    except (OSError, StopIteration):
        mem_kb = 0
    return {
        "cpus": os.cpu_count(),
        # ARM Linux (e.g. Docker on Apple silicon) has no "model name" line.
        "cpu_model": model or platform.machine(),
        "memory_gb": round(mem_kb / 1024 / 1024, 1),
        "python": platform.python_version(),
    }


def chart(results: list[dict[str, Any]], path: Path) -> None:
    workers = [r["workers"] for r in results]
    rates = [r["checks_per_min"] for r in results]
    base = rates[0] / workers[0]
    figure, axes = plt.subplots(figsize=(6, 3.6), dpi=150)
    # A numeric x axis, so linear scaling draws as a straight line.
    axes.plot(
        [0, workers[-1]],
        [0, base * workers[-1]],
        color="#a1a1aa",
        linestyle="--",
        linewidth=1.5,
        label="linear scaling from 1 worker",
    )
    axes.plot(
        workers, rates, color="#3f3f46", linewidth=2, marker="o", markersize=7, label="measured"
    )
    for x, rate in [(workers[0], rates[0]), (workers[-1], rates[-1])]:
        axes.annotate(
            f"{rate:.0f}/min",
            (x, rate),
            xytext=(8, -4),
            textcoords="offset points",
            ha="left",
            va="top",
            fontsize=9,
            color="#3f3f46",
        )
    axes.set_xticks(workers)
    axes.set_xlim(0, workers[-1] * 1.12)
    axes.set_ylim(0, max([*rates, base * workers[-1]]) * 1.1)
    axes.grid(axis="y", color="#e4e4e7", linewidth=0.8)
    axes.set_axisbelow(True)
    axes.set_xlabel(f"Worker processes ({SLOTS_PER_WORKER} capture slots each)")
    axes.set_ylabel("Site checks per minute")
    axes.legend(frameon=False, loc="upper left")
    axes.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    figure.savefig(path)
    plt.close(figure)


def render(results: list[dict[str, Any]], info: dict[str, Any], sites: int) -> str:
    base = results[0]["checks_per_min"]
    rows = [
        f"| {r['workers']} | {r['slots']} | {r['jobs']} | {r['elapsed_s']:.0f} s | "
        f"**{r['checks_per_min']:.1f}** | {r['checks_per_min'] / base:.2f}x "
        f"| {r['job_p50_s']:.1f} s / {r['job_p95_s']:.1f} s | {r['cpu_busy']:.0%} | "
        f"{r['jobs_with_more_than_one_attempt']} / {r['duplicate_runs']} / "
        f"{r['jobs_missing_runs']} |"
        for r in results
    ]
    spread = "; ".join(
        f"{r['workers']} worker(s): " + ", ".join(str(n) for n in r["jobs_per_worker"].values())
        for r in results
    )
    return "\n".join(
        [
            "# Performance: queue throughput",
            "",
            "> Generated by `make bench` (`server/benchmarks/queue_throughput.py`). Every number "
            "below comes from that run; re-run it to reproduce on your machine.",
            "",
            f"- Run: {info['date']} UTC, commit `{info['commit']}`",
            f"- Machine: {info['cpus']} CPUs ({info['cpu_model'] or 'unknown model'}), "
            f"{info['memory_gb']} GB RAM, inside the docker compose `tests` container, with "
            "Postgres and MinIO in their own containers on the same host.",
            f"- Workload: {sites} site checks per configuration. Each is a real "
            "`capture_and_check` job: headless Chromium loads a local fixture page on mobile "
            "and desktop (so 2 page loads, each waiting for 2 s of network quiet), the captures "
            "and screenshots go to MinIO, the checks run, and results and alert states are "
            "saved in one transaction. Each site has its own hostname, so the per-domain lock "
            "never makes a worker wait.",
            "",
            "![Site checks per minute by worker processes](performance/throughput.png)",
            "",
            "| Worker processes | Capture slots | Jobs | Wall time | Site checks / min "
            "| Speed-up | Job time p50 / p95 | CPU busy "
            "| Retried / duplicate runs / missing runs |",
            "|---|---|---|---|---|---|---|---|---|",
            *rows,
            "",
            "Wall time runs from the commit that enqueued the jobs to the last job's finish "
            "(database timestamps). Zero duplicates is checked, not assumed: every job must have "
            "exactly one attempt and exactly one mobile and one desktop run.",
            "",
            f"Jobs per worker process: {spread}.",
            "",
            "## Reading it",
            "",
            *reading(results, info),
            "",
        ]
    )


def reading(results: list[dict[str, Any]], info: dict[str, Any]) -> list[str]:
    """Observations computed from this run's numbers, so the text can't drift from them."""
    first, last = results[0], results[-1]
    lines = [
        f"- From {first['workers']} to {last['workers']} worker processes, throughput went "
        f"from {first['checks_per_min']:.1f} to {last['checks_per_min']:.1f} site checks per "
        f"minute ({last['checks_per_min'] / first['checks_per_min']:.2f}x for "
        f"{last['workers'] / first['workers']:.0f}x the workers), and the CPUs went from "
        f"{first['cpu_busy']:.0%} to {last['cpu_busy']:.0%} busy.",
    ]
    if last["cpu_busy"] >= 0.85:
        lines.append(
            f"- At {last['workers']} workers the {info['cpus']} CPUs were nearly saturated, so "
            "the limit there is the machine (Chromium rendering pages), not the queue: more "
            "workers need more CPUs. Claiming is one `UPDATE ... FOR UPDATE SKIP LOCKED` "
            "statement, so workers never wait on each other's claims."
        )
    else:
        lines.append(
            f"- The CPUs weren't saturated even at {last['workers']} workers "
            f"({last['cpu_busy']:.0%} busy), so the jobs' own waiting (2 s of network quiet "
            "per page load) dominates, and more capture slots per worker should add throughput."
        )
    if last["job_p50_s"] > first["job_p50_s"] * 1.1:
        lines.append(
            f"- A job took {first['job_p50_s']:.1f} s (median) with {first['workers']} worker "
            f"and {last['job_p50_s']:.1f} s with {last['workers']}: when the CPUs are shared by "
            "more concurrent page loads, each one takes longer."
        )
    else:
        lines.append(
            f"- A job took about as long with {last['workers']} workers as with "
            f"{first['workers']} ({last['job_p50_s']:.1f} s vs {first['job_p50_s']:.1f} s "
            "median), so the extra workers didn't slow each other down."
        )
    # If every slot started its next job the instant the last one finished, throughput would
    # be slots / job time. What's missing is the queue's overhead plus the idle tail at the end.
    efficiency = [r["checks_per_min"] / (r["slots"] * 60 / r["job_p50_s"]) for r in results]
    worst = results[efficiency.index(min(efficiency))]
    lines.append(
        f"- Measured throughput was {min(efficiency):.0%} to {max(efficiency):.0%} of the "
        "ceiling of capture slots x 60 / median job time, so claiming jobs, heartbeats and "
        "saving results add little on top of the page loads. Part of the gap is the end of "
        "each run, when slots sit idle while the last jobs finish: with "
        f"{worst['workers']} worker{'s' if worst['workers'] > 1 else ''}, one job's time is "
        f"{worst['job_p50_s'] / worst['elapsed_s']:.0%} of the {worst['elapsed_s']:.0f} s run."
    )
    lines.append(
        "- Real sites add network latency per page: jobs take longer but use little extra "
        "CPU, so on real traffic each worker's slots are busier waiting than computing."
    )
    return lines


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sites", type=int, default=500)
    # 1, 2 and 4 per the PRD; 8 to see where the machine tops out.
    parser.add_argument("--workers", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--out", type=Path, default=Path("../docs"))
    args = parser.parse_args()

    main_db = conninfo_to_dict(get_settings().database_url)["dbname"]
    database_url = await create_database(f"{main_db}_bench")
    await migrate(database_url)
    await ObjectStorage(get_settings(), bucket=BUCKET).ensure_bucket()
    hosts = hostnames(args.sites)
    server = FixtureServer()
    server.start()
    try:
        results = [
            await run_config(workers, database_url, server, hosts) for workers in args.workers
        ]
    finally:
        server.stop()

    info = machine() | {
        "date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M"),
        "commit": os.environ.get("BENCH_COMMIT", "unknown"),
    }
    out_dir = args.out / "performance"
    out_dir.mkdir(parents=True, exist_ok=True)
    chart(results, out_dir / "throughput.png")
    (out_dir / "queue_throughput.json").write_text(
        json.dumps({"machine": info, "sites": args.sites, "results": results}, indent=2) + "\n"
    )
    (args.out / "performance.md").write_text(render(results, info, args.sites))
    print(f"wrote {args.out / 'performance.md'}")


if __name__ == "__main__":
    asyncio.run(main())
