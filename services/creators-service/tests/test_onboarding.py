"""Tests for creators-service onboarding and payout."""

from uuid import uuid4

from app.models import CreatorOnboarding, CreatorPayout
from app.schemas.onboarding import OnboardingCreate
from app.schemas.payout import PayoutCreate

def test_onboarding_create():
    data = OnboardingCreate(user_id=uuid4(), kyc_type="individual")
    assert data.kyc_type == "individual"

def test_payout_create():
    data = PayoutCreate(creator_id=uuid4(), amount_cents=10000)
    assert data.amount_cents == 10000
    assert data.currency == "USD"

def test_onboarding_model():
    rec = CreatorOnboarding(user_id=uuid4(), kyc_type="entity")
    assert rec.kyc_type == "entity"

def test_payout_model():
    rec = CreatorPayout(creator_id=uuid4(), amount_cents=5000, currency="EUR")
    assert rec.currency == "EUR"
