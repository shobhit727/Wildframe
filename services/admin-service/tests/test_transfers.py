"""Tests for transfers."""

from app.models.transfers import TransferRecord
from app.schemas.transfers import TransferCreate


def test_transfer_create():
    data = TransferCreate(source_region="EU", target_region="US")
    assert data.source_region == "EU"


def test_transfer_model():
    rec = TransferRecord(source_region="EU", target_region="US")
    assert rec.source_region == "EU"
