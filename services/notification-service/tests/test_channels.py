"""Tests for `app.channels` - the delivery transports and the retry policy.

`EmailChannel` is the only transport with a real implementation: it builds an
`EmailMessage` and hands it to `smtplib` in a worker thread. `smtplib.SMTP` is
replaced by a recording fake, so these tests assert the *message that would be
sent* (headers, recipients, plain + HTML alternative bodies) without opening a
socket. `PushChannel` / `SMSChannel` are provider placeholders and must raise
`ChannelUnavailable` so the caller skips instead of retrying forever.

`deliver_with_retry` is where the exponential-backoff policy lives, so its
attempt count, doubling delay, "never retry a config error" rule and terminal
error message are all asserted here.
"""

import asyncio
import smtplib
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.channels import (
    CHANNELS,
    ChannelUnavailable,
    DeliveryError,
    EmailChannel,
    EmailQuotaTracker,
    InAppChannel,
    PushChannel,
    SMSChannel,
    deliver_with_retry,
)
from app.core.settings import settings
from app.models import Notification


def make_notification(title: str = "Title", message: str = "Message") -> Notification:
    return Notification(
        id=uuid4(),
        user_id=uuid4(),
        title=title,
        message=message,
        channel="email",
    )


class RecordingSMTP:
    """Stand-in for `smtplib.SMTP` used as a context manager."""

    instances: list["RecordingSMTP"] = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.sent: list = []
        self.closed = False
        RecordingSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.closed = True
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.logged_in = (username, password)

    def send_message(self, message):
        self.sent.append(message)


@pytest.fixture(autouse=True)
def _reset_recorder():
    RecordingSMTP.instances = []
    yield
    RecordingSMTP.instances = []


@pytest.fixture
def smtp_settings(monkeypatch):
    """Turn SMTP on with every optional feature exercised."""

    def _configure(**overrides):
        values = {
            "SMTP_HOST": "smtp.test",
            "SMTP_PORT": 2525,
            "SMTP_TIMEOUT": 7,
            "SMTP_STARTTLS": True,
            "SMTP_USERNAME": "mailer",
            "SMTP_PASSWORD": "hunter2",
            "SMTP_FROM": "no-reply@wildframe.test",
        }
        values.update(overrides)
        for key, value in values.items():
            monkeypatch.setattr(settings, key, value, raising=False)

    _configure()
    return _configure


@pytest.fixture
def no_smtp(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "", raising=False)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_exposes_the_four_channels():
    assert set(CHANNELS) == {"in-app", "email", "push", "sms"}
    assert isinstance(CHANNELS["in-app"], InAppChannel)
    assert isinstance(CHANNELS["email"], EmailChannel)
    assert isinstance(CHANNELS["push"], PushChannel)
    assert isinstance(CHANNELS["sms"], SMSChannel)


# ---------------------------------------------------------------------------
# InAppChannel
# ---------------------------------------------------------------------------


async def test_in_app_delivery_is_a_noop():
    assert await InAppChannel().deliver(make_notification()) is None
    assert InAppChannel.name == "in-app"


# ---------------------------------------------------------------------------
# PushChannel / SMSChannel
# ---------------------------------------------------------------------------


async def test_push_channel_is_unavailable_by_design():
    with pytest.raises(ChannelUnavailable, match="push provider not configured"):
        await PushChannel().deliver(make_notification())


async def test_sms_channel_is_unavailable_by_design():
    with pytest.raises(ChannelUnavailable, match="sms provider not configured"):
        await SMSChannel().deliver(make_notification())


# ---------------------------------------------------------------------------
# EmailChannel - configuration guards
# ---------------------------------------------------------------------------


async def test_email_without_an_smtp_host_is_unavailable(no_smtp):
    with pytest.raises(ChannelUnavailable, match="SMTP not configured"):
        await EmailChannel().deliver(make_notification(), recipient="a@b.test")


async def test_email_without_a_recipient_is_a_delivery_error(smtp_settings):
    with pytest.raises(DeliveryError, match="no recipient email address"):
        await EmailChannel().deliver(make_notification(), recipient=None)


