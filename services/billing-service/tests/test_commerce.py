"""Tests for commerce."""

from app.models.commerce import CommerceRecord
from app.schemas.commerce import CommerceCreate


def test_commerce_create():
    data = CommerceCreate(invoice_id="INV-1", amount_cents=1000, tax_cents=200)
    assert data.invoice_id == "INV-1"


def test_commerce_model():
    rec = CommerceRecord(invoice_id="INV-1", amount_cents=1000, tax_cents=200, currency="USD")
    assert rec.invoice_id == "INV-1"
