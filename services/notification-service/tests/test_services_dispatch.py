"""Tests for `NotificationService._dispatch` and `retry_delivery`.

These two functions carry the whole delivery contract:

* `_dispatch` must isolate failures **per channel** (one dead transport never
  drops the others), skip unconfigured channels instead of retrying them, and
  enforce the daily email quota;
* `retry_delivery` must re-dispatch **only** the channels that previously
  failed, must never silently retry a failed *email* (the recipient and template
  are not persisted), and must not duplicate the notification row.

The channel transports in `app.channels.CHANNELS` are replaced with scripted
fakes, so nothing touches SMTP, Twilio or a broker.
"""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.channels import ChannelUnavailable, DeliveryError, EmailQuotaTracker
from app.models import Notification, NotificationPreference, utcnow_naive
from app.repositories import NotificationRepository
from app.services import NotificationService


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeChannel:
    """A channel that records its calls and replays a scripted outcome."""

    def __init__(self, name: str, behaviour=None):
        self.name = name
        self.calls: list[dict] = []
        self._behaviour = behaviour

    async def deliver(self, notification, recipient=None, template="generic"):
        self.calls.append(
            {"notification_id": notification.id, "recipient": recipient, "template": template}
        )
        if self._behaviour is None:
            return None
        raise self._behaviour


def make_notification(delivery_status: str = "pending", errors: dict | None = None):
    return Notification(
        id=uuid4(),
        user_id=uuid4(),
        title="Title",
        message="Message",
        channel="in-app",
        delivery_status=delivery_status,
        delivery_errors=json.dumps(errors) if errors is not None else None,
    )


from app.repositories import NotificationRepository
def build_repo(notification=None, preference=None) -> MagicMock:
    """A repository double with the *real* parse_delivery_errors attached."""
    repo = MagicMock()
    # Column defaults only apply on INSERT, so an unsaved preference object has
    # `None` for every flag; the enabled-everything baseline is set explicitly.
    resolved = (
        preference
        if preference is not None
        else NotificationPreference(
            user_id=uuid4(),
            in_app_enabled=True,
            email_enabled=True,
            push_enabled=True,
            sms_enabled=True,
        )
    )
    repo.get_preference = AsyncMock(return_value=resolved)
    repo.get_by_id = AsyncMock(return_value=notification)
    repo.session = MagicMock()
    repo.session.commit = AsyncMock()
    # The real static method, so parse_delivery_errors behaviour is genuine.
    repo.parse_delivery_errors = NotificationRepository.parse_delivery_errors
    return repo


@pytest.fixture
def single_attempt(monkeypatch):
    """Collapse `deliver_with_retry` to one attempt so backoff is not re-tested here.

    The exponential-backoff schedule itself is covered in test_channels.py.
    """
    monkeypatch.setattr("app.channels.settings.DELIVERY_RETRY_ATTEMPTS", 1, raising=False)
    monkeypatch.setattr("app.channels.settings.DELIVERY_RETRY_BASE_DELAY", 0.0, raising=False)


@pytest.fixture
def patched_channels(monkeypatch, single_attempt):
    """Replace `app.services.CHANNELS` with fakes; returns the registry."""
    registry: dict[str, FakeChannel] = {}
    for name in ("in-app", "email", "push", "sms"):
        registry[name] = FakeChannel(name)
    monkeypatch.setattr("app.services.CHANNELS", registry)
    return registry


def service_for(repo, quota: EmailQuotaTracker | None = None) -> NotificationService:
    return NotificationService(repo, quota_tracker=quota)


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------


async def test_dispatch_marks_every_channel_sent(patched_channels):
    notif = make_notification()

    outcomes = await service_for(build_repo())._dispatch(
        notif, ["in-app", "push", "sms"], email_address=None, template="generic"
    )

    assert outcomes == {"in-app": "sent", "push": "sent", "sms": "sent"}


async def test_dispatch_reports_an_unknown_channel_without_raising(patched_channels):
    notif = make_notification()

    outcomes = await service_for(build_repo())._dispatch(
        notif, ["carrier-pigeon"], email_address=None, template="generic"
    )

    assert outcomes == {"carrier-pigeon": "failed: unknown channel"}


async def test_dispatch_isolates_a_failing_channel_from_the_others(patched_channels):
    patched_channels["sms"]._behaviour = DeliveryError("gateway 503")
    notif = make_notification()

    outcomes = await service_for(build_repo())._dispatch(
        notif, ["in-app", "sms", "push"], email_address=None, template="generic"
    )

    # sms failed, but in-app and push were still delivered.
    # `deliver_with_retry` wraps the final DeliveryError, hence the nested text.
    assert outcomes == {
        "in-app": "sent",
        "sms": "failed: delivery failed after 1 attempts: gateway 503",
        "push": "sent",
    }
    assert patched_channels["in-app"].calls
    assert patched_channels["push"].calls