async def test_email_with_an_unsupported_template_context_is_a_delivery_error(smtp_settings):
    # `render_template` raises KeyError when the context is missing a key the
    # named template needs; the channel must convert that, not leak it.
    with patch(
        "app.channels.render_template",
        side_effect=KeyError("message"),
    ):
        with pytest.raises(DeliveryError, match="unsupported email template context"):
            await EmailChannel().deliver(make_notification(), recipient="a@b.test")


async def test_email_with_a_bad_template_value_is_a_delivery_error(smtp_settings):
    with patch("app.channels.render_template", side_effect=ValueError("bad format spec")):
        with pytest.raises(DeliveryError, match="unsupported email template context"):
            await EmailChannel().deliver(make_notification(), recipient="a@b.test")


# ---------------------------------------------------------------------------
# EmailChannel - message construction
# ---------------------------------------------------------------------------


async def test_email_sends_a_subject_recipient_and_both_bodies(smtp_settings):
    notif = make_notification(title="New episode", message="Season 5 is out")

    with patch("app.channels.smtplib.SMTP", RecordingSMTP):
        await EmailChannel().deliver(notif, recipient="viewer@example.test", template="generic")

    smtp = RecordingSMTP.instances[0]
    assert smtp.host == "smtp.test"
    assert smtp.port == 2525
    assert smtp.timeout == 7
    assert len(smtp.sent) == 1
    message = smtp.sent[0]
    assert message["Subject"] == "New episode"
    assert message["From"] == "no-reply@wildframe.test"
    assert message["To"] == "viewer@example.test"
    assert message.is_multipart()
    assert smtp.closed is True


async def test_email_escapes_html_in_the_html_part(smtp_settings):
    notif = make_notification(title="<b>x</b>", message="<script>alert(1)</script>")

    with patch("app.channels.smtplib.SMTP", RecordingSMTP):
        await EmailChannel().deliver(notif, recipient="viewer@example.test")

    message = RecordingSMTP.instances[0].sent[0]
    html_part = next(p for p in message.walk() if p.get_content_type() == "text/html")
    assert "&lt;script&gt;" in html_part.get_content()
    assert "<script>" not in html_part.get_content()


async def test_email_strips_tags_from_the_plain_text_part(smtp_settings):
    notif = make_notification(title="Hi", message="<b>bold</b> text")

    with patch("app.channels.smtplib.SMTP", RecordingSMTP):
        await EmailChannel().deliver(notif, recipient="viewer@example.test")

    message = RecordingSMTP.instances[0].sent[0]
    text_part = message.get_body(preferencelist=("plain",))
    assert text_part.get_content().strip() == "bold text"


@pytest.mark.parametrize("template", ["generic", "welcome", "new_episode"])
async def test_email_renders_every_supported_template(smtp_settings, template):
    with patch("app.channels.smtplib.SMTP", RecordingSMTP):
        await EmailChannel().deliver(
            make_notification(), recipient="a@b.test", template=template
        )

    assert RecordingSMTP.instances[0].sent


async def test_email_falls_back_to_the_generic_template_for_an_unknown_name(smtp_settings):
    with patch("app.channels.smtplib.SMTP", RecordingSMTP):
        await EmailChannel().deliver(
            make_notification(title="T", message="M"),
            recipient="a@b.test",
            template="does-not-exist",
        )

    assert RecordingSMTP.instances[0].sent[0]["Subject"] == "T"


# ---------------------------------------------------------------------------
# EmailChannel - SMTP session options
# ---------------------------------------------------------------------------


async def test_email_starts_tls_when_configured(smtp_settings):
    with patch("app.channels.smtplib.SMTP", RecordingSMTP):
        await EmailChannel().deliver(make_notification(), recipient="a@b.test")

    assert RecordingSMTP.instances[0].started_tls is True


async def test_email_skips_starttls_when_disabled(smtp_settings):
    smtp_settings(SMTP_STARTTLS=False)

    with patch("app.channels.smtplib.SMTP", RecordingSMTP):
        await EmailChannel().deliver(make_notification(), recipient="a@b.test")

    assert RecordingSMTP.instances[0].started_tls is False


async def test_email_logs_in_when_both_credentials_are_set(smtp_settings):
    with patch("app.channels.smtplib.SMTP", RecordingSMTP):
        await EmailChannel().deliver(make_notification(), recipient="a@b.test")

    assert RecordingSMTP.instances[0].logged_in == ("mailer", "hunter2")


