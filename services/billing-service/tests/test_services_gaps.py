"""Coverage-closing behavioural tests for ``app.services.BillingService``.

Focus areas that the existing suite does not reach:

  * ``validate_transition`` rejection path (the FSM guard itself);
  * the thin accessors (``get_subscription``, ``list_floors``, ``commit`` /
    ``rollback``) which must delegate without inventing behaviour;
  * ``subscribe`` / ``cancel_subscription`` idempotent no-ops;
  * ``_fetch_content_details`` / ``_fetch_content_price`` — the content-service
    HTTP call, including 404, non-404, missing price, and bad creator_id;
  * ``sync_subscription_from_stripe`` — the monotonic stale-event guard and the
    full status→FSM mapping;
  * ``process_refund`` invoice-resolution cascade and its failure branches;
  * ``create_milestone`` / ``accrue_pool`` delegation.

All collaborators are mocks; these tests never touch a database.
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from app.models import (
    InvoiceStatus,
    MilestoneStatus,
    RefundStatus,
    RevenueTier,
    SubscriptionStatus,
    TrancheStatus,
)
from app.services import (
    INVOICE_TRANSITIONS,
    PAYOUT_TRANSITIONS,
    SUBSCRIPTION_TRANSITIONS,
    TRANCHE_TRANSITIONS,
    BillingService,
    DuplicatePayoutError,
    InvalidStateTransitionError,
    MilestoneAuthorizationError,
    MilestoneKillError,
    TierInvalidError,
    validate_transition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_REPO_NAMES = (
    "sub_repo",
    "purchase_repo",
    "inv_repo",
    "floor_repo",
    "pool_repo",
    "milestone_repo",
    "payout_repo",
    "refund_repo",
    "webhook_events_repo",
)


def _service(**overrides) -> BillingService:
    """A BillingService whose repositories are all AsyncMocks.

    Keys may be ``repo_name`` to replace a whole repository, or
    ``repo_name.attr`` to replace a single method on one.
    """
    repos = {name: AsyncMock() for name in _REPO_NAMES}
    service = BillingService(**repos)
    for key, value in overrides.items():
        target, _, attr = key.partition(".")
        if attr:
            setattr(getattr(service, target), attr, value)
        else:
            setattr(service, target, value)
    return service


def _sub(**overrides) -> MagicMock:
    defaults = {
        "id": uuid4(),
        "tier": RevenueTier.SVOD,
        "monthly_price": Decimal("7.99"),
        "status": SubscriptionStatus.ACTIVE,
        "is_active": True,
        "cancelled_at": None,
        "last_stripe_event_ts": None,
    }
    sub = MagicMock(**defaults)
    for key, value in overrides.items():
        setattr(sub, key, value)
    return sub


def _invoice(**overrides) -> MagicMock:
    defaults = {
        "id": uuid4(),
        "amount": Decimal("10.00"),
        "currency": "USD",
        "status": InvoiceStatus.PAID,
    }
    inv = MagicMock(**defaults)
    for key, value in overrides.items():
        setattr(inv, key, value)
    return inv


def _purchase(**overrides) -> MagicMock:
    defaults = {"id": uuid4(), "content_id": uuid4(), "price": Decimal("10.00"), "currency": "USD"}
    p = MagicMock(**defaults)
    for key, value in overrides.items():
        setattr(p, key, value)
    return p


# ---------------------------------------------------------------------------
# validate_transition
# ---------------------------------------------------------------------------


class TestValidateTransition:
    def test_allowed_transition_passes(self):
        assert (
            validate_transition(
                SubscriptionStatus.ACTIVE, SubscriptionStatus.CANCELLED, SUBSCRIPTION_TRANSITIONS
            )
            is None
        )

    def test_raw_string_keys_are_accepted(self):
        assert validate_transition("accrued", "paid", PAYOUT_TRANSITIONS) is None

    def test_disallowed_transition_raises(self):
        with pytest.raises(InvalidStateTransitionError, match="not in"):
            validate_transition(
                SubscriptionStatus.CANCELLED, "nonsense", SUBSCRIPTION_TRANSITIONS
            )

    def test_unknown_current_state_raises(self):
        with pytest.raises(InvalidStateTransitionError) as exc:
            validate_transition("nonexistent", "paid", PAYOUT_TRANSITIONS)
        assert "nonexistent" in str(exc.value)

    def test_error_names_the_context(self):
        with pytest.raises(InvalidStateTransitionError, match="stripe_sync"):
            validate_transition(
                SubscriptionStatus.CANCELLED, "nonsense", SUBSCRIPTION_TRANSITIONS, context="stripe_sync"
            )

    def test_error_lists_the_legal_source_states(self):
        with pytest.raises(InvalidStateTransitionError) as exc:
            validate_transition("zzz", "paid", PAYOUT_TRANSITIONS)
        # The message must show the operator what the legal sources are.
        assert "accrued" in str(exc.value)

    def test_refunded_invoice_is_terminal(self):
        # REFUNDED is terminal per the state machine in the module docstring.
        assert INVOICE_TRANSITIONS[InvoiceStatus.REFUNDED] == (InvoiceStatus.REFUNDED,)
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(
                InvoiceStatus.REFUNDED, InvoiceStatus.PAID, INVOICE_TRANSITIONS
            )

    def test_released_tranche_is_terminal(self):
        assert TRANCHE_TRANSITIONS[TrancheStatus.RELEASED] == (TrancheStatus.RELEASED,)
        with pytest.raises(InvalidStateTransitionError):
            validate_transition(
                TrancheStatus.RELEASED, TrancheStatus.REVERTED, TRANCHE_TRANSITIONS
            )

    def test_paid_payout_is_terminal(self):
        with pytest.raises(InvalidStateTransitionError):
            validate_transition("paid", "accrued", PAYOUT_TRANSITIONS)


# ---------------------------------------------------------------------------
# Thin accessors and transaction helpers
# ---------------------------------------------------------------------------


class TestAccessorsAndTransactions:
    async def test_get_subscription_delegates_to_the_repository(self):
        sub = _sub()
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=sub)
        user = uuid4()
        assert await svc.get_subscription(user) is sub
        svc.sub_repo.get_by_user.assert_awaited_once_with(user)

    async def test_get_subscription_returns_none_when_absent(self):
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=None)
        assert await svc.get_subscription(uuid4()) is None

    async def test_list_floors_delegates(self):
        floors = [MagicMock(), MagicMock()]
        svc = _service()
        svc.floor_repo.list_all = AsyncMock(return_value=floors)
        assert await svc.list_floors() is floors
        svc.floor_repo.list_all.assert_awaited_once()

    async def test_get_floor_delegates_with_the_region_code(self):
        svc = _service()
        svc.floor_repo.get_by_region = AsyncMock(return_value=MagicMock())
        assert await svc.get_floor("EU") is not None
        svc.floor_repo.get_by_region.assert_awaited_once_with("EU")

    async def test_commit_forwards_to_the_shared_session(self):
        # One commit per operation (#211) — every repo shares one session.
        session = AsyncMock()
        svc = _service()
        svc.webhook_events_repo.session = session
        await svc.commit()
        session.commit.assert_awaited_once()

    async def test_rollback_forwards_to_the_shared_session(self):
        session = AsyncMock()
        svc = _service()
        svc.webhook_events_repo.session = session
        await svc.rollback()
        session.rollback.assert_awaited_once()

    async def test_accrue_pool_uses_the_configured_pool_percentage(self):
        svc = _service()
        svc.pool_repo.create_entry = AsyncMock(return_value=MagicMock())
        start, end = datetime(2026, 1, 1), datetime(2026, 2, 1)
        await svc.accrue_pool(start, end, Decimal("100.00"))
        svc.pool_repo.create_entry.assert_awaited_once_with(
            start, end, Decimal("100.00"), Decimal("0.15")
        )

    async def test_create_milestone_delegates_to_the_repository(self):
        milestone = MagicMock()
        svc = _service()
        svc.milestone_repo.create = AsyncMock(return_value=milestone)
        creator = uuid4()
        result = await svc.create_milestone(
            creator, "Feature Film", Decimal("10000.00"),
            caller_id=uuid4(), caller_is_admin=True,
        )
        assert result is milestone
        svc.milestone_repo.create.assert_awaited_once_with(
            creator, "Feature Film", Decimal("10000.00")
        )

    async def test_create_milestone_rejects_an_unauthorised_caller(self):
        svc = _service()
        with pytest.raises(MilestoneAuthorizationError, match="not authorized"):
            await svc.create_milestone(uuid4(), "X", Decimal("1"), caller_id=uuid4())
        svc.milestone_repo.create.assert_not_awaited()

    async def test_create_milestone_allows_an_admin_on_behalf_of_another_creator(self):
        svc = _service()
        svc.milestone_repo.create = AsyncMock(return_value=MagicMock())
        await svc.create_milestone(
            uuid4(), "X", Decimal("1"), caller_id=uuid4(), caller_is_admin=True
        )
        svc.milestone_repo.create.assert_awaited_once()

    async def test_kill_milestone_reverts_only_locked_tranches(self):
        milestone = MagicMock(status=MilestoneStatus.PENDING)
        locked = MagicMock(status=TrancheStatus.LOCKED)
        released = MagicMock(status=TrancheStatus.RELEASED)
        svc = _service()
        svc.milestone_repo.get = AsyncMock(return_value=milestone)
        svc.milestone_repo.get_tranches = AsyncMock(return_value=[locked, released])
        result = await svc.kill_milestone(
            uuid4(), caller_id=uuid4(), caller_is_admin=True
        )
        assert milestone.status == MilestoneStatus.KILLED
        # An already-released tranche is NOT clawed back.
        assert locked.status == TrancheStatus.REVERTED
        assert released.status == TrancheStatus.RELEASED
        assert locked.reverted_at is not None
        assert result is milestone

    async def test_release_tranche_rejects_a_killed_milestone(self):
        svc = _service()
        svc.milestone_repo.get = AsyncMock(return_value=MagicMock(status=MilestoneStatus.KILLED))
        with pytest.raises(MilestoneKillError):
            await svc.release_tranche(uuid4(), 1, caller_id=uuid4(), caller_is_admin=True)
        svc.milestone_repo.get_tranches.assert_not_awaited()

    async def test_release_tranche_rejects_a_non_admin_caller(self):
        svc = _service()
        with pytest.raises(MilestoneAuthorizationError, match="admin privileges required"):
            await svc.release_tranche(uuid4(), 1, caller_id=uuid4(), caller_is_admin=False)
        svc.milestone_repo.get.assert_not_awaited()

    async def test_release_tranche_reports_a_missing_milestone(self):
        svc = _service()
        svc.milestone_repo.get = AsyncMock(return_value=None)
        mid = uuid4()
        with pytest.raises(Exception, match=f"Milestone {mid} not found"):
            await svc.release_tranche(mid, 1, caller_id=uuid4(), caller_is_admin=True)

    async def test_release_tranche_reports_a_missing_tranche_number(self):
        mid = uuid4()
        svc = _service()
        svc.milestone_repo.get = AsyncMock(return_value=MagicMock())
        svc.milestone_repo.get_tranches = AsyncMock(
            return_value=[MagicMock(tranche_number=1, status=TrancheStatus.LOCKED)]
        )
        with pytest.raises(Exception, match="Tranche 3 not found"):
            await svc.release_tranche(mid, 3, caller_id=uuid4(), caller_is_admin=True)

    async def test_release_tranche_accrues_a_payout_for_the_released_funds(self):
        mid, creator = uuid4(), uuid4()
        tranche = MagicMock(
            tranche_number=2, status=TrancheStatus.LOCKED, amount=Decimal("2000.00")
        )
        milestone = MagicMock(creator_id=creator, created_at=datetime(2026, 1, 1))
        svc = _service()
        svc.milestone_repo.get = AsyncMock(return_value=milestone)
        svc.milestone_repo.get_tranches = AsyncMock(return_value=[tranche])
        svc.payout_repo.accrue = AsyncMock()
        result = await svc.release_tranche(mid, 2, caller_id=uuid4(), caller_is_admin=True)
        assert result.status == TrancheStatus.RELEASED
        assert tranche.released_at is not None
        kwargs = svc.payout_repo.accrue.call_args.kwargs
        assert kwargs["idempotency_key"] == f"tranche:{mid}:2"
        assert kwargs["amount"] == Decimal("2000.00")
        assert kwargs["currency"] == "USD"
        assert kwargs["breakdown"]["tranche_number"] == 2

    async def test_release_tranche_tolerates_an_idempotent_redelivery(self):
        # TRANCHE_TRANSITIONS allows RELEASED -> RELEASED, so a Stripe redelivery
        # of the same release is a no-op rather than an error.
        tranche = MagicMock(tranche_number=1, status=TrancheStatus.RELEASED, amount=Decimal("1.00"))
        milestone = MagicMock(creator_id=uuid4(), created_at=datetime(2026, 1, 1))
        svc = _service()
        svc.milestone_repo.get = AsyncMock(return_value=milestone)
        svc.milestone_repo.get_tranches = AsyncMock(return_value=[tranche])
        svc.payout_repo.accrue = AsyncMock()
        await svc.release_tranche(uuid4(), 1, caller_id=uuid4(), caller_is_admin=True)
        svc.payout_repo.accrue.assert_awaited_once()

    async def test_release_tranche_rejects_a_reverted_tranche(self):
        # REVERTED is terminal: a killed milestone's funds must never be
        # re-released into a payout accrual.
        tranche = MagicMock(tranche_number=1, status=TrancheStatus.REVERTED)
        svc = _service()
        svc.milestone_repo.get = AsyncMock(return_value=MagicMock())
        svc.milestone_repo.get_tranches = AsyncMock(return_value=[tranche])
        svc.payout_repo.accrue = AsyncMock()
        with pytest.raises(InvalidStateTransitionError):
            await svc.release_tranche(uuid4(), 1, caller_id=uuid4(), caller_is_admin=True)
        svc.payout_repo.accrue.assert_not_awaited()

    async def test_kill_milestone_reports_a_missing_milestone(self):
        svc = _service()
        svc.milestone_repo.get = AsyncMock(return_value=None)
        mid = uuid4()
        with pytest.raises(Exception, match=f"Milestone {mid} not found"):
            await svc.kill_milestone(mid, caller_id=uuid4(), caller_is_admin=True)

    async def test_kill_milestone_rejects_a_non_admin_caller(self):
        svc = _service()
        with pytest.raises(MilestoneAuthorizationError, match="admin privileges required"):
            await svc.kill_milestone(uuid4(), caller_id=uuid4(), caller_is_admin=False)

    async def test_get_payout_history_delegates(self):
        entries = [MagicMock()]
        svc = _service()
        svc.payout_repo.get_by_creator = AsyncMock(return_value=entries)
        creator = uuid4()
        assert await svc.get_payout_history(creator) is entries
        svc.payout_repo.get_by_creator.assert_awaited_once_with(creator)

    async def test_get_pool_status_delegates(self):
        svc = _service()
        svc.pool_repo.get_latest = AsyncMock(return_value="entry")
        assert await svc.get_pool_status() == "entry"

    async def test_accrue_payout_creates_a_new_entry(self):
        svc = _service()
        svc.payout_repo.get_by_idempotency_key = AsyncMock(return_value=None)
        svc.payout_repo.accrue = AsyncMock(return_value="ledger")
        result = await svc.accrue_payout(
            uuid4(), Decimal("5.00"), "USD", "idem-1", datetime(2026, 1, 1), datetime(2026, 2, 1)
        )
        assert result == "ledger"
        assert svc.payout_repo.accrue.call_args.kwargs["idempotency_key"] == "idem-1"

    async def test_accrue_payout_is_idempotent_for_an_identical_retry(self):
        existing = MagicMock(amount=Decimal("5.00"))
        svc = _service()
        svc.payout_repo.get_by_idempotency_key = AsyncMock(return_value=existing)
        svc.payout_repo.accrue = AsyncMock()
        result = await svc.accrue_payout(
            uuid4(), Decimal("5.00"), "USD", "idem-1", datetime(2026, 1, 1), datetime(2026, 2, 1)
        )
        assert result is existing
        svc.payout_repo.accrue.assert_not_awaited()

    async def test_accrue_payout_rejects_a_conflicting_amount(self):
        svc = _service()
        svc.payout_repo.get_by_idempotency_key = AsyncMock(
            return_value=MagicMock(amount=Decimal("5.00"))
        )
        with pytest.raises(DuplicatePayoutError, match="cannot re-accrue"):
            await svc.accrue_payout(
                uuid4(), Decimal("9.99"), "USD", "idem-1", datetime(2026, 1, 1), datetime(2026, 2, 1)
            )

    async def test_accrue_payout_rejects_an_unlisted_currency(self):
        svc = _service()
        with pytest.raises(ValueError, match="Unsupported currency code"):
            await svc.accrue_payout(
                uuid4(), Decimal("5.00"), "ZZZ", "idem-1", datetime(2026, 1, 1), datetime(2026, 2, 1)
            )

    def test_calculate_creator_share_is_at_least_55_percent(self):
        assert BillingService.calculate_creator_share(Decimal("100.00")) == Decimal("55.00")

    def test_calculate_creator_share_is_exact_for_awkward_amounts(self):
        # Decimal, not float: 0.55 * 9.99 must be exactly 5.4945.
        assert BillingService.calculate_creator_share(Decimal("9.99")) == Decimal("5.4945")
        assert BillingService.calculate_creator_share(Decimal("0")) == Decimal("0.00")


# ---------------------------------------------------------------------------
# subscribe / cancel_subscription idempotency
# ---------------------------------------------------------------------------


class TestSubscriptionIdempotency:
    async def test_subscribe_with_the_same_tier_is_a_no_op(self):
        existing = _sub(tier=RevenueTier.SVOD)
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=existing)
        user = uuid4()
        result = await svc.subscribe(user, "svod")
        assert result is existing
        svc.sub_repo.create.assert_not_awaited()
        svc.sub_repo.session.flush.assert_not_awaited()

    async def test_subscribe_is_case_insensitive(self):
        existing = _sub(tier=RevenueTier.SVOD)
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=existing)
        assert await svc.subscribe(uuid4(), "SVOD") is existing

    async def test_subscribe_rejects_an_unknown_tier(self):
        svc = _service()
        with pytest.raises(TierInvalidError, match="Must be one of"):
            await svc.subscribe(uuid4(), "premium")
        svc.sub_repo.get_by_user.assert_not_awaited()

    async def test_subscribe_creates_when_absent(self):
        created = _sub()
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=None)
        svc.sub_repo.create = AsyncMock(return_value=created)
        user = uuid4()
        assert await svc.subscribe(user, "svod") is created
        # SVOD carries the product price; AVOD is free.
        svc.sub_repo.create.assert_awaited_once_with(
            user, RevenueTier.SVOD, Decimal("7.99")
        )

    async def test_upgrade_reactivates_a_cancelled_subscription(self):
        existing = _sub(
            tier=RevenueTier.AVOD, status=SubscriptionStatus.CANCELLED, is_active=False
        )
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=existing)
        await svc.subscribe(uuid4(), "svod")
        assert existing.tier == RevenueTier.SVOD
        assert existing.monthly_price == Decimal("7.99")
        assert existing.status == SubscriptionStatus.ACTIVE
        assert existing.is_active is True
        assert existing.cancelled_at is None
        assert existing.renewal_date is not None
        svc.sub_repo.session.flush.assert_awaited_once()

    async def test_cancel_subscription_returns_none_when_absent(self):
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=None)
        assert await svc.cancel_subscription(uuid4()) is None

    async def test_cancel_subscription_is_idempotent_when_already_cancelled(self):
        existing = _sub(status=SubscriptionStatus.CANCELLED)
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=existing)
        result = await svc.cancel_subscription(uuid4())
        assert result is existing
        # A second cancel must not re-stamp cancelled_at.
        assert existing.cancelled_at is None

    async def test_cancel_subscription_reverts_to_free_avod(self):
        existing = _sub(status=SubscriptionStatus.ACTIVE, tier=RevenueTier.SVOD)
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=existing)
        await svc.cancel_subscription(uuid4())
        assert existing.tier == RevenueTier.AVOD
        assert existing.monthly_price == Decimal("0.00")
        assert existing.is_active is False
        assert existing.status == SubscriptionStatus.CANCELLED
        assert existing.cancelled_at is not None


# ---------------------------------------------------------------------------
# _fetch_content_details / _fetch_content_price
# ---------------------------------------------------------------------------


def _json_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://content-service/api/v1/content/x")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("boom", request=request, response=response)


class TestFetchContentDetails:
    async def test_returns_price_and_creator(self):
        creator = uuid4()
        content = uuid4()
        svc = _service()
        with patch("app.services.httpx.AsyncClient") as client_cls:
            client = client_cls.return_value.__aenter__.return_value
            client.get = AsyncMock(
                return_value=_json_response({"price_usd": "9.99", "creator_id": str(creator)})
            )
            price, resolved = await svc._fetch_content_details(content)
        assert price == Decimal("9.99")
        assert resolved == creator
        # The canonical price comes from the content service, never the client.
        client.get.assert_awaited_once_with(
            f"http://content-service:8000/api/v1/content/{content}"
        )

    @pytest.mark.parametrize("field", ["creator_id", "creatorId", "creator"])
    async def test_creator_id_aliases_are_all_accepted(self, field):
        creator = uuid4()
        svc = _service()
        with patch("app.services.httpx.AsyncClient") as client_cls:
            client = client_cls.return_value.__aenter__.return_value
            client.get = AsyncMock(
                return_value=_json_response({"price_usd": 5, field: str(creator)})
            )
            _, resolved = await svc._fetch_content_details(uuid4())
        assert resolved == creator

    async def test_missing_price_is_rejected(self):
        svc = _service()
        with patch("app.services.httpx.AsyncClient") as client_cls:
            client = client_cls.return_value.__aenter__.return_value
            client.get = AsyncMock(return_value=_json_response({"creator_id": str(uuid4())}))
            with pytest.raises(ValueError, match="does not have a TVOD price set"):
                await svc._fetch_content_details(uuid4())

    async def test_missing_creator_is_rejected(self):
        svc = _service()
        with patch("app.services.httpx.AsyncClient") as client_cls:
            client = client_cls.return_value.__aenter__.return_value
            client.get = AsyncMock(return_value=_json_response({"price_usd": 5}))
            with pytest.raises(ValueError, match="has no creator_id"):
                await svc._fetch_content_details(uuid4())

    async def test_malformed_creator_id_is_rejected(self):
        svc = _service()
        with patch("app.services.httpx.AsyncClient") as client_cls:
            client = client_cls.return_value.__aenter__.return_value
            client.get = AsyncMock(
                return_value=_json_response({"price_usd": 5, "creator_id": "not-a-uuid"})
            )
            with pytest.raises(ValueError, match="Invalid creator_id"):
                await svc._fetch_content_details(uuid4())

    async def test_404_is_reported_as_not_found(self):
        svc = _service()
        with patch("app.services.httpx.AsyncClient") as client_cls:
            client = client_cls.return_value.__aenter__.return_value
            client.get = AsyncMock(side_effect=_status_error(404))
            with pytest.raises(ValueError, match="not found"):
                await svc._fetch_content_details(uuid4())

    async def test_other_http_status_is_wrapped(self):
        svc = _service()
        with patch("app.services.httpx.AsyncClient") as client_cls:
            client = client_cls.return_value.__aenter__.return_value
            client.get = AsyncMock(side_effect=_status_error(503))
            with pytest.raises(ValueError, match="Failed to fetch content details"):
                await svc._fetch_content_details(uuid4())

    async def test_transport_failure_is_wrapped(self):
        svc = _service()
        with patch("app.services.httpx.AsyncClient") as client_cls:
            client = client_cls.return_value.__aenter__.return_value
            client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            with pytest.raises(ValueError, match="Failed to fetch content details"):
                await svc._fetch_content_details(uuid4())

    async def test_timeout_is_bounded_at_five_seconds(self):
        # A hung content service must not hang the webhook handler.
        svc = _service()
        with patch("app.services.httpx.AsyncClient") as client_cls:
            client_cls.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=_json_response({"price_usd": 1, "creator_id": str(uuid4())})
            )
            await svc._fetch_content_details(uuid4())
        assert client_cls.call_args.kwargs["timeout"] == 5.0

    async def test_fetch_content_price_returns_only_the_price(self):
        creator = uuid4()
        svc = _service()
        with patch("app.services.httpx.AsyncClient") as client_cls:
            client = client_cls.return_value.__aenter__.return_value
            client.get = AsyncMock(
                return_value=_json_response({"price_usd": "2.50", "creator_id": str(creator)})
            )
            assert await svc._fetch_content_price(uuid4()) == Decimal("2.50")


# ---------------------------------------------------------------------------
# sync_subscription_from_stripe
# ---------------------------------------------------------------------------


class TestSyncSubscriptionFromStripe:
    async def test_returns_none_when_the_user_has_no_subscription(self):
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=None)
        assert await svc.sync_subscription_from_stripe(uuid4(), "active", 100) is None
        svc.sub_repo.session.flush.assert_not_awaited()

    async def test_active_status_activates_the_subscription(self):
        sub = _sub(status=SubscriptionStatus.CANCELLED, is_active=False, tier=RevenueTier.AVOD)
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=sub)
        result = await svc.sync_subscription_from_stripe(uuid4(), "active", 200)
        assert result is sub
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.is_active is True
        # Reactivation must not wipe the price or stamp a cancellation.
        assert sub.tier == RevenueTier.AVOD
        assert sub.cancelled_at is None
        assert sub.last_stripe_event_ts == 200
        svc.sub_repo.session.flush.assert_awaited_once()

    @pytest.mark.parametrize("stripe_status", ["canceled", "unpaid", "incomplete_expired"])
    async def test_terminal_stripe_statuses_revert_to_free_avod(self, stripe_status):
        sub = _sub(status=SubscriptionStatus.ACTIVE, is_active=True, tier=RevenueTier.SVOD)
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=sub)
        await svc.sync_subscription_from_stripe(uuid4(), stripe_status, 200)
        assert sub.status == SubscriptionStatus.CANCELLED
        assert sub.is_active is False
        assert sub.tier == RevenueTier.AVOD
        assert sub.monthly_price == Decimal("0.00")
        assert sub.cancelled_at is not None

    @pytest.mark.parametrize("stripe_status", ["past_due", "trialing", "incomplete", "paused", ""])
    async def test_unmapped_statuses_leave_the_state_untouched(self, stripe_status):
        sub = _sub(status=SubscriptionStatus.ACTIVE, is_active=True)
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=sub)
        result = await svc.sync_subscription_from_stripe(uuid4(), stripe_status, 200)
        # past_due etc. are not in the mapping, so nothing may be reconciled —
        # and critically the monotonic timestamp must not advance either.
        assert result is sub
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.last_stripe_event_ts is None

    async def test_stale_event_is_ignored_by_the_monotonic_guard(self):
        # #482: an out-of-order Stripe retry must not regress the state.
        sub = _sub(
            status=SubscriptionStatus.CANCELLED,
            is_active=False,
            last_stripe_event_ts=1000,
        )
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=sub)
        result = await svc.sync_subscription_from_stripe(uuid4(), "active", 999)
        assert result is sub
        assert sub.status == SubscriptionStatus.CANCELLED
        assert sub.last_stripe_event_ts == 1000
        svc.sub_repo.session.flush.assert_not_awaited()

    async def test_equal_timestamp_is_not_stale(self):
        sub = _sub(status=SubscriptionStatus.ACTIVE, last_stripe_event_ts=1000)
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=sub)
        result = await svc.sync_subscription_from_stripe(uuid4(), "canceled", 1000)
        assert sub.status == SubscriptionStatus.CANCELLED
        assert sub.last_stripe_event_ts == 1000
        svc.sub_repo.session.flush.assert_awaited_once()

    async def test_newer_event_advances_the_timestamp(self):
        sub = _sub(status=SubscriptionStatus.ACTIVE, last_stripe_event_ts=1000)
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=sub)
        await svc.sync_subscription_from_stripe(uuid4(), "active", 1001)
        assert sub.last_stripe_event_ts == 1001

    async def test_redelivery_of_the_same_event_is_idempotent(self):
        sub = _sub(status=SubscriptionStatus.CANCELLED, is_active=False, tier=RevenueTier.AVOD)
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=sub)
        first = await svc.sync_subscription_from_stripe(uuid4(), "canceled", 2000)
        first_cancelled_at = first.cancelled_at
        second = await svc.sync_subscription_from_stripe(uuid4(), "canceled", 2000)
        # The second delivery must not re-stamp the cancellation time.
        assert second.cancelled_at == first_cancelled_at

    async def test_status_already_at_target_does_not_mutate(self):
        sub = _sub(status=SubscriptionStatus.ACTIVE, is_active=True, last_stripe_event_ts=None)
        svc = _service()
        svc.sub_repo.get_by_user = AsyncMock(return_value=sub)
        await svc.sync_subscription_from_stripe(uuid4(), "active", 500)
        assert sub.status == SubscriptionStatus.ACTIVE
        assert sub.is_active is True
        # The timestamp is still advanced so a later event is not treated as new.
        assert sub.last_stripe_event_ts == 500


# ---------------------------------------------------------------------------
# process_refund invoice-resolution cascade
# ---------------------------------------------------------------------------


def _refund_service(**repo_overrides) -> BillingService:
    """A BillingService with the refund cascade collaborators fully stubbed."""
    return _service(
        **{
            "refund_repo.get_by_refund_id": AsyncMock(return_value=None),
            "refund_repo.apply_to_invoice": AsyncMock(return_value=True),
            "refund_repo.create": AsyncMock(side_effect=lambda **kw: MagicMock(**kw)),
            "inv_repo.get": AsyncMock(return_value=None),
            "inv_repo.get_by_stripe_invoice_id": AsyncMock(return_value=None),
            "inv_repo.get_by_purchase_id": AsyncMock(return_value=None),
            "purchase_repo.get_by_stripe_payment_intent_id": AsyncMock(return_value=None),
            **repo_overrides,
        }
    )


class TestProcessRefundResolutionCascade:
    async def test_duplicate_refund_returns_the_existing_row(self):
        existing = MagicMock(refund_id="re_1")
        svc = _refund_service()
        svc.refund_repo.get_by_refund_id = AsyncMock(return_value=existing)
        with patch("app.services.StripeClient.retrieve_refund", side_effect=AssertionError("no call")):
            result = await svc.process_refund("re_1", "ch_1", Decimal("5.00"), "USD")
        assert result is existing
        svc.refund_repo.create.assert_not_awaited()

    async def test_unlisted_currency_is_rejected(self):
        svc = _refund_service()
        with pytest.raises(ValueError, match="Unsupported currency code"):
            await svc.process_refund("re_1", "ch_1", Decimal("5.00"), "ZZZ")

    async def test_resolves_the_invoice_from_the_stripe_invoice_id(self):
        invoice = _invoice(amount=Decimal("10.00"))
        svc = _refund_service()
        svc.inv_repo.get_by_stripe_invoice_id = AsyncMock(return_value=invoice)
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_1", "ch_1", Decimal("5.00"), "USD", stripe_invoice_id="in_1"
                )
        svc.inv_repo.get_by_stripe_invoice_id.assert_awaited_once_with("in_1")
        assert result.status == RefundStatus.PROCESSED
        assert svc.refund_repo.apply_to_invoice.call_args.args[0] == invoice.id

    async def test_a_mismatched_stripe_refund_is_held_for_review(self):
        # The webhook says 5.00 but Stripe's authoritative record says 9.99 —
        # never auto-apply an amount we cannot reconcile.
        invoice = _invoice(amount=Decimal("20.00"))
        svc = _refund_service()
        svc.inv_repo.get = AsyncMock(return_value=invoice)
        with patch(
            "app.services.StripeClient.retrieve_refund",
            return_value={"amount": 999, "currency": "usd", "charge": "ch_stripe"},
        ):
            result = await svc.process_refund(
                "re_1", "ch_1", Decimal("5.00"), "USD", invoice_id=invoice.id
            )
        assert result.status == RefundStatus.PENDING_REVIEW
        svc.refund_repo.apply_to_invoice.assert_not_awaited()

    async def test_matching_stripe_refund_is_applied_against_the_invoice(self):
        invoice = _invoice(amount=Decimal("20.00"))
        svc = _refund_service()
        svc.inv_repo.get = AsyncMock(return_value=invoice)
        with patch(
            "app.services.StripeClient.retrieve_refund",
            return_value={"amount": 500, "currency": "USD"},
        ):
            result = await svc.process_refund(
                "re_1", "ch_1", Decimal("5.00"), "USD", invoice_id=invoice.id
            )
        assert result.status == RefundStatus.PROCESSED

    async def test_unconvertible_stripe_amount_falls_back_to_the_webhook_amount(self):
        invoice = _invoice(amount=Decimal("20.00"))
        svc = _refund_service()
        svc.inv_repo.get = AsyncMock(return_value=invoice)
        with patch(
            "app.services.StripeClient.retrieve_refund",
            return_value={"amount": "not-a-number", "currency": "USD"},
        ):
            result = await svc.process_refund(
                "re_1", "ch_1", Decimal("5.00"), "USD", invoice_id=invoice.id
            )
        assert result.status == RefundStatus.PROCESSED

    async def test_stripe_lookup_failure_is_survived(self):
        from app.core.stripe_client import StripeError

        invoice = _invoice(amount=Decimal("20.00"))
        svc = _refund_service()
        svc.inv_repo.get = AsyncMock(return_value=invoice)
        with patch("app.services.StripeClient.retrieve_refund", side_effect=StripeError("down")):
            result = await svc.process_refund(
                "re_1", "ch_1", Decimal("5.00"), "USD", invoice_id=invoice.id
            )
        # A Stripe outage must not block reconciliation of the local invoice.
        assert result.status == RefundStatus.PROCESSED

    async def test_unexpected_stripe_lookup_exception_is_survived(self):
        invoice = _invoice(amount=Decimal("20.00"))
        svc = _refund_service()
        svc.inv_repo.get = AsyncMock(return_value=invoice)
        with patch(
            "app.services.StripeClient.retrieve_refund", side_effect=RuntimeError("kaboom")
        ):
            result = await svc.process_refund(
                "re_1", "ch_1", Decimal("5.00"), "USD", invoice_id=invoice.id
            )
        assert result.status == RefundStatus.PROCESSED

    async def test_unknown_local_invoice_id_falls_through_to_the_stripe_lookup(self):
        invoice = _invoice(amount=Decimal("20.00"))
        missing = uuid4()
        svc = _refund_service()
        svc.inv_repo.get = AsyncMock(return_value=None)
        svc.inv_repo.get_by_stripe_invoice_id = AsyncMock(return_value=invoice)
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_1", "ch_1", Decimal("5.00"), "USD",
                    invoice_id=missing, stripe_invoice_id="in_9",
                )
        # Stripe linkage is authoritative: it resolves to a different invoice
        # than the caller supplied, so the refund is held rather than applied
        # against an invoice the caller never named.
        svc.inv_repo.get.assert_not_awaited()
        svc.inv_repo.get_by_stripe_invoice_id.assert_awaited_once_with("in_9")
        assert result.status == RefundStatus.PENDING_REVIEW
        assert result.invoice_id is None

    async def test_resolves_the_invoice_through_the_payment_intent(self):
        purchase = _purchase()
        invoice = _invoice(amount=Decimal("20.00"))
        svc = _refund_service()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_1", "", Decimal("5.00"), "USD", payment_intent_id="pi_1"
                )
        svc.inv_repo.get_by_purchase_id.assert_awaited_once_with(purchase.id)
        assert result.status == RefundStatus.PROCESSED

    async def test_resolves_the_invoice_through_the_charge_stripe_invoice_field(self):
        invoice = _invoice(amount=Decimal("20.00"))
        svc = _refund_service()
        svc.inv_repo.get_by_stripe_invoice_id = AsyncMock(return_value=invoice)
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch(
                "app.services.StripeClient.retrieve_charge",
                return_value={"invoice": "in_charge", "payment_intent": None},
            ):
                result = await svc.process_refund(
                    "re_1", "ch_1", Decimal("5.00"), "USD", payment_intent_id="pi_1"
                )
        svc.inv_repo.get_by_stripe_invoice_id.assert_awaited_once_with("in_charge")
        assert result.status == RefundStatus.PROCESSED

    async def test_resolves_the_invoice_through_the_charge_payment_intent_field(self):
        purchase = _purchase()
        invoice = _invoice(amount=Decimal("20.00"))
        svc = _refund_service()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch(
                "app.services.StripeClient.retrieve_charge",
                return_value={"invoice": None, "payment_intent": "pi_from_charge"},
            ):
                result = await svc.process_refund("re_1", "ch_1", Decimal("5.00"), "USD")
        # The charge's payment_intent is adopted as the authoritative one.
        svc.purchase_repo.get_by_stripe_payment_intent_id.assert_any_await("pi_from_charge")
        assert result.status == RefundStatus.PROCESSED

    async def test_failing_invoice_lookup_is_survived_and_the_refund_is_held(self):
        svc = _refund_service()
        svc.inv_repo.get_by_stripe_invoice_id = AsyncMock(side_effect=RuntimeError("db down"))
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(side_effect=RuntimeError("db"))
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_1", "ch_1", Decimal("5.00"), "USD", stripe_invoice_id="in_1"
                )
        # Unresolvable invoice => PENDING_REVIEW, never auto-applied.
        assert result.status == RefundStatus.PENDING_REVIEW
        svc.refund_repo.apply_to_invoice.assert_not_awaited()

    async def test_failing_charge_lookup_is_survived(self):
        from app.core.stripe_client import StripeError

        svc = _refund_service()
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", side_effect=StripeError("x")):
                result = await svc.process_refund("re_1", "ch_1", Decimal("5.00"), "USD")
        assert result.status == RefundStatus.PENDING_REVIEW

    async def test_failing_unexpected_charge_lookup_is_survived(self):
        svc = _refund_service()
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch(
                "app.services.StripeClient.retrieve_charge", side_effect=RuntimeError("boom")
            ):
                result = await svc.process_refund("re_1", "ch_1", Decimal("5.00"), "USD")
        assert result.status == RefundStatus.PENDING_REVIEW

    async def test_failing_purchase_lookup_during_invoice_resolution_is_survived(self):
        svc = _refund_service()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(side_effect=RuntimeError("db"))
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_1", "ch_1", Decimal("5.00"), "USD", payment_intent_id="pi_1"
                )
        assert result.status == RefundStatus.PENDING_REVIEW

    async def test_unresolvable_invoice_is_held_for_review(self):
        svc = _refund_service()
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund("re_1", "", Decimal("5.00"), "USD")
        assert result.status == RefundStatus.PENDING_REVIEW
        assert result.invoice_id is None
        svc.refund_repo.apply_to_invoice.assert_not_awaited()

    async def test_invoice_currency_mismatch_is_held_for_review(self):
        invoice = _invoice(amount=Decimal("20.00"), currency="EUR")
        svc = _refund_service()
        svc.inv_repo.get = AsyncMock(return_value=invoice)
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_1", "ch_1", Decimal("5.00"), "USD", invoice_id=invoice.id
                )
        assert result.status == RefundStatus.PENDING_REVIEW
        assert result.invoice_id == invoice.id
        svc.refund_repo.apply_to_invoice.assert_not_awaited()

    async def test_refund_exceeding_the_invoice_is_rejected(self):
        invoice = _invoice(amount=Decimal("1.00"))
        svc = _refund_service()
        svc.inv_repo.get = AsyncMock(return_value=invoice)
        svc.refund_repo.apply_to_invoice = AsyncMock(return_value=False)
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_1", "ch_1", Decimal("5.00"), "USD", invoice_id=invoice.id
                )
        assert result.status == RefundStatus.REJECTED

    async def test_stripe_charge_overrides_the_webhook_charge_id(self):
        invoice = _invoice(amount=Decimal("20.00"))
        svc = _refund_service()
        svc.inv_repo.get = AsyncMock(return_value=invoice)
        with patch(
            "app.services.StripeClient.retrieve_refund",
            return_value={"charge": "ch_authoritative", "amount": 500, "currency": "USD"},
        ):
            result = await svc.process_refund(
                "re_1", "ch_from_webhook", Decimal("5.00"), "USD", invoice_id=invoice.id
            )
        # Stripe's record wins over the webhook payload's charge id.
        assert result.charge_id == "ch_authoritative"

    async def test_stripe_payment_intent_overrides_the_webhook_value(self):
        purchase = _purchase()
        invoice = _invoice(amount=Decimal("20.00"))
        svc = _refund_service()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(return_value=purchase)
        svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
        with patch(
            "app.services.StripeClient.retrieve_refund",
            return_value={
                "charge": "ch_1",
                "payment_intent": "pi_authoritative",
                "amount": 500,
                "currency": "USD",
            },
        ):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_1", "ch_1", Decimal("5.00"), "USD", payment_intent_id="pi_webhook"
                )
        # Stripe's record wins over the webhook payload's payment intent id.
        svc.purchase_repo.get_by_stripe_payment_intent_id.assert_any_await("pi_authoritative")
        assert result.status == RefundStatus.PROCESSED

    async def test_an_unresolvable_local_invoice_id_is_read_exactly_once(self):
        # A supplied local id that no longer resolves is read once, then the
        # stripe_invoice_id cascade takes over. (The second read at
        # services.py:518-521 is unreachable: that block requires
        # resolved_invoice_id non-None while resolved_invoice is None, and the
        # two are only ever assigned together — see the report.)
        inv_id = uuid4()
        svc = _refund_service()
        svc.inv_repo.get = AsyncMock(return_value=None)
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_1", "ch_1", Decimal("5.00"), "USD", invoice_id=inv_id
                )
        assert svc.inv_repo.get.await_count == 1
        assert result.status == RefundStatus.PENDING_REVIEW

    async def test_a_failing_invoice_lookup_via_the_charge_is_survived(self):
        # The charge's `invoice` field is consulted when nothing else resolves;
        # a database error there must degrade, not propagate.
        svc = _refund_service()
        svc.inv_repo.get_by_stripe_invoice_id = AsyncMock(side_effect=RuntimeError("db down"))
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch(
                "app.services.StripeClient.retrieve_charge",
                return_value={"invoice": "in_charge", "payment_intent": None},
            ):
                result = await svc.process_refund("re_1", "ch_1", Decimal("5.00"), "USD")
        assert result.status == RefundStatus.PENDING_REVIEW

    async def test_a_failing_purchase_lookup_via_the_charge_payment_intent_is_survived(self):
        svc = _refund_service()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(side_effect=RuntimeError("db"))
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch(
                "app.services.StripeClient.retrieve_charge",
                return_value={"invoice": None, "payment_intent": "pi_from_charge"},
            ):
                result = await svc.process_refund("re_1", "ch_1", Decimal("5.00"), "USD")
        assert result.status == RefundStatus.PENDING_REVIEW

    async def test_the_payment_intent_cascade_is_retried_after_a_transient_failure(self):
        # The cascade attempts the same payment-intent lookup twice: once before
        # the charge lookup and once after. A transient failure on the first
        # attempt must still be recovered by the second.
        purchase = _purchase()
        invoice = _invoice(amount=Decimal("20.00"))
        svc = _refund_service()
        svc.purchase_repo.get_by_stripe_payment_intent_id = AsyncMock(
            side_effect=[RuntimeError("transient"), purchase]
        )
        svc.inv_repo.get_by_purchase_id = AsyncMock(return_value=invoice)
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_1", "", Decimal("5.00"), "USD", payment_intent_id="pi_1"
                )
        assert svc.purchase_repo.get_by_stripe_payment_intent_id.await_count == 2
        assert result.status == RefundStatus.PROCESSED

    async def test_refund_metadata_is_persisted(self):
        invoice = _invoice(amount=Decimal("20.00"))
        user, reason = uuid4(), "fraudulent"
        svc = _refund_service()
        svc.inv_repo.get = AsyncMock(return_value=invoice)
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_1",
                    "ch_1",
                    Decimal("5.00"),
                    "USD",
                    invoice_id=invoice.id,
                    user_id=user,
                    reason=reason,
                )
        assert result.user_id == user
        assert result.reason == reason


class TestProcessRefundDeadBranch:
    """services.py:518-521 is unreachable, and this proves it at runtime.

    The block reads::

        if resolved_invoice_id is None or resolved_invoice is None:
            if resolved_invoice_id is not None:            # 517
                resolved_invoice = await inv_repo.get(...) # 519

    It needs ``resolved_invoice_id`` non-None *while* ``resolved_invoice`` is
    None. The two are only ever set together: the single read at line 450 is
    immediately followed by ``if resolved_invoice is None: resolved_invoice_id =
    None`` (451-452), and every cascade branch (455-515) assigns
    ``resolved_invoice = inv`` and ``resolved_invoice_id = inv.id`` as an
    adjacent pair under the same ``if inv:``.

    Rather than assert that on the source text, each test below drives one
    resolver path and asserts the *observable* invariant that makes the block
    dead: a refund is either applied against a real, readable invoice
    (PROCESSED/REJECTED with that invoice's id) or filed with ``invoice_id=None``
    for review. There is no third outcome, so the reload never runs.
    """

    async def _resolve_and_assert(
        self,
        *,
        invoice_id=None,
        stripe_invoice_id=None,
        payment_intent_id=None,
        charge_id=None,
        by_stripe_invoice_id=None,
        by_purchase_id=None,
        purchase=None,
        charge=None,
        expect_processed: bool,
    ) -> MagicMock:
        svc = _refund_service(
            **{
                "inv_repo.get": AsyncMock(return_value=None),
                "inv_repo.get_by_stripe_invoice_id": AsyncMock(return_value=by_stripe_invoice_id),
                "inv_repo.get_by_purchase_id": AsyncMock(return_value=by_purchase_id),
                "purchase_repo.get_by_stripe_payment_intent_id": AsyncMock(return_value=purchase),
            }
        )
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch(
                "app.services.StripeClient.retrieve_charge", return_value=charge or {}
            ):
                result = await svc.process_refund(
                    "re_dead",
                    charge_id or "",
                    Decimal("5.00"),
                    "USD",
                    invoice_id=invoice_id,
                    stripe_invoice_id=stripe_invoice_id,
                    payment_intent_id=payment_intent_id,
                )
        if expect_processed:
            assert result.status == RefundStatus.PROCESSED
            assert result.invoice_id is not None
            svc.refund_repo.apply_to_invoice.assert_awaited_once()
        else:
            # Unresolved invoice => reviewed by hand, never auto-applied.
            assert result.status == RefundStatus.PENDING_REVIEW
            assert result.invoice_id is None
            svc.refund_repo.apply_to_invoice.assert_not_awaited()
        return result

    async def test_resolved_via_a_supplied_local_invoice_id(self):
        inv = _invoice(amount=Decimal("20.00"))
        svc = _refund_service(**{"inv_repo.get": AsyncMock(return_value=inv)})
        with patch("app.services.StripeClient.retrieve_refund", return_value={}):
            with patch("app.services.StripeClient.retrieve_charge", return_value={}):
                result = await svc.process_refund(
                    "re_dead", "ch_1", Decimal("5.00"), "USD", invoice_id=inv.id
                )
        assert result.status == RefundStatus.PROCESSED
        assert result.invoice_id == inv.id

    async def test_unreadable_local_invoice_id_falls_through_to_pending_review(self):
        await self._resolve_and_assert(invoice_id=uuid4(), expect_processed=False)

    async def test_resolved_via_the_stripe_invoice_id(self):
        inv = _invoice(amount=Decimal("20.00"))
        await self._resolve_and_assert(
            stripe_invoice_id="in_1", by_stripe_invoice_id=inv, expect_processed=True
        )

    async def test_unresolvable_stripe_invoice_id_falls_through(self):
        await self._resolve_and_assert(stripe_invoice_id="in_1", expect_processed=False)

    async def test_resolved_via_the_payment_intent(self):
        purchase = _purchase()
        inv = _invoice(amount=Decimal("20.00"))
        await self._resolve_and_assert(
            payment_intent_id="pi_1", purchase=purchase, by_purchase_id=inv, expect_processed=True
        )

    async def test_unresolvable_payment_intent_falls_through(self):
        await self._resolve_and_assert(payment_intent_id="pi_1", expect_processed=False)

    async def test_resolved_via_the_charge_stripe_invoice_field(self):
        inv = _invoice(amount=Decimal("20.00"))
        await self._resolve_and_assert(
            charge_id="ch_1",
            charge={"invoice": "in_charge", "payment_intent": None},
            by_stripe_invoice_id=inv,
            expect_processed=True,
        )

    async def test_resolved_via_the_charge_payment_intent_field(self):
        purchase = _purchase()
        inv = _invoice(amount=Decimal("20.00"))
        await self._resolve_and_assert(
            charge_id="ch_1",
            charge={"invoice": None, "payment_intent": "pi_c"},
            purchase=purchase,
            by_purchase_id=inv,
            expect_processed=True,
        )

    async def test_charge_with_no_resolvable_invoice_falls_through(self):
        await self._resolve_and_assert(
            charge_id="ch_1", charge={"invoice": None, "payment_intent": None},
            expect_processed=False,
        )

    async def test_no_resolver_input_at_all_falls_through(self):
        await self._resolve_and_assert(expect_processed=False)

    async def test_a_purchase_with_no_invoice_falls_through(self):
        # get_by_purchase_id returns None: the purchase exists but the invoice
        # does not, so nothing may be applied.
        await self._resolve_and_assert(
            payment_intent_id="pi_1", purchase=_purchase(), expect_processed=False
        )
