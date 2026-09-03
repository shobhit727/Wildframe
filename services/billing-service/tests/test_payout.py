"""Tests for payout ledger."""

from uuid import uuid4
from app.models.payout_ledger import PayoutLedger


def test_ledger_model():
    rec = PayoutLedger(
        payout_id=uuid4(), creator_id=uuid4(), gross_cents=10000, tax_cents=2000, net_cents=8000
    )
    assert rec.gross_cents == 10000


def test_ledger_reconciled():
    rec = PayoutLedger(
        payout_id=uuid4(),
        creator_id=uuid4(),
        gross_cents=1000,
        tax_cents=200,
        net_cents=800,
        reconciled=False,
    )
    assert rec.reconciled is False
