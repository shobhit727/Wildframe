"""Unit tests for repository layer."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from app.models import LoginAudit


class TestUserRepository:
    """Test user repository operations."""

    async def test_create_user(self, user_repository, password_manager):
        """Test creating a new user."""
        email = "newuser@example.com"
        password_hash = password_manager.hash_password("SecurePass123!")

        user = await user_repository.create(
            email=email,
            password_hash=password_hash,
            first_name="John",
            last_name="Doe",
        )
        await user_repository.commit()

        assert user.id is not None
        assert user.email == email
        assert user.password_hash == password_hash

    async def test_get_user_by_email(self, test_user, user_repository):
        """Test fetching user by email."""
        user = await user_repository.get_by_email(test_user.email)

        assert user is not None
        assert user.id == test_user.id
        assert user.email == test_user.email

    async def test_get_user_by_email_not_found(self, user_repository):
        """Test fetching non-existent user by email."""
        user = await user_repository.get_by_email("nonexistent@example.com")

        assert user is None

    async def test_get_user_by_id(self, test_user, user_repository):
        """Test fetching user by ID."""
        user = await user_repository.get_by_id(test_user.id)

        assert user is not None
        assert user.id == test_user.id

    async def test_update_user(self, test_user, user_repository):
        """Test updating user."""
        new_first_name = "Updated"

        user = await user_repository.update(
            test_user.id,
            first_name=new_first_name,
        )
        await user_repository.commit()

        assert user.first_name == new_first_name

    async def test_increment_login_attempts(self, test_user, user_repository):
        """Test incrementing login attempts."""
        initial_attempts = test_user.login_attempts

        user = await user_repository.increment_login_attempts(test_user.id)
        await user_repository.commit()

        assert user.login_attempts == initial_attempts + 1

    async def test_reset_login_attempts(self, test_user, user_repository):
        """Test resetting login attempts."""
        # First increment
        await user_repository.increment_login_attempts(test_user.id)
        await user_repository.commit()

        # Then reset
        user = await user_repository.reset_login_attempts(test_user.id)
        await user_repository.commit()

        assert user.login_attempts == 0
        assert user.locked_until is None


class TestRefreshTokenRepository:
    """Test refresh token repository operations."""

    async def test_create_refresh_token(self, test_user, token_repository):
        """Test creating refresh token."""
        token_hash = "token_hash_123"
        expires_at = datetime.now(UTC) + timedelta(days=7)

        token = await token_repository.create(
            user_id=test_user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        await token_repository.commit()

        assert token.id is not None
        assert token.user_id == test_user.id
        assert token.token_hash == token_hash

    async def test_get_refresh_token_by_hash(self, test_user, token_repository):
        """Test fetching refresh token by hash."""
        token_hash = "token_hash_123"
        expires_at = datetime.now(UTC) + timedelta(days=7)

        await token_repository.create(
            user_id=test_user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        await token_repository.commit()

        token = await token_repository.get_by_token_hash(token_hash)

        assert token is not None
        assert token.token_hash == token_hash

    async def test_get_refresh_token_by_hash_not_found(self, token_repository):
        """Test fetching non-existent refresh token."""
        token = await token_repository.get_by_token_hash("nonexistent_hash")

        assert token is None

    async def test_revoke_refresh_token(self, test_user, token_repository):
        """Test revoking refresh token."""
        token_hash = "token_hash_123"
        expires_at = datetime.now(UTC) + timedelta(days=7)

        await token_repository.create(
            user_id=test_user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        await token_repository.commit()

        success = await token_repository.revoke(token_hash)
        await token_repository.commit()

        assert success is True

        # Verify token is gone
        token = await token_repository.get_by_token_hash(token_hash)
        assert token is None

    async def test_delete_expired_tokens(self, test_user, token_repository):
        """Test deleting expired tokens."""
        # Create expired token
        expired_at = datetime.now(UTC) - timedelta(hours=1)
        await token_repository.create(
            user_id=test_user.id,
            token_hash="expired_hash",
            expires_at=expired_at,
        )

        # Create valid token
        valid_at = datetime.now(UTC) + timedelta(days=7)
        await token_repository.create(
            user_id=test_user.id,
            token_hash="valid_hash",
            expires_at=valid_at,
        )
        await token_repository.commit()

        # Delete expired
        count = await token_repository.delete_expired()
        await token_repository.commit()

        assert count >= 1


class TestLoginAuditRepository:
    """Test login audit repository operations."""

    async def test_create_audit_record(self, test_user, audit_repository):
        """Test creating login audit record."""
        status = "success"
        ip_address = "192.168.1.1"

        audit = await audit_repository.create(
            user_id=test_user.id,
            status=status,
            ip_address=ip_address,
        )
        await audit_repository.commit()

        assert audit.id is not None
        assert audit.user_id == test_user.id
        assert audit.status == status
        assert audit.ip_address == ip_address

    async def test_get_recent_failed_attempts(self, test_user, audit_repository):
        """Test counting recent failed attempts."""
        # Create failed attempts
        for i in range(3):
            await audit_repository.create(
                user_id=test_user.id,
                status="failed",
                ip_address="192.168.1.1",
            )

        await audit_repository.commit()

        count = await audit_repository.get_recent_failed_attempts(
            test_user.id,
            minutes=5,
        )

        assert count >= 3

    async def test_get_recent_failed_attempts_filtered_by_time(self, test_user, audit_repository):
        """Test that old attempts are not counted."""
        # Old failed attempt (more than 5 minutes ago)
        old_audit = LoginAudit(
            user_id=test_user.id,
            status="failed",
            ip_address="192.168.1.1",
            created_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        audit_repository.session.add(old_audit)
        await audit_repository.commit()

        count = await audit_repository.get_recent_failed_attempts(
            test_user.id,
            minutes=5,
        )

        assert count == 0


# ==========================================================================
# Error paths, the remaining queries, and the privacy repositories.
# ==========================================================================

from datetime import datetime as _dt
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.models import ConsentRecord, PrivacyNotice, RefreshToken, TokenBlacklist
from app.repositories import (
    ConsentRecordRepository,
    PrivacyNoticeRepository,
    TokenBlacklistRepository,
)
from app.repositories.privacy_repository import (
    ConsentRecordRepository as PrivacyConsentRecordRepository,
)


class TestUserRepositoryErrorPaths:
    async def test_create_rolls_back_and_reraises(self, user_repository):
        with patch.object(
            type(user_repository.session), "flush", AsyncMock(side_effect=RuntimeError("deadlock"))
        ):
            with pytest.raises(RuntimeError):
                await user_repository.create(
                    email="rollback@example.com", password_hash="hash"
                )

    async def test_update_unknown_user_returns_none(self, user_repository):
        assert await user_repository.update(uuid4(), first_name="X") is None

    async def test_update_rolls_back_and_reraises(self, test_user, user_repository):
        with patch.object(
            type(user_repository.session), "flush", AsyncMock(side_effect=RuntimeError("io"))
        ):
            with pytest.raises(RuntimeError):
                await user_repository.update(test_user.id, first_name="X")

    async def test_update_ignores_unknown_attributes(self, test_user, user_repository):
        user = await user_repository.update(
            test_user.id, first_name="Known", not_a_column="ignored"
        )

        assert user.first_name == "Known"
        assert not hasattr(user, "not_a_column")

    async def test_increment_login_attempts_for_missing_user_raises(self, user_repository):
        with pytest.raises(ValueError, match="not found"):
            await user_repository.increment_login_attempts(uuid4())

    async def test_reset_login_attempts_for_missing_user_raises(self, user_repository):
        with pytest.raises(ValueError, match="not found"):
            await user_repository.reset_login_attempts(uuid4())

    async def test_inactive_users_are_invisible_to_get_by_email(self, test_user, user_repository):
        test_user.is_active = False
        await user_repository.commit()

        assert await user_repository.get_by_email(test_user.email) is None

    async def test_email_lookup_is_case_insensitive(self, test_user, user_repository):
        assert await user_repository.get_by_email(test_user.email.upper()) is test_user

    async def test_base_repository_helpers(self, test_user, user_repository):
        user_repository.session.add(
            __import__("app.models", fromlist=["User"]).User(
                email="helpers@example.com", password_hash="h"
            )
        )
        await user_repository.flush()
        await user_repository.rollback()
        await user_repository.commit()

        assert await user_repository.get_by_email("helpers@example.com") is None


class TestRefreshTokenRepositoryRemaining:
    async def test_create_rolls_back_and_reraises(self, test_user, token_repository):
        with patch.object(
            type(token_repository.session),
            "flush",
            AsyncMock(side_effect=RuntimeError("constraint")),
        ):
            with pytest.raises(RuntimeError):
                await token_repository.create(
                    user_id=test_user.id, token_hash="h", expires_at=_dt.now(UTC)
                )

    async def test_get_by_user_id_returns_the_only_token(self, test_user, token_repository):
        only = await token_repository.create(
            user_id=test_user.id,
            token_hash="only",
            expires_at=_dt.now(UTC) + timedelta(days=1),
        )
        await token_repository.commit()

        assert (await token_repository.get_by_user_id(test_user.id)).id == only.id

    async def test_get_by_user_id_raises_when_the_user_has_several_tokens(
        self, test_user, token_repository
    ):
        """BUG: the docstring says "latest", but the query never limits.

        ``get_by_user_id`` orders by ``created_at DESC, id DESC`` and then calls
        ``scalar_one_or_none()``. Ordering does not limit, so any user holding two
        or more refresh tokens makes this raise ``MultipleResultsFound`` instead
        of returning the newest. It needs ``.limit(1)`` (or ``scalars().first()``).
        No production caller exists yet, so the bug is latent.
        """
        from sqlalchemy.exc import MultipleResultsFound

        token_repository.session.add_all(
            [
                RefreshToken(
                    user_id=test_user.id,
                    token_hash="older",
                    expires_at=_dt.now(UTC) - timedelta(days=1),
                    created_at=_dt.now(UTC) - timedelta(days=2),
                ),
                RefreshToken(
                    user_id=test_user.id,
                    token_hash="newer",
                    expires_at=_dt.now(UTC) + timedelta(days=1),
                    created_at=_dt.now(UTC),
                ),
            ]
        )
        await token_repository.commit()

        with pytest.raises(MultipleResultsFound):
            await token_repository.get_by_user_id(test_user.id)

    async def test_get_by_user_id_is_none_without_tokens(self, test_user, token_repository):
        assert await token_repository.get_by_user_id(test_user.id) is None

    async def test_consume_deletes_and_returns_the_token(self, test_user, token_repository):
        await token_repository.create(
            user_id=test_user.id,
            token_hash="consume-me",
            expires_at=_dt.now(UTC) + timedelta(days=1),
        )
        await token_repository.commit()

        consumed = await token_repository.consume("consume-me")
        await token_repository.commit()

        assert consumed is not None
        assert await token_repository.get_by_token_hash("consume-me") is None

    async def test_consume_of_unknown_hash_is_none(self, token_repository):
        assert await token_repository.consume("nope") is None

    async def test_consume_rolls_back_and_reraises(self, token_repository):
        with patch.object(
            type(token_repository.session), "execute", AsyncMock(side_effect=RuntimeError("io"))
        ):
            with pytest.raises(RuntimeError):
                await token_repository.consume("anything")

    async def test_revoke_unknown_hash_is_false(self, token_repository):
        assert await token_repository.revoke("never-existed") is False

    async def test_revoke_rolls_back_and_reraises(self, token_repository):
        with patch.object(
            TokenBlacklistRepository, "create", AsyncMock()
        ), patch.object(
            type(token_repository), "consume", AsyncMock(side_effect=RuntimeError("io"))
        ):
            with pytest.raises(RuntimeError):
                await token_repository.revoke("x")

    async def test_delete_expired_rolls_back_and_reraises(self, token_repository):
        with patch.object(
            type(token_repository.session), "execute", AsyncMock(side_effect=RuntimeError("io"))
        ):
            with pytest.raises(RuntimeError):
                await token_repository.delete_expired()

    async def test_revoke_all_for_user_removes_every_token(self, test_user, token_repository):
        for i in range(3):
            await token_repository.create(
                user_id=test_user.id,
                token_hash=f"bulk-{i}",
                expires_at=_dt.now(UTC) + timedelta(days=1),
            )
        await token_repository.commit()

        removed = await token_repository.revoke_all_for_user(test_user.id)
        await token_repository.commit()

        assert removed == 3
        assert await token_repository.get_by_user_id(test_user.id) is None

    async def test_revoke_all_for_unknown_user_is_zero(self, token_repository):
        assert await token_repository.revoke_all_for_user(uuid4()) == 0

    async def test_revoke_all_rolls_back_and_reraises(self, token_repository):
        with patch.object(
            type(token_repository.session), "execute", AsyncMock(side_effect=RuntimeError("io"))
        ):
            with pytest.raises(RuntimeError):
                await token_repository.revoke_all_for_user(uuid4())


class TestLoginAuditRepositoryErrorPaths:
    async def test_create_rolls_back_and_reraises(self, test_user, audit_repository):
        with patch.object(
            type(audit_repository.session),
            "flush",
            AsyncMock(side_effect=RuntimeError("io")),
        ):
            with pytest.raises(RuntimeError):
                await audit_repository.create(
                    user_id=test_user.id, status="failed", ip_address="1.1.1.1"
                )

    async def test_successful_attempts_are_not_counted(self, test_user, audit_repository):
        await audit_repository.create(
            user_id=test_user.id, status="success", ip_address="1.1.1.1"
        )
        await audit_repository.commit()

        assert await audit_repository.get_recent_failed_attempts(test_user.id) == 0

    async def test_another_users_attempts_are_not_counted(self, test_user, audit_repository):
        await audit_repository.create(
            user_id=uuid4(), status="failed", ip_address="1.1.1.1"
        )
        await audit_repository.commit()

        assert await audit_repository.get_recent_failed_attempts(test_user.id) == 0


class TestTokenBlacklistRepository:
    async def test_create_rolls_back_and_reraises(self, test_user, test_session):
        repo = TokenBlacklistRepository(test_session)
        with patch.object(
            type(test_session), "flush", AsyncMock(side_effect=RuntimeError("io"))
        ):
            with pytest.raises(RuntimeError):
                await repo.create(
                    token_hash="h", user_id=test_user.id, expires_at=_dt.now(UTC)
                )

    def test_blacklist_repository_shares_the_base_helpers(self):
        from app.repositories import BaseRepository

        assert issubclass(TokenBlacklistRepository, BaseRepository)

    async def test_is_blacklisted_reports_membership(self, test_user, test_session):
        repo = TokenBlacklistRepository(test_session)
        await repo.create(
            token_hash="present", user_id=test_user.id, expires_at=_dt.now(UTC)
        )
        await test_session.commit()

        assert await repo.is_blacklisted("present") is True
        assert await repo.is_blacklisted("absent") is False

    async def test_delete_expired_removes_only_expired_entries(self, test_user, test_session):
        repo = TokenBlacklistRepository(test_session)
        await repo.create(
            token_hash="stale",
            user_id=test_user.id,
            expires_at=_dt.now(UTC) - timedelta(hours=1),
        )
        await repo.create(
            token_hash="fresh",
            user_id=test_user.id,
            expires_at=_dt.now(UTC) + timedelta(hours=1),
        )
        await test_session.commit()

        removed = await repo.delete_expired()
        await test_session.commit()

        assert removed == 1
        assert await repo.is_blacklisted("stale") is False
        assert await repo.is_blacklisted("fresh") is True

    async def test_delete_expired_with_nothing_to_do_is_zero(self, test_session):
        assert await TokenBlacklistRepository(test_session).delete_expired() == 0

    async def test_delete_expired_rolls_back_and_reraises(self, test_session):
        with patch.object(
            type(test_session), "execute", AsyncMock(side_effect=RuntimeError("io"))
        ):
            with pytest.raises(RuntimeError):
                await TokenBlacklistRepository(test_session).delete_expired()


def _notice(session, **kwargs):
    fields = {
        "version": "1.0.0",
        "jurisdiction": "EU",
        "title": "T",
        "content": "C",
        "language": "en",
        "effective_date": _dt.now(UTC),
    }
    fields.update(kwargs)
    notice = PrivacyNotice(**fields)
    session.add(notice)
    return notice


def _consent(session, **kwargs):
    fields = {
        "user_id": uuid4(),
        "consent_type": "marketing",
        "jurisdiction": "EU",
        "granted": True,
        "version": "1.0.0",
    }
    fields.update(kwargs)
    record = ConsentRecord(**fields)
    session.add(record)
    return record


class TestPrivacyNoticeRepositoryQueries:
    async def test_get_all_current_only_returns_current(self, test_session):
        repo = PrivacyNoticeRepository(test_session)
        _notice(test_session, version="1.0.0", jurisdiction="EU", is_current=True)
        _notice(test_session, version="2.0.0", jurisdiction="EU", is_current=False)
        await test_session.commit()

        current = await repo.get_all_current()

        assert [n.version for n in current] == ["1.0.0"]

    async def test_get_all_current_is_empty_when_nothing_is_current(self, test_session):
        assert await PrivacyNoticeRepository(test_session).get_all_current() == []

    async def test_get_by_jurisdiction_is_ordered_by_effective_date(self, test_session):
        repo = PrivacyNoticeRepository(test_session)
        _notice(
            test_session,
            version="1.0.0",
            jurisdiction="US",
            effective_date=_dt.now(UTC) - timedelta(days=2),
        )
        _notice(
            test_session,
            version="2.0.0",
            jurisdiction="US",
            effective_date=_dt.now(UTC) - timedelta(days=1),
        )
        _notice(test_session, version="3.0.0", jurisdiction="EU")
        await test_session.commit()

        notices = await repo.get_by_jurisdiction("US")

        assert [n.version for n in notices] == ["2.0.0", "1.0.0"]

    async def test_get_by_jurisdiction_for_an_unknown_jurisdiction_is_empty(self, test_session):
        assert await PrivacyNoticeRepository(test_session).get_by_jurisdiction("ZZ") == []

    async def test_get_current_defaults_to_english(self, test_session):
        repo = PrivacyNoticeRepository(test_session)
        _notice(test_session, language="en", is_current=True)
        _notice(test_session, version="2.0.0", language="de", is_current=True)
        await test_session.commit()

        assert (await repo.get_current("EU")).language == "en"

    async def test_get_current_for_an_unknown_jurisdiction_is_none(self, test_session):
        assert await PrivacyNoticeRepository(test_session).get_current("ZZ") is None

    async def test_deprecate_clears_current_and_stamps_the_date(self, test_session):
        repo = PrivacyNoticeRepository(test_session)
        notice = _notice(test_session, is_current=True)
        await test_session.commit()

        await repo.deprecate(notice)
        await test_session.commit()

        assert notice.is_current is False
        assert notice.deprecated_date is not None
        assert await repo.get_current("EU") is None

    async def test_set_current_keeps_the_same_row_current(self, test_session):
        """Re-setting the already-current notice must not deprecate itself."""
        repo = PrivacyNoticeRepository(test_session)
        notice = _notice(test_session, is_current=True)
        await test_session.commit()

        await repo.set_current(notice)
        await test_session.commit()

        assert notice.is_current is True
        assert notice.deprecated_date is None


class TestConsentRecordRepositoryQueries:
    async def test_get_by_user_type_jurisdiction_matches_exactly(self, test_session):
        repo = PrivacyConsentRecordRepository(test_session)
        record = _consent(test_session, consent_type="marketing", jurisdiction="EU")
        _consent(test_session, consent_type="analytics", jurisdiction="EU")
        _consent(test_session, consent_type="marketing", jurisdiction="US")
        await test_session.commit()

        found = await repo.get_by_user_type_jurisdiction(
            record.user_id, "marketing", "EU"
        )

        assert found is not None
        assert found.consent_type == "marketing"
        assert found.jurisdiction == "EU"

    async def test_get_by_user_type_jurisdiction_miss_is_none(self, test_session):
        assert (
            await PrivacyConsentRecordRepository(test_session).get_by_user_type_jurisdiction(
                uuid4(), "marketing", "EU"
            )
            is None
        )

    async def test_get_by_user_is_newest_first(self, test_session):
        repo = PrivacyConsentRecordRepository(test_session)
        user_id = uuid4()
        _consent(
            test_session,
            user_id=user_id,
            consent_type="first",
            created_at=_dt.now(UTC) - timedelta(days=1),
        )
        _consent(test_session, user_id=user_id, consent_type="second")
        _consent(test_session, user_id=uuid4(), consent_type="other-user")
        await test_session.commit()

        records = await repo.get_by_user(user_id)

        assert [r.consent_type for r in records] == ["second", "first"]

    async def test_get_by_user_for_an_unknown_user_is_empty(self, test_session):
        assert await PrivacyConsentRecordRepository(test_session).get_by_user(uuid4()) == []

    async def test_get_active_by_user_filters_granted_and_not_withdrawn(self, test_session):
        repo = PrivacyConsentRecordRepository(test_session)
        user_id = uuid4()
        _consent(test_session, user_id=user_id, consent_type="granted")
        _consent(test_session, user_id=user_id, consent_type="refused", granted=False)
        _consent(
            test_session,
            user_id=user_id,
            consent_type="withdrawn",
            withdrawn_at=_dt.now(UTC),
        )
        await test_session.commit()

        active = await repo.get_active_by_user(user_id)

        assert [r.consent_type for r in active] == ["granted"]

    async def test_get_active_by_user_for_an_unknown_user_is_empty(self, test_session):
        assert await PrivacyConsentRecordRepository(test_session).get_active_by_user(uuid4()) == []

    async def test_update_flushes_and_returns_the_same_row(self, test_session):
        repo = PrivacyConsentRecordRepository(test_session)
        record = _consent(test_session, consent_metadata="before")

        updated = await repo.update(record)
        await test_session.commit()

        assert updated is record
        assert record.consent_metadata == "before"

    async def test_grant_clears_withdrawal_state(self, test_session):
        repo = PrivacyConsentRecordRepository(test_session)
        record = _consent(test_session, granted=False)
        record.withdrawn_at = _dt.now(UTC)
        record.withdrawal_reason = "changed my mind"
        await test_session.commit()

        granted = await repo.grant(record)
        await test_session.commit()

        assert granted.granted is True
        assert granted.granted_at is not None
        assert granted.withdrawn_at is None
        assert granted.withdrawal_reason is None

    async def test_withdraw_records_the_reason(self, test_session):
        repo = PrivacyConsentRecordRepository(test_session)
        record = _consent(test_session)

        withdrawn = await repo.withdraw(record, "privacy settings")
        await test_session.commit()

        assert withdrawn.granted is False
        assert withdrawn.withdrawal_reason == "privacy settings"
        assert withdrawn.withdrawn_at is not None

    async def test_withdraw_without_a_reason_leaves_it_null(self, test_session):
        repo = PrivacyConsentRecordRepository(test_session)
        record = _consent(test_session)

        withdrawn = await repo.withdraw(record)
        await test_session.commit()

        assert withdrawn.withdrawal_reason is None

    async def test_create_returns_the_persisted_row(self, test_session):
        repo = PrivacyConsentRecordRepository(test_session)
        record = ConsentRecord(
            user_id=uuid4(),
            consent_type="cookies",
            jurisdiction="EU",
            granted=True,
            version="1.0.0",
        )

        created = await repo.create(record)
        await test_session.commit()

        assert created is record
        assert created.id is not None
