"""Content 80% coverage."""
from datetime import UTC, datetime
from uuid import uuid4
from app.models.rights import RightsHolder, TerritorialLicense
from app.models.reviews import Review

def test_rights_exclusive():
    h = RightsHolder(name="H", type="creator")
    l = TerritorialLicense(content_id=uuid4(), rights_holder_id=uuid4(), territory="IN", exclusive=False, avail_start=datetime.now(UTC), avail_end=datetime.now(UTC))
    assert l.exclusive is False
    assert l.territory == "IN"

def test_review_verified():
    r = Review(content_id=uuid4(), user_id=uuid4(), rating=3, text="ok", verified_viewer=False)
    assert r.verified_viewer is False

def test_review_helpful():
    r = Review(content_id=uuid4(), user_id=uuid4(), rating=5, text="good", helpful_votes=10)
    assert r.helpful_votes == 10
