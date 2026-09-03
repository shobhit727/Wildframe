"""Tests for EU."""

from app.models.eu_compliance import EUCompliance
from app.schemas.eu import EUCreate


def test_eu_create():
    data = EUCreate()
    assert data.avms_enabled is True


def test_eu_model():
    rec = EUCompliance()
    assert rec.avms_enabled is True