async def test_dispatch_skips_an_unconfigured_channel_without_retrying(patched_channels):
    patched_channels["push"]._behaviour = ChannelUnavailable("push provider not configured")
    notif = make_notification()

    outcomes = await service_for(build_repo())._dispatch(
        notif, ["push"], email_address=None, template="generic"
    )

    assert outcomes == {"push": "skipped: push provider not configured"}
    # A config problem is not retried - exactly one attempt.
    assert len(patched_channels["push"].calls) == 1


async def test_dispatch_passes_the_recipient_only_to_the_email_channel(patched_channels):
    notif = make_notification()

    await service_for(build_repo())._dispatch(
        notif, ["in-app", "email"], email_address="user@example.test", template="welcome"
    )

    assert patched_channels["email"].calls[0]["recipient"] == "user@example.test"
    assert patched_channels["email"].calls[0]["template"] == "welcome"
    assert patched_channels["in-app"].calls[0]["recipient"] is None
    assert patched_channels["in-app"].calls[0]["template"] == "welcome"


async def test_dispatch_allows_email_within_the_quota(patched_channels):
    quota = EmailQuotaTracker({"smtp": 2})

    outcomes = await service_for(build_repo(), quota)._dispatch(
        make_notification(), ["email"], email_address="a@b.test", template="generic"
    )

    assert outcomes == {"email": "sent"}
    assert quota.get_status("smtp") == (1, 2)


async def test_dispatch_blocks_email_once_the_daily_quota_is_exhausted(patched_channels):
    quota = EmailQuotaTracker({"smtp": 1})
    service = service_for(build_repo(), quota)

    first = await service._dispatch(
        make_notification(), ["email"], email_address="a@b.test", template="generic"
    )
    second = await service._dispatch(
        make_notification(), ["email"], email_address="a@b.test", template="generic"
    )

    assert first == {"email": "sent"}
    assert second == {"email": "failed: daily quota exceeded (1)"}
    # The blocked attempt must not have touched the transport.
    assert len(patched_channels["email"].calls) == 1


async def test_dispatch_treats_an_unconfigured_provider_as_unlimited(patched_channels):
    quota = EmailQuotaTracker({})  # no "smtp" entry

    outcomes = await service_for(build_repo(), quota)._dispatch(
        make_notification(), ["email"], email_address="a@b.test", template="generic"
    )

    assert outcomes == {"email": "sent"}


async def test_dispatch_does_not_consume_quota_for_non_email_channels(patched_channels):
    quota = EmailQuotaTracker({"smtp": 1})

    await service_for(build_repo(), quota)._dispatch(
        make_notification(), ["in-app", "push"], email_address=None, template="generic"
    )

    assert quota.get_status("smtp") == (0, 1)


async def test_dispatch_mixes_quota_blocks_and_real_failures(patched_channels):
    quota = EmailQuotaTracker({"smtp": 1})
    patched_channels["sms"]._behaviour = DeliveryError("no credit")
    service = service_for(build_repo(), quota)

    outcomes = await service._dispatch(
        make_notification(),
        ["in-app", "email", "sms"],
        email_address="a@b.test",
        template="generic",
    )
    # Drain the quota, then dispatch again.
    outcomes2 = await service._dispatch(
        make_notification(),
        ["email", "sms"],
        email_address="a@b.test",
        template="generic",
    )

    assert outcomes == {
        "in-app": "sent",
        "email": "sent",
        "sms": "failed: delivery failed after 1 attempts: no credit",
    }
    assert outcomes2 == {
        "email": "failed: daily quota exceeded (1)",
        "sms": "failed: delivery failed after 1 attempts: no credit",
    }


# ---------------------------------------------------------------------------
# _record_outcomes (the state machine _dispatch feeds)
# ---------------------------------------------------------------------------


def test_record_outcomes_marks_fully_sent():
    notif = make_notification()

    NotificationService._record_outcomes(notif, {"in-app": "sent", "push": "sent"})

    assert notif.delivery_status == "sent"
    assert notif.delivered_at is not None
    assert json.loads(notif.delivery_errors) == {"in-app": "sent", "push": "sent"}


def test_record_outcomes_marks_partial_when_some_fail():
    notif = make_notification()

    NotificationService._record_outcomes(notif, {"in-app": "sent", "sms": "failed: nope"})

    assert notif.delivery_status == "partial"
    assert notif.delivered_at is not None


def test_record_outcomes_marks_failed_when_nothing_was_sent():
    notif = make_notification()

    NotificationService._record_outcomes(notif, {"sms": "failed: nope"})

    assert notif.delivery_status == "failed"
    assert notif.delivered_at is None


def test_record_outcomes_marks_skipped_when_everything_was_skipped():
    notif = make_notification()

    NotificationService._record_outcomes(notif, {"push": "skipped: not configured"})

    assert notif.delivery_status == "skipped"
    assert notif.delivered_at is None


