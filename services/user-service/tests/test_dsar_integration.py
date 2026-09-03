"""Integration tests for user-service DSAR."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from app.models.dsar import DSARRequest

def test_dsar_create_with_sla():
    dsar = DSARRequest(user_id=uuid4(), request_type="access", status="pending", data_categories="profile", sla_deadline=datetime.now(UTC) + timedelta(days=30))
    assert dsar.status == "pending"
    assert dsar.sla_deadline is not None

def test_dsar_get_by_id():
    dsar = DSARRequest(user_id=uuid4(), request_type="portability", status="pending", data_categories="profile", sla_deadline=datetime.now(UTC))
    assert dsar.request_type == "portability"

def test_child_account_flow():
    from app.models.child_account import ChildAccount
    rec = ChildAccount(child_user_id=uuid4(), parent_user_id=uuid4(), relationship="parent")
    assert rec.relationship == "parent"
