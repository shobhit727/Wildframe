"""Tests for processors."""

from app.models.processors import Processor
from app.schemas.processors import ProcessorCreate


def test_processor_create():
    data = ProcessorCreate(name="Vendor")
    assert data.name == "Vendor"


def test_processor_model():
    rec = Processor(name="Test")
    assert rec.name == "Test"
