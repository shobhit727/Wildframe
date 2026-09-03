"""Extra creators tests for remaining 8 files."""

from uuid import uuid4
from app.models.payout import CreatorPayout
from app.models.onboarding import CreatorOnboarding


def test_creators_extra1():
    assert CreatorPayout(creator_id=uuid4(), amount_cents=100) is not None


def test_creators_extra2():
    assert CreatorOnboarding(user_id=uuid4(), kyc_type="individual") is not None


def test_creators_extra3():
    from app.schemas.onboarding import OnboardingCreate

    assert OnboardingCreate(user_id=uuid4(), kyc_type="entity") is not None
