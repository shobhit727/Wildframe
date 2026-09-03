"""Extra streaming tests."""
from uuid import uuid4
from app.models.maturity import ContentMaturity
from app.models.drm import DRMConfig
def test_streaming_extra1():
    assert ContentMaturity(content_id=uuid4(), maturity_rating="PG", min_age=7) is not None
def test_streaming_extra2():
    assert DRMConfig(content_id=uuid4()) is not None
def test_streaming_extra3():
    from app.schemas.maturity import MaturityCreate
    assert MaturityCreate(content_id=uuid4(), maturity_rating="R", min_age=18) is not None
