"""Integration tests for auth-service privacy - DB-backed."""

import pytest
from datetime import datetime, UTC

from app.models.privacy import PrivacyNotice
from app.repositories.privacy_repository import PrivacyNoticeRepository


@pytest.mark.asyncio
async def test_create_and_get_current_notice(db_session):
    repo = PrivacyNoticeRepository(db_session)
    notice = PrivacyNotice(
        version="1.0.1",
        jurisdiction="EU",
        title="Test EU Notice",
        content="Content",
        language="en",
        effective_date=datetime.now(UTC),
        notice_metadata='{"test": 1}',
    )
    created = await repo.create(notice)
    await db_session.commit()
    fetched = await repo.get_by_version_jurisdiction_language("1.0.1", "EU", "en")
    assert fetched is not None
    assert fetched.title == "Test EU Notice"


@pytest.mark.asyncio
async def test_set_current_deprecates_old(db_session):
    repo = PrivacyNoticeRepository(db_session)
    n1 = PrivacyNotice(version="1.0.2", jurisdiction="US", title="US Old", content="old", language="en", effective_date=datetime.now(UTC))
    n2 = PrivacyNotice(version="1.0.3", jurisdiction="US", title="US New", content="new", language="en", effective_date=datetime.now(UTC))
    await repo.create(n1)
    await repo.create(n2)
    await repo.set_current(n1)
    await db_session.commit()
    await repo.set_current(n2)
    await db_session.commit()
    current = await repo.get_current("US", "en")
    assert current.version == "1.0.3"
    assert current.is_current is True


@pytest.mark.asyncio
async def test_consent_grant_withdraw(db_session):
    from app.repositories.privacy_repository import ConsentRecordRepository
    from app.models.privacy import ConsentRecord
    from uuid import uuid4
    repo = ConsentRecordRepository(db_session)
    consent = ConsentRecord(user_id=uuid4(), consent_type="marketing", jurisdiction="EU", granted=True, version="1.0.0")
    created = await repo.create(consent)
    await db_session.commit()
    await repo.withdraw(created, "no longer needed")
    await db_session.commit()
    assert created.granted is False
    assert created.withdrawn_at is not None
