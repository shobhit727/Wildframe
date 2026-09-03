"""Auth privacy 80% coverage."""
import pytest
from datetime import UTC, datetime
from uuid import uuid4
from app.models.privacy import PrivacyNotice, ConsentRecord
from app.schemas import PrivacyNoticeCreate

def test_notice_version_validation():
    data = PrivacyNoticeCreate(version="2.1.3", jurisdiction="US", title="T", content="C", language="en", effective_date=datetime.now(UTC))
    assert data.version == "2.1.3"

def test_notice_jurisdiction_in():
    n = PrivacyNotice(version="1.0.0", jurisdiction="IN", title="IN", content="C", language="en", effective_date=datetime.now(UTC))
    assert n.jurisdiction == "IN"

def test_consent_granted_false():
    c = ConsentRecord(user_id=uuid4(), consent_type="cookies", jurisdiction="GLOBAL", granted=False, version="1.0.0")
    assert c.granted is False

def test_consent_metadata():
    c = ConsentRecord(user_id=uuid4(), consent_type="profiling", jurisdiction="EU", granted=True, version="1.0.0", consent_metadata='{"a":1}')
    assert "a" in c.consent_metadata

def test_privacy_notice_is_current():
    n = PrivacyNotice(version="1.0.0", jurisdiction="EU", title="T", content="C", language="en", effective_date=datetime.now(UTC), is_current=True)
    assert n.is_current is True
