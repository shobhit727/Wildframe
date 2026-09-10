"""Behavioral model tests for auth-service entities."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.models import (
    AgeVerification,
    ConsentRecord,
    LoginAudit,
    PrivacyNotice,
    RefreshToken,
    SecurityAudit,
    TokenBlacklist,
    User,
)


def test_user_lock_state_tracks_future_and_expired_lock() -> None:
    user = User()

    user.locked_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=1)
    assert user.is_locked is True

    user.locked_until = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1)
    assert user.is_locked is False


def test_user_lock_state_setter_locks_and_unlocks_user() -> None:
    user = User()

    user.is_locked = True
    assert user.locked_until is not None
    assert user.locked_until > datetime.now(UTC).replace(tzinfo=None)

    user.is_locked = False
    assert user.locked_until is None


def test_refresh_token_preserves_security_context() -> None:
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=7)
    token = RefreshToken(
        user_id=uuid4(),
        token_hash="hash",
        device_id="device-1",
        ip_address="127.0.0.1",
        user_agent="Wildframe test client",
        expires_at=expires_at,
    )

    assert token.token_hash == "hash"
    assert token.expires_at == expires_at
    assert token.revoked_at is None


def test_blacklisted_token_keeps_user_and_expiration() -> None:
    expires_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15)
    entry = TokenBlacklist(
        user_id=uuid4(),
        token_hash="revoked-token-hash",
        expires_at=expires_at,
    )

    assert entry.token_hash == "revoked-token-hash"
    assert entry.expires_at == expires_at


def test_login_audit_allows_anonymous_failed_login() -> None:
    audit = LoginAudit(
        email="unknown@example.com",
        status="failed",
        ip_address="203.0.113.10",
        failure_reason="invalid credentials",
    )

    assert audit.user_id is None
    assert audit.status == "failed"
    assert audit.failure_reason == "invalid credentials"


def test_age_verification_captures_minor_jurisdiction_policy() -> None:
    verification = AgeVerification(
        user_id=uuid4(),
        verification_method="self_declare",
        declared_age=15,
        verified_age=15,
        is_minor=True,
        jurisdiction="EU",
        consent_minor_age=16,
        verified_by="self",
    )

    assert verification.is_minor is True
    assert verification.consent_minor_age == 16
    assert verification.verified_at is None


def test_security_audit_records_explicit_encryption_state() -> None:
    audit = SecurityAudit(
        event_type="token.revoked", details="logout", encrypted=True
    )

    assert audit.event_type == "token.revoked"
    assert audit.details == "logout"
    assert audit.encrypted is True


def test_privacy_notice_records_current_state() -> None:
    notice = PrivacyNotice(
        version="1.0.0",
        jurisdiction="EU",
        title="Privacy Notice",
        content="Privacy policy body",
        language="en",
        effective_date=datetime.now(UTC).replace(tzinfo=None),
        is_current=False,
    )

    assert notice.version == "1.0.0"
    assert notice.is_current is False


def test_consent_record_tracks_grant_and_withdrawal_state() -> None:
    consent = ConsentRecord(
        user_id=uuid4(),
        consent_type="marketing",
        jurisdiction="EU",
        granted=True,
        version="1.0.0",
    )

    assert consent.granted is True
    assert consent.withdrawn_at is None

    consent.granted = False
    consent.withdrawn_at = datetime.now(UTC).replace(tzinfo=None)
    assert consent.granted is False
    assert consent.withdrawn_at is not None
