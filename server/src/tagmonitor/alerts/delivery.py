"""The send_alert job: deliver one alert row from the outbox."""

from tagmonitor.alerts.senders import OutgoingEmail
from tagmonitor.queue.jobs import Job, complete
from tagmonitor.worker.context import JobError, WorkerContext


async def run_send_alert(job: Job, ctx: WorkerContext) -> None:
    alert_id = job.payload["alert_id"]
    async with ctx.pool.connection() as conn:
        cursor = await conn.execute(
            """
            SELECT a.subject, a.body_text, a.body_html, a.dedupe_key, a.sent_at, s.alert_email
            FROM alerts a JOIN sites s ON s.id = a.site_id
            WHERE a.id = %s
            """,
            (alert_id,),
        )
        alert = await cursor.fetchone()
        if alert is None or alert["sent_at"] is not None:
            await complete(conn, job)  # site deleted, or already delivered: nothing to do
            return

    email = OutgoingEmail(
        to=alert["alert_email"],
        subject=alert["subject"],
        text=alert["body_text"],
        html=alert["body_html"],
        idempotency_key=alert["dedupe_key"],
    )
    try:
        await ctx.email.send(email)
    except Exception as exc:
        async with ctx.pool.connection() as conn:
            await conn.execute(
                "UPDATE alerts SET attempts = attempts + 1, last_error = %s WHERE id = %s",
                (repr(exc)[:2000], alert_id),
            )
        raise JobError("email_failed", repr(exc), transient=True) from exc

    # sent_at is set only now that the provider accepted the message (PRD §13).
    async with ctx.pool.connection() as conn, conn.transaction():
        await conn.execute(
            "UPDATE alerts SET sent_at = now(), attempts = attempts + 1, last_error = NULL "
            "WHERE id = %s",
            (alert_id,),
        )
        await complete(conn, job)
