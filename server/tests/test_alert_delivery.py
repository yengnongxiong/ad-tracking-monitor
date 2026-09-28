"""Delivering alerts from the outbox: the send_alert job and the three email backends."""

import json
from uuid import uuid4

import httpx
import pytest

from tagmonitor.alerts.delivery import run_send_alert
from tagmonitor.alerts.senders import (
    EmailSender,
    OutgoingEmail,
    ResendEmailSender,
    SmtpEmailSender,
)
from tagmonitor.config import Settings, get_settings
from tagmonitor.db.pool import Pool
from tagmonitor.queue.jobs import PRIORITY_SEND_ALERT, Job, claim, enqueue
from tagmonitor.storage import ObjectStorage
from tagmonitor.worker.context import JobError, WorkerContext
from tests import mailpit
from tests.factories import insert_site, insert_user


class RecordingSender(EmailSender):
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[OutgoingEmail] = []
        self.error = error

    async def send(self, email: OutgoingEmail) -> None:
        if self.error:
            raise self.error
        self.sent.append(email)


async def queued_alert(pool: Pool, email: str = "owner@example.com") -> tuple[int, Job]:
    site = await insert_site(pool, await insert_user(pool), alert_email=email)
    async with pool.connection() as conn:
        cursor = await conn.execute(
            "INSERT INTO alerts (site_id, check_key, kind, dedupe_key, subject, body_text, "
            "body_html) VALUES (%s, 'meta_pixel', 'failure', %s, 'Pixel down', 'text', "
            "'<p>html</p>') RETURNING id",
            (site, f"{site}:meta_pixel:failure:t0"),
        )
        row = await cursor.fetchone()
        assert row is not None
        await enqueue(conn, "send_alert", {"alert_id": row["id"]}, priority=PRIORITY_SEND_ALERT)
        (job,) = await claim(conn, "worker-1", 1)
    return int(row["id"]), job


def context(pool: Pool, storage: ObjectStorage, sender: EmailSender) -> WorkerContext:
    return WorkerContext(
        pool=pool,
        capturer=None,
        storage=storage,
        email=sender,
        settings=get_settings(),  # type: ignore[arg-type]
    )


async def alert_row(pool: Pool, alert_id: int) -> dict[str, object]:
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT * FROM alerts WHERE id = %s", (alert_id,))
        row = await cursor.fetchone()
    assert row is not None
    return dict(row)


async def test_sends_and_marks_sent(db: Pool, storage: ObjectStorage) -> None:
    alert_id, job = await queued_alert(db, "maria@shop.example")
    sender = RecordingSender()

    await run_send_alert(job, context(db, storage, sender))

    (email,) = sender.sent
    assert (email.to, email.subject, email.text) == ("maria@shop.example", "Pixel down", "text")
    assert email.idempotency_key.endswith(":meta_pixel:failure:t0")
    row = await alert_row(db, alert_id)
    assert row["sent_at"] is not None and row["attempts"] == 1


async def test_a_failed_send_is_recorded_and_retried(db: Pool, storage: ObjectStorage) -> None:
    alert_id, job = await queued_alert(db)
    sender = RecordingSender(error=ConnectionRefusedError("SMTP server down"))

    with pytest.raises(JobError) as info:
        await run_send_alert(job, context(db, storage, sender))

    assert info.value.transient  # the runner will retry with backoff
    row = await alert_row(db, alert_id)
    assert row["sent_at"] is None  # only set once the provider accepted it
    assert row["attempts"] == 1
    assert "SMTP server down" in str(row["last_error"])


async def test_an_already_sent_alert_is_never_sent_again(db: Pool, storage: ObjectStorage) -> None:
    alert_id, job = await queued_alert(db)
    async with db.connection() as conn:
        await conn.execute("UPDATE alerts SET sent_at = now() WHERE id = %s", (alert_id,))
    sender = RecordingSender()
    await run_send_alert(job, context(db, storage, sender))
    assert sender.sent == []


async def test_smtp_sender_delivers_to_mailpit() -> None:
    recipient = f"{uuid4().hex[:10]}@test.example"
    email = OutgoingEmail(
        to=recipient,
        subject="Your Meta Pixel stopped firing on shop.example",
        text="plain body",
        html="<p>html body</p>",
        idempotency_key=f"site:meta_pixel:failure:{uuid4()}",
    )
    await SmtpEmailSender(get_settings()).send(email)

    (received,) = await mailpit.messages_to(recipient)
    assert received["Subject"] == email.subject
    full = await mailpit.message(received["ID"])
    assert full["Text"].strip() == "plain body"
    assert "<p>html body</p>" in full["HTML"]


async def test_resend_sender_posts_with_an_idempotency_key() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "email_123"})

    settings = Settings(resend_api_key="re_test", email_from="tag-monitor <a@tm.example>")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = ResendEmailSender(settings, client)
    await sender.send(OutgoingEmail("o@shop.example", "Subject", "text", "<p>h</p>", "key-1"))

    (request,) = requests
    assert str(request.url) == "https://api.resend.com/emails"
    assert request.headers["Authorization"] == "Bearer re_test"
    assert request.headers["Idempotency-Key"] == "key-1"
    assert json.loads(request.content) == {
        "from": "tag-monitor <a@tm.example>",
        "to": ["o@shop.example"],
        "subject": "Subject",
        "text": "text",
        "html": "<p>h</p>",
    }


async def test_resend_errors_raise() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(422)))
    sender = ResendEmailSender(Settings(resend_api_key="re_test"), client)
    with pytest.raises(httpx.HTTPStatusError):
        await sender.send(OutgoingEmail("o@shop.example", "S", "t", "h", "k"))
