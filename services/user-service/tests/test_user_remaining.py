"""Remaining user tests."""
from uuid import uuid4
def test_user_remaining1():
    from app.models.privacy import UserConsentRecord
    assert UserConsentRecord is not None
def test_user_remaining2():
    from app.models.dsar import DSARRequest
    assert DSARRequest is not None
def test_user_remaining3():
    from app.models.child_account import ChildAccount
    assert ChildAccount is not None
def test_user_remaining4():
    from app.schemas.privacy import ConsentRecordCreate
    assert ConsentRecordCreate is not None
def test_user_remaining5():
    from app.schemas.dsar import DSARCreateRequest
    assert DSARCreateRequest is not None
