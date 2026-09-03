"""Final moderation tests."""
from uuid import uuid4
from app.models.dmca import DMCATakedown
def test_moderation_final1():
    assert DMCATakedown(content_id=uuid4(), reporter_email="a@b.com", reason="x") is not None
def test_moderation_final2():
    from app.schemas.dmca import TakedownCreate
    assert TakedownCreate(content_id=uuid4(), reporter_email="a@b.com", reason="y") is not None
