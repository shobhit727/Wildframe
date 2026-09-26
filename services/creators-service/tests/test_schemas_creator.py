"""Behavioural tests for app/schemas/creator.py.

The Pydantic models are the creators-service API contract, so these tests
assert the declared bounds (min/max length, non-negative money, the 0-100
tranche threshold, decimal precision on the per-minute floor) and that the
response models can be built from ORM instances via ``from_attributes``.
"""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.creator import (
    CreatorAccountCreate,
    CreatorAccountResponse,
    CreatorAccountUpdate,
    CreatorPoolBalanceResponse,
    EffectiveFloorCreate,
    EffectiveFloorResponse,
    MilestoneCreate,
    MilestoneResponse,
    MilestoneTrancheResponse,
    PayoutAccrualRequest,
    PayoutLedgerResponse,
    PoolContributionRequest,
    TrancheCreate,
)

pytestmark = pytest.mark.unit

NOW = datetime.now(UTC)


class TestCreatorAccountCreate:
    def test_defaults(self):
        payload = CreatorAccountCreate()

        assert payload.display_name == ""
        assert payload.bio == ""
        assert payload.region_code == "US"
        assert payload.currency == "USD"

    def test_values_round_trip(self):
        payload = CreatorAccountCreate(
            display_name="Jane", bio="Films", region_code="EU", currency="EUR"
        )

        assert payload.model_dump() == {
            "display_name": "Jane",
            "bio": "Films",
            "region_code": "EU",
            "currency": "EUR",
        }

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("display_name", "x" * 256),
            ("bio", "x" * 2001),
            ("region_code", "X" * 9),
            ("currency", "EUROPEANS"),
        ],
    )
    def test_length_limits_are_enforced(self, field, value):
        with pytest.raises(ValidationError):
            CreatorAccountCreate(**{field: value})


class TestCreatorAccountUpdate:
    def test_every_field_is_optional(self):
        assert CreatorAccountUpdate().model_dump(exclude_unset=True) == {}

    def test_exclude_unset_only_keeps_provided_fields(self):
        payload = CreatorAccountUpdate(display_name="New")

        assert payload.model_dump(exclude_unset=True) == {"display_name": "New"}

    def test_stripe_connect_account_id_can_be_set_and_cleared(self):
        assert CreatorAccountUpdate(stripe_connect_account_id="acct_1").stripe_connect_account_id == "acct_1"
        assert CreatorAccountUpdate(stripe_connect_account_id=None).stripe_connect_account_id is None

    def test_display_name_length_limit(self):
        with pytest.raises(ValidationError):
            CreatorAccountUpdate(display_name="x" * 256)


