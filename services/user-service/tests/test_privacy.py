"""Tests for user-service privacy - consent and child."""

from uuid import uuid4

from app.models.privacy import UserConsentRecord
from app.schemas.privacy import ConsentRecordCreate
from app.models.child_account import ChildAccount
from app.schemas.child import ChildAccountCreate


def test_user_consent_create():
    data = ConsentRecordCreate(
        user_id=uuid4(),
        consent_type="marketing",
        jurisdiction="EU",
        granted=True,
        version="1.0.0",
    )
    assert data.granted is True


def test_user_consent_model():
    rec = UserConsentRecord(
        user_id=uuid4(), consent_type="analytics", jurisdiction="US", granted=True, version="1.0.0"
    )
    assert rec.consent_type == "analytics"


def test_child_account_create():
    data = ChildAccountCreate(child_user_id=uuid4(), parent_user_id=uuid4())
    assert data.relationship == "parent"


def test_child_model():
    rec = ChildAccount(child_user_id=uuid4(), parent_user_id=uuid4(), relationship="parent")
    assert rec.relationship == "parent"
