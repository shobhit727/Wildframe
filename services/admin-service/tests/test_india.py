"""Tests for India."""

from app.models.india import IndiaCompliance
from app.schemas.india import IndiaCreate


def test_india_create():
    data = IndiaCreate(grievance_officer="officer@wildframe.com")
    assert data.grievance_officer == "officer@wildframe.com"


def test_india_model():
    rec = IndiaCompliance(grievance_officer="test@wildframe.com")
    assert rec.grievance_officer == "test@wildframe.com"
