"""Email delivery backends (PRD §13): console for local runs, SMTP (Mailpit in dev), Resend."""

import asyncio
import logging
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

from tagmonitor.config import Settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str
    # Stable per alert. Resend uses it to drop duplicate sends; for SMTP it becomes the
    # Message-ID, which lets mail clients collapse a duplicate if a retry re-sends.
    idempotency_key: str


class EmailSender(ABC):
    @abstractmethod
    async def send(self, email: OutgoingEmail) -> None:
        """Return only once the provider has accepted the message; raise otherwise."""


class ConsoleEmailSender(EmailSender):
    """Prints emails to the log: zero setup for trying things locally."""

    async def send(self, email: OutgoingEmail) -> None:
        log.info("email to %s: %s\n%s", email.to, email.subject, email.text)


class SmtpEmailSender(EmailSender):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, email: OutgoingEmail) -> None:
        message = EmailMessage()
        message["From"] = self.settings.email_from
        message["To"] = email.to
        message["Subject"] = email.subject
        message["Message-ID"] = f"<{email.idempotency_key.replace(':', '.')}@tag-monitor>"
        message.set_content(email.text)
        message.add_alternative(email.html, subtype="html")
        await asyncio.to_thread(self._deliver, message)

    def _deliver(self, message: EmailMessage) -> None:
        settings = self.settings
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username and settings.smtp_password:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)


class ResendEmailSender(EmailSender):
    API_URL = "https://api.resend.com/emails"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        if not settings.resend_api_key:
            raise ValueError("EMAIL_BACKEND=resend needs RESEND_API_KEY")
        self.settings = settings
        self.api_key = settings.resend_api_key
        self.client = client or httpx.AsyncClient(timeout=30)

    async def send(self, email: OutgoingEmail) -> None:
        response = await self.client.post(
            self.API_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Idempotency-Key": email.idempotency_key,
            },
            json={
                "from": self.settings.email_from,
                "to": [email.to],
                "subject": email.subject,
                "text": email.text,
                "html": email.html,
            },
        )
        response.raise_for_status()


def make_email_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "smtp":
        return SmtpEmailSender(settings)
    if settings.email_backend == "resend":
        return ResendEmailSender(settings)
    return ConsoleEmailSender()