@pytest.mark.parametrize(
    "username,password", [("", "hunter2"), ("mailer", ""), ("", "")]
)
async def test_email_skips_login_when_credentials_are_incomplete(
    smtp_settings, username, password
):
    smtp_settings(SMTP_USERNAME=username, SMTP_PASSWORD=password)

    with patch("app.channels.smtplib.SMTP", RecordingSMTP):
        await EmailChannel().deliver(make_notification(), recipient="a@b.test")

    assert RecordingSMTP.instances[0].logged_in is None


# ---------------------------------------------------------------------------
# EmailChannel - transport failures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        smtplib.SMTPException("550 mailbox unavailable"),
        smtplib.SMTPServerDisconnected("connection closed"),
        OSError("network unreachable"),
    ],
)
async def test_email_converts_smtp_failures_into_a_delivery_error(smtp_settings, exc):
    with patch("app.channels.smtplib.SMTP", RecordingSMTP):
        with patch.object(RecordingSMTP, "send_message", side_effect=exc):
            with pytest.raises(DeliveryError, match="smtp send failed"):
                await EmailChannel().deliver(make_notification(), recipient="a@b.test")


async def test_email_propagates_an_unexpected_smtp_error(smtp_settings):
    """Only SMTPException/OSError are converted; a bug still surfaces."""
    with patch("app.channels.smtplib.SMTP", RecordingSMTP):
        with patch.object(RecordingSMTP, "send_message", side_effect=RuntimeError("bug")):
            with pytest.raises(RuntimeError, match="bug"):
                await EmailChannel().deliver(make_notification(), recipient="a@b.test")


# ---------------------------------------------------------------------------
# deliver_with_retry
# ---------------------------------------------------------------------------


class ScriptedChannel:
    def __init__(self, name="scripted", failures=0, exc=None):
        self.name = name
        self.attempts = 0
        self._failures = failures
        self._exc = exc or DeliveryError("transient")

    async def deliver(self, notification, recipient=None, template="generic"):
        self.attempts += 1
        if self.attempts <= self._failures:
            raise self._exc


