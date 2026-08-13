"""Claim-arbitration tests for the Stripe webhook durable inbox (#47/#42).

Verifies the repository-level idempotency contract that makes repeated or
concurrent delivery of the same Stripe event produce exactly one side
effect, and that reclaim/retry is bounded:

  * first claim wins the insert race (unique event_id constraint);
  * PROCESSED rows are never reclaimed;
  * FAILED rows are reclaimed only until max_attempts;
  * PROCESSING rows are reclaimed only once the lease is stale;
  * invalid signatures are rejected before any state mutation (route level).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import StripeWebhookEvent, WebhookEventStatus
from app.repositories import WebhookEventRepository

pytestmark = pytest.mark.asyncio


def _make_row(
    status: WebhookEventStatus = WebhookEventStatus.PROCESSED,
    attempts: int = 1,
    claimed_at: datetime | None = None,
    event_id: str | None = None,
):
    row = StripeWebhookEvent(
        event_id=event_id or f"evt_{uuid4().hex}",
        event_type="checkout.session.completed",
        status=status,
        attempts=attempts,
        claimed_at=claimed_at,
    )
    return row


async def _repo_with(existing: StripeWebhookEvent | None, flush_raises: bool = False):
    session = AsyncMock()
    session.add = Mock()
    select_result = Mock()
    select_result.scalar_one_or_none.return_value = existing
    update_result = Mock()
    update_result.rowcount = 1
    session.execute.side_effect = [select_result, update_result]
    if flush_raises:
        session.flush.side_effect = IntegrityError(
            "stmt", {}, Exception("duplicate key")
        )
    return WebhookEventRepository(session), session


class TestClaimArbitration:
    async def test_first_claim_wins(self):
        repo, session = await _repo_with(existing=None)

        won = await repo.claim("evt_new_1", "checkout.session.completed")

        assert won is True
        session.add.assert_called_once()
        session.commit.assert_not_awaited()

    async def test_replay_after_processed_is_not_reclaimed(self):
        repo, _session = await _repo_with(
            existing=_make_row(status=WebhookEventStatus.PROCESSED), flush_raises=True
        )

        won = await repo.claim("evt_proc_1", "checkout.session.completed")

        assert won is False

    async def test_failed_below_max_attempts_is_reclaimed(self):
        repo, session = await _repo_with(
            existing=_make_row(status=WebhookEventStatus.FAILED, attempts=1),
            flush_raises=True,
        )

        won = await repo.claim("evt_fail_1", "checkout.session.completed")

        assert won is True
        guarded = session.execute.await_args_list[1].args[0]
        criteria = " ".join(str(col) for col in guarded._where_criteria)
        assert "stripe_webhook_events.status" in criteria
        assert "stripe_webhook_events.attempts" in criteria

    async def test_failed_exhausted_attempts_is_not_reclaimed(self):
        repo, _session = await _repo_with(
            existing=_make_row(status=WebhookEventStatus.FAILED, attempts=3),
            flush_raises=True,
        )

        won = await repo.claim("evt_exh_1", "checkout.session.completed", max_attempts=3)

        assert won is False

    async def test_processing_fresh_lease_is_not_reclaimed(self):
        fresh = datetime.now(UTC).replace(tzinfo=None)
        repo, _session = await _repo_with(
            existing=_make_row(
                status=WebhookEventStatus.PROCESSING, claimed_at=fresh, attempts=1
            ),
            flush_raises=True,
        )

        won = await repo.claim("evt_fresh_1", "checkout.session.completed")

        assert won is False

    async def test_processing_stale_lease_is_reclaimed(self):
        stale = (datetime.now(UTC) - timedelta(seconds=120)).replace(tzinfo=None)
        repo, _session = await _repo_with(
            existing=_make_row(
                status=WebhookEventStatus.PROCESSING, claimed_at=stale, attempts=1
            ),
            flush_raises=True,
        )

        won = await repo.claim("evt_stale_1", "checkout.session.completed")

        assert won is True

    async def test_complete_and_fail_mark_state(self):
        repo, session = await _repo_with(existing=None)
        assert await repo.complete("evt_done_1") is True
        assert await repo.fail("evt_err_1", "boom") is True
        assert session.execute.await_count == 2