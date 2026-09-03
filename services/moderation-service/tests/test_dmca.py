"""Tests for moderation dmca."""
from uuid import uuid4
from app.models.dmca import DMCATakedown
from app.schemas.dmca import TakedownCreate, CounterNoticeCreate
def test_takedown_create():
    data = TakedownCreate(content_id=uuid4(), reporter_email="a@b.com", reason="copy")
    assert data.reason == "copy"
def test_dmca_model():
    rec = DMCATakedown(content_id=uuid4(), reporter_email="a@b.com", reason="test", status="pending")
    assert rec.status == "pending"