async def test_retry_returns_immediately_on_the_first_success(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_RETRY_ATTEMPTS", 3, raising=False)
    channel = ScriptedChannel(failures=0)

    await deliver_with_retry(channel, make_notification())

    assert channel.attempts == 1


async def test_retry_stops_as_soon_as_a_retry_succeeds(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_RETRY_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(settings, "DELIVERY_RETRY_BASE_DELAY", 0.0, raising=False)
    channel = ScriptedChannel(failures=1)

    await deliver_with_retry(channel, make_notification())

    assert channel.attempts == 2


async def test_retry_exhausts_the_configured_attempts_then_raises(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_RETRY_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(settings, "DELIVERY_RETRY_BASE_DELAY", 0.0, raising=False)
    channel = ScriptedChannel(failures=99)

    with pytest.raises(DeliveryError, match="delivery failed after 3 attempts: transient"):
        await deliver_with_retry(channel, make_notification())

    assert channel.attempts == 3


async def test_retry_never_retries_a_channel_unavailable_error(monkeypatch):
    """A missing provider config will not fix itself - retrying is pointless."""
    monkeypatch.setattr(settings, "DELIVERY_RETRY_ATTEMPTS", 5, raising=False)
    monkeypatch.setattr(settings, "DELIVERY_RETRY_BASE_DELAY", 0.0, raising=False)
    channel = ScriptedChannel(failures=99, exc=ChannelUnavailable("no provider"))

    with pytest.raises(ChannelUnavailable, match="no provider"):
        await deliver_with_retry(channel, make_notification())

    assert channel.attempts == 1


async def test_retry_treats_a_zero_or_negative_attempt_count_as_one(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_RETRY_ATTEMPTS", 0, raising=False)
    channel = ScriptedChannel(failures=99)

    with pytest.raises(DeliveryError, match="after 1 attempts"):
        await deliver_with_retry(channel, make_notification())

    assert channel.attempts == 1


async def test_retry_doubles_the_backoff_delay_between_attempts(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_RETRY_ATTEMPTS", 4, raising=False)
    monkeypatch.setattr(settings, "DELIVERY_RETRY_BASE_DELAY", 0.05, raising=False)
    sleeps: list[float] = []

    async def _record(delay):
        sleeps.append(delay)

    channel = ScriptedChannel(failures=99)
    with patch("app.channels.asyncio.sleep", new=_record):
        with pytest.raises(DeliveryError):
            await deliver_with_retry(channel, make_notification())

    # 3 sleeps for 4 attempts: base, doubled, doubled again.
    assert sleeps == [0.05, 0.1, 0.2]


async def test_retry_logs_each_intermediate_attempt(monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_RETRY_ATTEMPTS", 3, raising=False)
    monkeypatch.setattr(settings, "DELIVERY_RETRY_BASE_DELAY", 0.0, raising=False)
    channel = ScriptedChannel(name="sms", failures=99)

    with patch("app.channels.logger") as logger:
        with pytest.raises(DeliveryError):
            await deliver_with_retry(channel, make_notification())

    # Warning for attempts 1 and 2 only; the 3rd is terminal.
    assert logger.warning.call_count == 2
    assert "sms" in logger.warning.call_args.args


# ---------------------------------------------------------------------------
# EmailQuotaTracker
# ---------------------------------------------------------------------------


def test_quota_tracker_treats_a_missing_provider_as_unlimited():
    tracker = EmailQuotaTracker({"sendgrid": 5})

    assert tracker.check_and_increment("smtp") == (True, 0, 0)


def test_quota_tracker_counts_down_to_the_limit():
    tracker = EmailQuotaTracker({"smtp": 2})

    assert tracker.check_and_increment("smtp") == (True, 1, 2)
    assert tracker.check_and_increment("smtp") == (True, 0, 2)
    assert tracker.check_and_increment("smtp") == (False, 0, 2)


def test_quota_tracker_resets_on_a_new_day_bucket():
    tracker = EmailQuotaTracker({"smtp": 1})
    assert tracker.check_and_increment("smtp") == (True, 0, 1)
    assert tracker.check_and_increment("smtp") == (False, 0, 1)

    # Simulate the clock rolling into the next UTC day.
    with patch.object(tracker, "_day_bucket", return_value=2.0):
        assert tracker.check_and_increment("smtp") == (True, 0, 1)


def test_quota_get_status_reports_the_current_bucket():
    tracker = EmailQuotaTracker({"smtp": 3})

    assert tracker.get_status("smtp") == (0, 3)
    tracker.check_and_increment("smtp")
    assert tracker.get_status("smtp") == (1, 3)


def test_quota_get_status_is_zero_for_a_stale_day():
    tracker = EmailQuotaTracker({"smtp": 3})
    tracker.check_and_increment("smtp")

    with patch.object(tracker, "_day_bucket", return_value=99.0):
        assert tracker.get_status("smtp") == (0, 3)


def test_quota_get_status_for_an_unconfigured_provider():
    tracker = EmailQuotaTracker({})

    assert tracker.get_status("smtp") == (0, 0)


def test_quota_tracker_defaults_to_no_limits():
    tracker = EmailQuotaTracker()

    assert tracker._quotas == {}
    assert tracker.check_and_increment("smtp") == (True, 0, 0)


def test_quota_tracker_counts_providers_independently():
    tracker = EmailQuotaTracker({"smtp": 1, "ses": 1})

    assert tracker.check_and_increment("smtp") == (True, 0, 1)
    assert tracker.check_and_increment("smtp") == (False, 0, 1)
    assert tracker.check_and_increment("ses") == (True, 0, 1)


# ---------------------------------------------------------------------------
# Threading: the SMTP send really is off the event loop
# ---------------------------------------------------------------------------


async def test_smtp_send_runs_in_a_worker_thread(smtp_settings):
    """`asyncio.to_thread` keeps the event loop free during the SMTP handshake."""
    notif = make_notification()
    loop_thread = None
    seen: list[int] = []

    class _ThreadCheckingSMTP(RecordingSMTP):
        def send_message(self, message):
            import threading

            seen.append(threading.get_ident())
            super().send_message(message)

    with patch("app.channels.smtplib.SMTP", _ThreadCheckingSMTP):
        loop_thread = __import__("threading").get_ident()
        await EmailChannel().deliver(notif, recipient="a@b.test")

    assert seen and seen[0] != loop_thread