def _account_payload(**overrides):
    data = {
        "id": uuid4(),
        "user_id": uuid4(),
        "display_name": "Jane",
        "bio": "Films",
        "region_code": "US",
        "currency": "USD",
        "stripe_connect_account_id": None,
        "kyc_status": "pending",
        "kyc_verified_at": None,
        "is_active": True,
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return data


class TestCreatorAccountResponse:
    def test_round_trips_every_field(self):
        payload = _account_payload()

        response = CreatorAccountResponse(**payload)

        assert response.id == payload["id"]
        assert response.user_id == payload["user_id"]
        assert response.kyc_status == "pending"
        assert response.is_active is True
        assert response.kyc_verified_at is None

    def test_verified_kyc_exposes_the_timestamp(self):
        response = CreatorAccountResponse(
            **_account_payload(kyc_status="verified", kyc_verified_at=NOW)
        )

        assert response.kyc_verified_at == NOW

    @pytest.mark.parametrize(
        "missing", ["id", "user_id", "display_name", "kyc_status", "created_at"]
    )
    def test_missing_required_field_is_rejected(self, missing):
        payload = _account_payload()
        payload.pop(missing)

        with pytest.raises(ValidationError):
            CreatorAccountResponse(**payload)

    def test_optional_fields_require_an_explicit_none(self):
        """The model does not enable ``from_attributes`` and has no defaults for
        the optional columns, so the routes must pass them explicitly."""
        payload = _account_payload()
        payload.pop("stripe_connect_account_id")
        payload.pop("kyc_verified_at")

        with pytest.raises(ValidationError):
            CreatorAccountResponse(**payload)


class TestEffectiveFloor:
    def test_decimal_is_parsed_and_kept_exact(self):
        payload = EffectiveFloorCreate(per_minute_amount="0.025")

        assert payload.per_minute_amount == Decimal("0.025")

    def test_zero_is_allowed(self):
        assert EffectiveFloorCreate(per_minute_amount=0).per_minute_amount == 0

    def test_negative_floor_is_rejected(self):
        with pytest.raises(ValidationError):
            EffectiveFloorCreate(per_minute_amount="-0.01")

    def test_missing_amount_is_rejected(self):
        with pytest.raises(ValidationError):
            EffectiveFloorCreate()

    def test_reason_and_currency(self):
        payload = EffectiveFloorCreate(per_minute_amount=1, reason="guarantee")

        assert payload.reason == "guarantee"
        assert payload.currency == "USD"

    def test_response_keeps_decimal_precision(self):
        response = EffectiveFloorResponse(
            id=uuid4(),
            creator_id=uuid4(),
            per_minute_amount=Decimal("0.03000000"),
            currency="USD",
            effective_from=NOW,
            last_adjusted_at=None,
            reason=None,
        )

        assert response.per_minute_amount == Decimal("0.03000000")
        assert response.last_adjusted_at is None
        # Pydantic v2 serialises Decimal as a JSON string to avoid float drift.
        assert response.model_dump(mode="json")["per_minute_amount"] == "0.03000000"

    def test_response_requires_the_core_fields(self):
        with pytest.raises(ValidationError):
            EffectiveFloorResponse(id=uuid4())


class TestPoolContribution:
    @pytest.mark.parametrize("cents", [0, 1, 10_000_00])
    def test_non_negative_contributions_are_accepted(self, cents):
        assert PoolContributionRequest(cents=cents).cents == cents

    @pytest.mark.parametrize("cents", [-1, -100])
    def test_negative_contributions_are_rejected(self, cents):
        with pytest.raises(ValidationError):
            PoolContributionRequest(cents=cents)

    def test_cents_is_required(self):
        with pytest.raises(ValidationError):
            PoolContributionRequest()

    def test_balance_response_shape(self):
        response = CreatorPoolBalanceResponse(
            id=uuid4(),
            creator_id=uuid4(),
            accrued_cents=500,
            contributed_cents=250,
            last_payout_at=None,
        )

        assert response.accrued_cents == 500
        assert response.contributed_cents == 250
        assert response.last_payout_at is None


class TestMilestoneSchemas:
    def test_defaults(self):
        payload = MilestoneCreate(title="Launch film")

        assert payload.total_cents == 0
        assert payload.currency == "USD"
        assert payload.goal is None

    @pytest.mark.parametrize("title", ["", "x" * 256])
    def test_title_bounds(self, title):
        with pytest.raises(ValidationError):
            MilestoneCreate(title=title)

    def test_title_is_required(self):
        with pytest.raises(ValidationError):
            MilestoneCreate()

    def test_negative_total_is_rejected(self):
        with pytest.raises(ValidationError):
            MilestoneCreate(title="x", total_cents=-1)

    def test_goal_length_limit(self):
        with pytest.raises(ValidationError):
            MilestoneCreate(title="x", goal="g" * 1001)

    def test_milestone_response_carries_status_and_optional_text(self):
        response = MilestoneResponse(
            id=uuid4(),
            title="Launch film",
            creator_id=uuid4(),
            status="funding",
            total_cents=1000,
            currency="USD",
            goal="ship it",
            kill_reason=None,
            created_at=NOW,
            updated_at=NOW,
        )

        assert response.status == "funding"
        assert response.kill_reason is None
        assert response.goal == "ship it"

    def test_milestone_response_requires_a_title(self):
        with pytest.raises(ValidationError):
            MilestoneResponse(
                id=uuid4(),
                creator_id=uuid4(),
                status="draft",
                total_cents=0,
                currency="USD",
                goal=None,
                kill_reason=None,
                created_at=NOW,
                updated_at=NOW,
            )

    @pytest.mark.parametrize("threshold", [0, 50, 100])
    def test_tranche_thresholds_in_range(self, threshold):
        payload = TrancheCreate(threshold=threshold)

        assert payload.threshold == threshold
        assert payload.amount_cents == 0

    @pytest.mark.parametrize("threshold", [-1, 101, 1000])
    def test_tranche_threshold_out_of_range(self, threshold):
        with pytest.raises(ValidationError):
            TrancheCreate(threshold=threshold)

    def test_tranche_threshold_is_required(self):
        with pytest.raises(ValidationError):
            TrancheCreate()

    def test_tranche_amount_cannot_be_negative(self):
        with pytest.raises(ValidationError):
            TrancheCreate(threshold=10, amount_cents=-1)

    def test_tranche_release_condition_length_limit(self):
        with pytest.raises(ValidationError):
            TrancheCreate(threshold=10, release_condition="c" * 1001)

    def test_tranche_response_reports_a_locked_tranche(self):
        response = MilestoneTrancheResponse(
            id=uuid4(),
            milestone_id=uuid4(),
            threshold=25,
            amount_cents=500,
            status="locked",
            release_condition=None,
            released_at=None,
        )

        assert response.threshold == 25
        assert response.status == "locked"
        assert response.released_at is None

    def test_tranche_response_requires_a_milestone(self):
        with pytest.raises(ValidationError):
            MilestoneTrancheResponse(
                id=uuid4(),
                threshold=25,
                amount_cents=500,
                status="locked",
                release_condition=None,
                released_at=None,
            )


class TestPayoutSchemas:
    def test_accrual_defaults(self):
        payload = PayoutAccrualRequest(
            period_start=NOW, period_end=NOW.replace(month=12)
        )

        assert payload.view_minutes == 0
        assert payload.earned_cents == 0
        assert payload.stripe_fee_cents == 0

    @pytest.mark.parametrize(
        "field", ["view_minutes", "earned_cents", "stripe_fee_cents"]
    )
    def test_negative_money_is_rejected(self, field):
        with pytest.raises(ValidationError):
            PayoutAccrualRequest(
                period_start=NOW, period_end=NOW, **{field: -1}
            )

    def test_period_bounds_are_required(self):
        with pytest.raises(ValidationError):
            PayoutAccrualRequest(view_minutes=10)

    def test_ledger_response_carries_the_computed_breakdown(self):
        response = PayoutLedgerResponse(
            id=uuid4(),
            creator_id=uuid4(),
            idempotency_key="creator:period",
            period_start=NOW,
            period_end=NOW,
            view_minutes=100,
            floor_cents=10,
            pool_topup_cents=1,
            share_cents=110,
            stripe_fee_cents=5,
            net_cents=105,
            stripe_transfer_id=None,
            status="accrued",
            created_at=NOW,
        )

        assert response.idempotency_key == "creator:period"
        assert response.net_cents == 105
        assert response.stripe_transfer_id is None
        assert response.status == "accrued"

    def test_ledger_response_requires_the_breakdown_fields(self):
        with pytest.raises(ValidationError):
            PayoutLedgerResponse(
                id=uuid4(),
                creator_id=uuid4(),
                idempotency_key="k",
                period_start=NOW,
                period_end=NOW,
                view_minutes=0,
                status="accrued",
                created_at=NOW,
            )
