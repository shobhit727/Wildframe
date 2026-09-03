"""Tests for streaming-service maturity and drm."""

from uuid import uuid4

from app.models.maturity import ContentMaturity
from app.models.drm import DRMConfig
from app.schemas.maturity import MaturityCreate
from app.schemas.drm import DRMCreate


def test_maturity_create():
    data = MaturityCreate(content_id=uuid4(), maturity_rating="PG", min_age=7)
    assert data.maturity_rating == "PG"
    assert data.min_age == 7


def test_drm_create():
    data = DRMCreate(content_id=uuid4())
    assert data.device_limit == 3
    assert data.expiry_hours == 48


def test_maturity_model():
    rec = ContentMaturity(content_id=uuid4(), maturity_rating="R", min_age=18)
    assert rec.maturity_rating == "R"


def test_drm_model():
    rec = DRMConfig(content_id=uuid4(), fairplay_enabled=True, widevine_enabled=True)
    assert rec.fairplay_enabled is True
