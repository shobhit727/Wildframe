"""Tests for audit."""
from app.models.audit import BillingAudit
def test_audit_model():
    rec = BillingAudit(event_type="payment")
    assert rec.event_type == "payment"
def test_audit_encrypted():
    rec = BillingAudit(event_type="refund")
    assert rec.encrypted is True
