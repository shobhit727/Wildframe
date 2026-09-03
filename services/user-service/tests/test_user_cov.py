"""User 80% coverage."""
from datetime import UTC, datetime
from uuid import uuid4
from app.models.privacy import UserConsentRecord
from app.models.dsar import DSARRequest
from app.models.child_account import ChildAccount

def test_consent_types():
    for t in ["marketing","analytics","profiling","cookies"]:
        c = UserConsentRecord(user_id=uuid4(), consent_type=t, jurisdiction="EU", granted=True, version="1.0.0")
        assert c.consent_type == t

def test_dsar_types():
    for rt in ["access","portability","deletion"]:
        r = DSARRequest(user_id=uuid4(), request_type=rt, status="pending", data_categories="[]", sla_deadline=datetime.now(UTC))
        assert r.request_type == rt

def test_child_guardian():
    c = ChildAccount(child_user_id=uuid4(), parent_user_id=uuid4(), relationship="guardian")
    assert c.relationship == "guardian"
