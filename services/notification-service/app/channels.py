"""Notification channel adapters.

Each channel is an independent transport. Failures are isolated per channel:
a failing channel is recorded on the notification row and never drops the
remaining channels. Channels without provider configuration raise
ChannelUnavailable and are skipped, not retried.
"""

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from app.core.settings import settings
from app.models import Notification
from app.templates import render_template

logger = logging.getLogger(__name__)


class ChannelUnavailable(Exception):
    """Channel has no usable provider configuration; skipping is correct."""


class DeliveryError(Exception):
    """Channel delivery failed; retrying may succeed."""


class InAppChannel:
    """In-app delivery is the persisted notification row itself."""

    name = "in-app"

    async def deliver(self, notification: Notification, recipient=None, template: str = "generic"):
        return None


class EmailChannel:
    """Transactional email via SMTP (stdlib smtplib)."""

    name = "email"

    async def deliver(self, notification: Notification, recipient=None, template: str = "generic"):
        if not settings.SMTP_HOST:
            raise ChannelUnavailable("SMTP not configured")
        if not recipient:
            raise ChannelUnavailable("no recipient email address")
        subject, html_body, text_body = render_template(
            template, title=notification.title, message=notification.message
        )
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = settings.SMTP_FROM
        message["To"] = recipient
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        try:
            await asyncio.to_thread(self._send_smtp, message)
        except smtplib.SMTPException as exc:
            raise DeliveryError(f"smtp send failed: {exc}") from exc

    def _send_smtp(self, message: EmailMessage) -> None:
        with smtplib.SMTP(
            settings.SMTP_HOST, settings.SMTP_PORT, timeout=settings.SMTP_TIMEOUT
        ) as server:
            if settings.SMTP_STARTTLS:
                server.starttls()
            if settings.SMTP_USERNAME:
                server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            server.send_message(message)


class PushChannel:
    """Push delivery requires a provider SDK/credentials; none configured."""

    name = "push"

    async def deliver(self, notification: Notification, recipient=None, template: str = "generic"):
        raise ChannelUnavailable("push provider not configured")


class SmsChannel:
    """SMS delivery requires a provider SDK/credentials; none configured."""

    name = "sms"

    async def deliver(self, notification: Notification, recipient=None, template: str = "generic"):
        raise ChannelUnavailable("sms provider not configured")


CHANNELS: dict[str, object] = {
    "in-app": InAppChannel(),
    "email": EmailChannel(),
    "push": PushChannel(),
    "sms": SmsChannel(),
}


async def deliver_with_retry(
    channel,
    notification: Notification,
    recipient=None,
    template: str = "generic",
) -> None:
    """Deliver via a channel, retrying transient failures with exponential backoff.

    ChannelUnavailable (config) is never retried; DeliveryError (transient)
    is retried with exponential backoff up to DELIVERY_RETRY_ATTEMPTS.
    """
    attempts = max(1, settings.DELIVERY_RETRY_ATTEMPTS)
    delay = settings.DELIVERY_RETRY_BASE_DELAY
    last: DeliveryError | None = None
    for attempt in range(attempts):
        try:
            await channel.deliver(notification, recipient=recipient, template=template)
            return
        except ChannelUnavailable:
            raise
        except DeliveryError as exc:
            last = exc
            if attempt < attempts - 1:
                logger.warning(
                    "channel %s delivery attempt %d/%d failed: %s",
                    channel.name,
                    attempt + 1,
                    attempts,
                    exc,
                )
                await asyncio.sleep(delay)
                delay *= 2
    raise DeliveryError(f"delivery failed after {attempts} attempts: {last}")
