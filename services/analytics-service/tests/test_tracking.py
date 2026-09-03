"""Tests for tracking."""
from uuid import uuid4
from app.models.tracking import TrackingConsent
from app.schemas.tracking import TrackingCreate
def test_tracking_create():
    data = TrackingCreate(user_id=uuid4())
    assert data.cookie_consent == "essential"
def test_tracking_model():
    rec = TrackingConsent(user_id=uuid4(), cookie_consent="essential")
    assert rec.cookie_consent == "essential"
