"""Low-level billing coverage: Stripe adapter, JWT verifier, and repositories."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import JWTError

from app.core import jwt_verifier
from app.core.stripe_client import StripeClient, StripeError
from app.models import RefundStatus, RevenueTier
from app.repositories import (
    CreatorPoolRepository,
    InvoiceRepository,
    MilestoneRepository,
    PayoutLedgerRepository,
    PurchaseRepository,
    RefundRepository,
    RegionFloorRepository,
    StripeWebhookEventRepository,
    SubscriptionRepository,
)


class Result:
    def __init__(self, value=None, rows=None, rowcount=1):
        self.value = value
        self.rows = rows or []
        self.rowcount = rowcount

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return SimpleNamespace(all=lambda: self.rows)


def session():
    s = MagicMock()
    s.execute = AsyncMock(return_value=Result())
    s.flush = AsyncMock()
    s.rollback = AsyncMock()
    s.commit = AsyncMock()
    return s


def test_stripe_client_success_paths(monkeypatch):
    user = __import__("uuid").uuid4()
    content = __import__("uuid").uuid4()
    account = SimpleNamespace(id="acct_1")
    transfer = SimpleNamespace(id="tr_1")
    checkout = SimpleNamespace(id="cs_1")
    monkeypatch.setattr(StripeClient, "create_checkout_session", StripeClient.create_checkout_session)
    monkeypatch.setattr("app.core.stripe_client.stripe.checkout.Session.create", lambda **_: checkout)
    assert StripeClient.create_checkout_session(user, "price_1", "svod", "ok", "cancel").id == "cs_1"
    assert StripeClient.create_tvod_purchase_session(user, content, Decimal("4.99"), "ok", "cancel").id == "cs_1"
    monkeypatch.setattr("app.core.stripe_client.stripe.Account.create", lambda **_: account)
    assert StripeClient.create_connect_account(user, "US", "creator@example.com").id == "acct_1"
    monkeypatch.setattr("app.core.stripe_client.stripe.Transfer.create", lambda **_: transfer)
    assert StripeClient.transfer_to_creator("acct_1", Decimal("5.00"), "idem").id == "tr_1"


def test_stripe_client_failures(monkeypatch):
    from app.core import stripe_client

    def fail(**_):
        raise stripe_client._StripeError("boom")

    monkeypatch.setattr(stripe_client.stripe.checkout.Session, "create", fail)
    with pytest.raises(StripeError):
        StripeClient.create_checkout_session(__import__("uuid").uuid4(), "p", "svod", "a", "b")

    monkeypatch.setattr(stripe_client.stripe.Account, "create", fail)
    with pytest.raises(StripeError):
        StripeClient.create_connect_account(__import__("uuid").uuid4(), "US", "x@y")

    monkeypatch.setattr(stripe_client.stripe.Transfer, "create", fail)
    with pytest.raises(StripeError):
        StripeClient.transfer_to_creator("acct", Decimal("1"), "id")

    monkeypatch.setattr(stripe_client.settings, "STRIPE_WEBHOOK_SECRET", "")
    with pytest.raises(StripeError):
        StripeClient.handle_webhook(b"{}", "")


@pytest.mark.asyncio
async def test_jwt_verifier_rejects_and_caches(monkeypatch):
    jwt_verifier.clear_cache()
    with pytest.raises(JWTError):
        jwt_verifier.verify_with_jwks("not-a-token", {"keys": []})

    monkeypatch.setattr(jwt_verifier, "fetch_jwks", AsyncMock(return_value={"keys": []}))
    first = await jwt_verifier.get_cached_jwks("https://auth.example/jwks", ttl=300)
    second = await jwt_verifier.get_cached_jwks("https://auth.example/jwks", ttl=300)
    assert first == second
    jwt_verifier.fetch_jwks.assert_awaited_once()

    with pytest.raises(JWTError):
        jwt_verifier.verify_with_jwks("eyJhbGciOiJIUzI1NiJ9.e30.sig", {"keys": []})


@pytest.mark.asyncio
async def test_repository_crud_queries_and_writes():
    uid = __import__("uuid").uuid4()
    cid = __import__("uuid").uuid4()
    s = session()
    obj = MagicMock()
    s.execute.return_value = Result(obj)
    assert await SubscriptionRepository(s).get_by_user(uid) is obj
    sub = await SubscriptionRepository(s).create(uid, RevenueTier.SVOD, Decimal("7.99"))
    assert sub.tier == RevenueTier.SVOD
    await SubscriptionRepository(s).update_tier(uid, RevenueTier.AVOD, Decimal("0"))
    
    s.execute.return_value = Result(obj)
    pr = PurchaseRepository(s)
    assert await pr.get_by_user_and_content(uid, cid) is obj
    assert await pr.get_by_stripe_payment_intent_id("pi") is obj
    assert (await pr.create(uid, cid, Decimal("5"), "idem")).price == Decimal("5")

    inv = InvoiceRepository(s)
    assert await inv.get(uid) is obj
    assert await inv.get_by_stripe_invoice_id("in") is obj
    assert await inv.get_by_purchase_id(cid) is obj
    assert await inv.get_latest_for_user(uid) is obj
    assert await inv.get_by_user(uid) == [obj]
    assert (await inv.create(uid, Decimal("5"))).amount == Decimal("5")

    floor = RegionFloorRepository(s)
    assert await floor.get_by_region("US") is obj
    assert await floor.list_all() == [obj]

    pool = CreatorPoolRepository(s)
    assert await pool.get_latest() is obj
    entry = await pool.create_entry(datetime.now(UTC), datetime.now(UTC), Decimal("100"), Decimal("0.15"))
    assert entry.pool_amount == Decimal("15.00")
    s.execute.return_value = Result(rowcount=1)
    assert await pool.redistribute_pool(uid, Decimal("1")) is True

    milestone = MilestoneRepository(s)
    assert await milestone.get(uid) is obj
    created = await milestone.create(uid, "Project", Decimal("100.01"))
    assert created.project_title == "Project"
    assert s.add.call_count >= 5
    assert await milestone.get_tranches(uid) == [obj]

    payout = PayoutLedgerRepository(s)
    assert await payout.get_by_idempotency_key("k") is obj
    entry = await payout.accrue(uid, Decimal("5"), "USD", "new", datetime.now(UTC), datetime.now(UTC))
    assert entry.amount == Decimal("5")
    assert await payout.get_by_creator(uid) == [obj]

    refund = RefundRepository(s)
    assert await refund.get_by_refund_id("re") is obj
    created_refund = await refund.create("re2", Decimal("1"), "USD")
    assert created_refund.status == RefundStatus.PROCESSED
    assert await refund.apply_to_invoice(uid, Decimal("1")) is True


@pytest.mark.asyncio
async def test_webhook_repository_claim_replay_and_state_transitions():
    s = session()
    repo = StripeWebhookEventRepository(s)
    assert await repo.get("evt") is None
    assert await repo.claim("evt", "checkout") is True
    assert await repo.complete("evt") is True
    assert await repo.fail("evt", "error") is True
    await repo.commit()


    s.add.reset_mock()
    s.flush.side_effect = IntegrityError("stmt", {}, Exception("dup"))
    existing = SimpleNamespace(
        status="failed",
        attempts=1,
        claimed_at=None,
    )
    repo.get = AsyncMock(return_value=existing)
    s.execute.return_value = Result(rowcount=1)
    assert await repo.claim("evt", "checkout", max_attempts=3) is True


@pytest.mark.asyncio
async def test_refund_and_payout_duplicate_paths():
    from sqlalchemy.exc import IntegrityError

    uid = __import__("uuid").uuid4()
    s = session()
    existing = MagicMock()
    repo = RefundRepository(s)
    repo.get_by_refund_id = AsyncMock(return_value=existing)
    assert await repo.create("re", Decimal("1"), "USD") is existing

    payout = PayoutLedgerRepository(s)
    payout.get_by_idempotency_key = AsyncMock(return_value=existing)
    assert await payout.accrue(
        uid, Decimal("1"), "USD", "k", datetime.now(UTC), datetime.now(UTC)
    ) is existing