def test_record_outcomes_does_not_overwrite_an_existing_delivered_at():
    notif = make_notification()
    notif.delivered_at = utcnow_naive()
    original = notif.delivered_at

    NotificationService._record_outcomes(notif, {"in-app": "sent"})

    assert notif.delivered_at == original


# ---------------------------------------------------------------------------
# retry_delivery
# ---------------------------------------------------------------------------


async def test_retry_returns_none_for_an_unknown_notification(patched_channels):
    repo = build_repo(notification=None)

    assert await service_for(repo).retry_delivery(uuid4(), uuid4()) is None
    repo.session.commit.assert_not_awaited()


async def test_retry_is_a_noop_for_a_notification_that_already_succeeded(
    patched_channels,
):
    notif = make_notification(delivery_status="sent")
    repo = build_repo(notification=notif)

    result = await service_for(repo).retry_delivery(notif.id, notif.user_id)

    assert result == {"status": "sent"}
    # Nothing re-dispatched and nothing committed.
    assert not patched_channels["in-app"].calls
    repo.session.commit.assert_not_awaited()


async def test_retry_is_a_noop_for_a_pending_notification(patched_channels):
    notif = make_notification(delivery_status="pending")
    repo = build_repo(notification=notif)

    assert await service_for(repo).retry_delivery(notif.id, notif.user_id) == {
        "status": "pending"
    }


async def test_retry_re_dispatches_only_the_failed_channels(patched_channels):
    notif = make_notification(
        delivery_status="partial",
        errors={"in-app": "sent", "sms": "failed: gateway 503"},
    )
    repo = build_repo(notification=notif)

    result = await service_for(repo).retry_delivery(notif.id, notif.user_id)

    assert patched_channels["sms"].calls
    assert not patched_channels["in-app"].calls  # already sent - must not resend
    assert result == {"status": "sent"}
    assert json.loads(notif.delivery_errors) == {"in-app": "sent", "sms": "sent"}
    repo.session.commit.assert_awaited_once()


async def test_retry_skips_failed_channels_that_are_now_preference_disabled(
    patched_channels,
):
    preference = NotificationPreference(user_id=uuid4(), sms_enabled=False)
    notif = make_notification(
        delivery_status="failed",
        errors={"in-app": "sent", "sms": "failed: gateway 503"},
    )
    repo = build_repo(notification=notif, preference=preference)

    result = await service_for(repo).retry_delivery(notif.id, notif.user_id)

    assert not patched_channels["sms"].calls
    assert json.loads(notif.delivery_errors)["sms"] == "skipped: preference disabled"
    assert result == {"status": "partial"}


async def test_retry_never_redelivers_email(patched_channels):
    """Recipient/template are not persisted - a blind resend would be wrong."""
    notif = make_notification(
        delivery_status="failed", errors={"email": "failed: smtp send failed"}
    )
    repo = build_repo(notification=notif)

    result = await service_for(repo).retry_delivery(notif.id, notif.user_id)

    assert not patched_channels["email"].calls
    # The original failure is preserved rather than being converted to a skip.
    assert json.loads(notif.delivery_errors) == {"email": "failed: smtp send failed"}
    assert result == {"status": "failed"}


async def test_retry_does_nothing_when_no_channel_is_in_a_failed_state(patched_channels):
    notif = make_notification(
        delivery_status="failed", errors={"in-app": "sent", "sms": "skipped: not configured"}
    )
    repo = build_repo(notification=notif)

    result = await service_for(repo).retry_delivery(notif.id, notif.user_id)

    assert not patched_channels["sms"].calls
    assert result == {"status": "failed"}
    repo.session.commit.assert_not_awaited()


async def test_retry_handles_a_notification_with_no_stored_outcomes(patched_channels):
    notif = make_notification(delivery_status="failed", errors=None)
    repo = build_repo(notification=notif)

    result = await service_for(repo).retry_delivery(notif.id, notif.user_id)

    assert result == {"status": "failed"}
    repo.session.commit.assert_not_awaited()


async def test_retry_keeps_failing_the_notification_when_the_retry_fails(patched_channels):
    patched_channels["sms"]._behaviour = DeliveryError("still down")
    notif = make_notification(
        delivery_status="failed", errors={"sms": "failed: gateway 503"}
    )
    repo = build_repo(notification=notif)

    result = await service_for(repo).retry_delivery(notif.id, notif.user_id)

    assert result == {"status": "failed"}
    assert (
        json.loads(notif.delivery_errors)["sms"]
        == "failed: delivery failed after 1 attempts: still down"
    )


async def test_retry_never_creates_a_second_notification_row(patched_channels):
    """Idempotency: retry re-dispatches, it does not re-create."""
    notif = make_notification(
        delivery_status="failed", errors={"sms": "failed: gateway 503"}
    )
    repo = build_repo(notification=notif)
    repo.create = AsyncMock()

    await service_for(repo).retry_delivery(notif.id, notif.user_id)

    repo.create.assert_not_awaited()
    repo.get_by_id.assert_awaited_once_with(notif.id, user_id=notif.user_id)
