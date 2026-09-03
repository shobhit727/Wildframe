"""Tests for auth-service privacy - notices and consent."""

from datetime import datetime, UTC
from uuid import uuid4

from app.models.privacy import PrivacyNotice, ConsentRecord
from app.schemas import PrivacyNoticeCreate, ConsentRecordCreate


def test_privacy_notice_create_schema():
    data = PrivacyNoticeCreate(
        version="1.0.0",
        jurisdiction="EU",
        title="EU Privacy",
        content="Test content",
        language="en",
        effective_date=datetime.now(UTC),
        notice_metadata='{"test": 1}',
    )
    assert data.version == "1.0.0"
    assert data.jurisdiction == "EU"


def test_privacy_notice_model():
    notice = PrivacyNotice(
        version="1.0.0",
        jurisdiction="EU",
        title="Test",
        content="Content",
        language="en",
        effective_date=datetime.now(UTC),
        notice_metadata='{"a":1}',
    )
    assert notice.version == "1.0.0"
    assert notice.jurisdiction == "EU"


def test_consent_create_schema():
    data = ConsentRecordCreate(
        user_id=uuid4(),
        consent_type="marketing",
        jurisdiction="EU",
        granted=True,
        version="1.0.0",
        consent_metadata='{"src":"test"}',
    )
    assert data.consent_type == "marketing"
    assert data.granted is True


def test_consent_model():
    consent = ConsentRecord(
        user_id=uuid4(),
        consent_type="analytics",
        jurisdiction="US",
        granted=True,
        version="1.0.0",
        consent_metadata="{}",
    )
    assert consent.consent_type == "analytics"


def test_age_verify_schema():
    from app.schemas.age import AgeVerifyRequest

    req = AgeVerifyRequest(user_id=uuid4(), declared_age=25, jurisdiction="EU")
    assert req.declared_age == 25
    assert req.jurisdiction == "EU"
