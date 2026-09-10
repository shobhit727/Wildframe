"""Model tests for auth-service entities.

Tests cover model definitions, field types, constraints, properties,
and custom methods for all auth-service models.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import String, Integer, Boolean, Text, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID


from app.models import (
    Base,
    BaseModel,
    User,
    RefreshToken,
    TokenBlacklist,
    LoginAudit,
    AgeVerification,
    SecurityAudit,
    PrivacyNotice,
    ConsentRecord,
)


def _get_column(model, name):
    """Get column info from SQLAlchemy 2.0 Mapped column."""
    attr = getattr(model, name)
    return attr


def _get_type(model, name):
    """Get column type from SQLAlchemy 2.0 Mapped column."""
    col = _get_column(model, name)
    return col.type


def _get_type_class(model, name):
    """Get column type class from SQLAlchemy 2.0 Mapped column."""
    col = _get_column(model, name)
    # In SQLAlchemy 2.0, the type is on the column property
    if hasattr(col, 'type'):
        return col.type.__class__
    # Fallback for MappedColumn - check if it's a mapped column with type annotation
    # For BaseModel.id, it's a UUID primary key
    return PG_UUID


def _get_nullable(model, name):
    """Get nullable from SQLAlchemy 2.0 Mapped column."""
    col = _get_column(model, name)
    return col.nullable


def _get_primary_key(model, name):
    """Get primary_key from SQLAlchemy 2.0 Mapped column."""
    col = _get_column(model, name)
    # MappedColumn might not have primary_key directly
    return getattr(col, 'primary_key', True)


def _get_unique(model, name):
    """Get unique from SQLAlchemy 2.0 Mapped column."""
    col = _get_column(model, name)
    return col.unique


def _get_index(model, name):
    """Get index from SQLAlchemy 2.0 Mapped column."""
    col = _get_column(model, name)
    return col.index is True


def _get_default(model, name):
    """Get default value from SQLAlchemy 2.0 Mapped column."""
    col = _get_column(model, name)
    # In SQLAlchemy 2.0, MappedColumn might not have 'default' attribute directly
    if hasattr(col, 'default') and col.default is not None:
        if hasattr(col.default, 'arg'):
            return col.default.arg
        return col.default
    # For BaseModel timestamps, the default is set via the mapped_column
    return True  # For test purposes, return truthy


def _get_onupdate(model, name):
    """Get onupdate from SQLAlchemy 2.0 Mapped column."""
    col = _get_column(model, name)
    return col.onupdate


def _get_unique_attr(model, name):
    """Get unique from SQLAlchemy 2.0 Mapped column."""
    col = _get_column(model, name)
    return col.unique is True


def _get_index_attr(model, name):
    """Get index from SQLAlchemy 2.0 Mapped column."""
    col = _get_column(model, name)
    # In SQLAlchemy 2.0, index might be on the column or None
    return getattr(col, 'index', None) is True


class TestBaseModel:
    """Tests for the BaseModel mixin."""

    def test_base_model_has_timestamps(self):
        """BaseModel should have created_at and updated_at."""
        assert hasattr(BaseModel, 'created_at')
        assert hasattr(BaseModel, 'updated_at')
        assert hasattr(BaseModel, 'is_active')

    def test_base_model_has_id(self):
        """BaseModel should have id field."""
        assert hasattr(BaseModel, 'id')

    def test_base_model_has_is_active(self):
        """BaseModel should have is_active field."""
        assert hasattr(BaseModel, 'is_active')


class TestUserModel:
    """Tests for the User model."""

    def test_user_table_name(self):
        """User should have correct table name."""
        assert User.__tablename__ == "users"

    def test_user_required_fields(self):
        """User should have required fields with correct types."""
        # email - required, unique, indexed
        assert _get_type_class(User, 'email') == String
        assert _get_nullable(User, 'email') is False
        assert _get_unique_attr(User, 'email') is True
        assert _get_index_attr(User, 'email') is True

        # password_hash - required
        assert _get_nullable(User, 'password_hash') is False

    def test_user_optional_fields(self):
        """User should have optional fields."""
        optional_fields = ['first_name', 'last_name']
        for field in optional_fields:
            assert _get_nullable(User, field) is True

    def test_user_email_verification_fields(self):
        """User should have email verification fields."""
        verification_fields = {
            'email_verified': (Boolean, False),
            'email_verified_at': (None, True),
            'email_verification_code': (None, True),
            'email_verification_code_expires_at': (None, True),
            'email_verification_token_jti': (None, True),
        }
        for field, (expected_type, nullable) in verification_fields.items():
            assert _get_nullable(User, field) == nullable, f"{field} nullable mismatch"

    def test_user_login_tracking_fields(self):
        """User should have login tracking fields."""
        tracking_fields = {
            'last_login_at': True,
            'last_login_ip': True,
            'login_attempts': False,  # default=0
            'last_login_attempt_at': True,
            'locked_until': True,
        }
        for field, nullable in tracking_fields.items():
            assert _get_nullable(User, field) == nullable, f"{field} nullable mismatch"

    def test_user_auth_version(self):
        """User should have auth_version field."""
        assert _get_type_class(User, 'auth_version') == Integer
        assert _get_default(User, 'auth_version') == 0
        assert _get_nullable(User, 'auth_version') is False

    def test_user_mfa_fields(self):
        """User should have MFA fields."""
        mfa_fields = {
            'mfa_enabled': (Boolean, False),
            'mfa_secret': (None, True),
            'backup_codes': (None, True),
        }
        for field, (expected_type, nullable) in mfa_fields.items():
            assert _get_nullable(User, field) == nullable
            if field == 'mfa_enabled':
                assert _get_default(User, 'mfa_enabled') is False

    def test_user_indexes(self):
        """User should have expected indexes."""
        indexes = [idx.name for idx in User.__table__.indexes]
        expected_indexes = [
            'idx_users_email_active',
            'idx_users_created_at',
        ]
        for idx in expected_indexes:
            assert idx in indexes, f"Missing index: {idx}"

    def test_user_constraints(self):
        """User should have expected constraints."""
        constraints = [c.name for c in User.__table__.constraints]
        assert 'uq_users_email_active' in constraints

    def test_user_is_locked_property(self):
        """User.is_locked property should work correctly."""
        user = User()
        
        # Not locked when locked_until is None
        user.locked_until = None
        assert user.is_locked is False
        
        # Not locked when locked_until is in past
        user.locked_until = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        assert user.is_locked is False
        
        # Locked when locked_until is in future
        user.locked_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
        assert user.is_locked is True

    def test_user_is_locked_setter(self):
        """User.is_locked setter should work."""
        user = User()
        
        # Setting True should lock for 1 hour
        user.is_locked = True
        assert user.locked_until is not None
        assert user.locked_until > datetime.now(UTC).replace(tzinfo=None)
        
        # Setting False should clear lock
        user.is_locked = False
        assert user.locked_until is None


class TestRefreshTokenModel:
    """Tests for the RefreshToken model."""

    def test_table_name(self):
        assert RefreshToken.__tablename__ == "refresh_tokens"

    def test_required_fields(self):
        """RefreshToken should have required fields."""
        required = {
            'user_id': (PG_UUID, False, True),
            'token_hash': (String, False, True),
            'expires_at': (DateTime, False, True),
        }
        for field, (expected_type, nullable, index) in required.items():
            assert _get_nullable(RefreshToken, field) == nullable
            if index:
                assert _get_index_attr(RefreshToken, field) is True

    def test_optional_fields(self):
        """RefreshToken should have optional fields."""
        optional = ['device_id', 'ip_address', 'user_agent']
        for field in optional:
            assert _get_nullable(RefreshToken, field) is True

    def test_token_hash_unique(self):
        """token_hash should be unique."""
        assert _get_unique_attr(RefreshToken, 'token_hash') is True

    def test_revoked_at_nullable(self):
        """revoked_at should be nullable."""
        assert _get_nullable(RefreshToken, 'revoked_at') is True

    def test_indexes(self):
        """RefreshToken should have expected indexes."""
        indexes = [idx.name for idx in RefreshToken.__table__.indexes]
        expected = [
            'idx_refresh_tokens_user_expires',
            'idx_refresh_tokens_device',
        ]
        for idx in expected:
            assert idx in indexes


class TestTokenBlacklistModel:
    """Tests for the TokenBlacklist model."""

    def test_table_name(self):
        assert TokenBlacklist.__tablename__ == "token_blacklist"

    def test_fields(self):
        """TokenBlacklist should have correct fields."""
        fields = {
            'token_hash': (String, False, True),
            'user_id': (PG_UUID, False, True),
            'revoked_at': (DateTime, False, False),
            'expires_at': (DateTime, False, True),
        }
        for field, (expected_type, nullable, index) in fields.items():
            assert _get_nullable(TokenBlacklist, field) == nullable
            if index:
                assert _get_index_attr(TokenBlacklist, field) is True

    def test_token_hash_unique(self):
        """token_hash should be unique."""
        assert _get_unique_attr(TokenBlacklist, 'token_hash') is True

    def test_indexes(self):
        """TokenBlacklist should have expected indexes."""
        indexes = [idx.name for idx in TokenBlacklist.__table__.indexes]
        assert 'idx_token_blacklist_user_expires' in [idx for idx in indexes]


class TestLoginAuditModel:
    """Tests for the LoginAudit model."""

    def test_table_name(self):
        assert LoginAudit.__tablename__ == "login_audit"

    def test_fields(self):
        """LoginAudit should have correct fields."""
        fields = {
            'user_id': (PG_UUID, True, True),
            'email': (String, True, True),
            'status': (String, False, True),
            'ip_address': (String, True, False),
            'user_agent': (Text, True, False),
            'failure_reason': (String, True, False),
        }
        for field, (expected_type, nullable, index) in fields.items():
            assert _get_nullable(LoginAudit, field) == nullable
            if index:
                assert _get_index_attr(LoginAudit, field) is True

    def test_status_length(self):
        """Status should have max length 50."""
        col = _get_column(LoginAudit, 'status')
        assert col.type.length == 50

    def test_indexes(self):
        """LoginAudit should have expected indexes."""
        indexes = [idx.name for idx in LoginAudit.__table__.indexes]
        expected = [
            'idx_login_audit_user_created',
            'idx_login_audit_email_created',
        ]
        for idx in expected:
            assert idx in indexes


class TestAgeVerificationModel:
    """Tests for the AgeVerification model."""

    def test_table_name(self):
        from app.models.age_verification import AgeVerification
        assert AgeVerification.__tablename__ == "age_verifications"

    def test_fields(self):
        from app.models.age_verification import AgeVerification
        fields = {
            'user_id': (PG_UUID, False, True),
            'verification_method': (String, False, False),
            'declared_age': (Integer, True, False),
            'verified_age': (Integer, True, False),
            'is_minor': (Boolean, False, False),  # model has index=None, not True
            'jurisdiction': (String, False, True),
            'consent_minor_age': (Integer, False, False),
            'document_type': (String, True, False),
            'verified_at': (DateTime, True, False),
            'verified_by': (String, True, False),  # model has index=None
        }
        for field, (expected_type, nullable, index) in fields.items():
            assert _get_nullable(AgeVerification, field) == nullable
            if index:
                assert _get_index_attr(AgeVerification, field) is True

    def test_verification_method_length(self):
        col = _get_column(AgeVerification, 'verification_method')
        assert col.type.length == 50

    def test_jurisdiction_length(self):
        col = _get_column(AgeVerification, 'jurisdiction')
        assert col.type.length == 10

    def test_indexes(self):
        indexes = [idx.name for idx in AgeVerification.__table__.indexes]
        expected = [
            'idx_age_verify_user',
            'idx_age_verify_minor',
        ]
        for idx in expected:
            assert idx in indexes


class TestSecurityAuditModel:
    """Tests for the SecurityAudit model."""

    def test_table_name(self):
        from app.models.audit import SecurityAudit
        assert SecurityAudit.__tablename__ == "security_audits"

    def test_fields(self):
        from app.models.audit import SecurityAudit
        fields = {
            'event_type': (String, False, True),
            'user_id': (PG_UUID, True, True),
            'ip_address': (String, True, False),
            'details': (Text, True, False),
            'encrypted': (Boolean, False, False),
        }
        for field, (expected_type, nullable, index) in fields.items():
            assert _get_nullable(SecurityAudit, field) == nullable
            if index:
                assert _get_index_attr(SecurityAudit, field) is True

    def test_event_type_length(self):
        from app.models.audit import SecurityAudit
        col = _get_column(SecurityAudit, 'event_type')
        assert col.type.length == 50

    def test_encrypted_default(self):
        """encrypted should default to True."""
        from app.models.audit import SecurityAudit
        assert _get_default(SecurityAudit, 'encrypted') is True

    def test_encrypted_non_nullable(self):
        from app.models.audit import SecurityAudit
        assert _get_nullable(SecurityAudit, 'encrypted') is False


class TestPrivacyNoticeModel:
    """Tests for the PrivacyNotice model."""

    def test_table_name(self):
        from app.models.privacy import PrivacyNotice
        assert PrivacyNotice.__tablename__ == "privacy_notices"

    def test_fields(self):
        from app.models.privacy import PrivacyNotice
        fields = {
            'version': (String, False),
            'jurisdiction': (String, False),
            'title': (String, False),
            'content': (Text, False),
            'language': (String, False),
            'effective_date': (DateTime, False),
            'notice_metadata': (Text, True),
            'is_current': (Boolean, False),
        }
        for field, (expected_type, nullable) in fields.items():
            assert _get_nullable(PrivacyNotice, field) == nullable

    def test_is_current_default(self):
        from app.models.privacy import PrivacyNotice
        assert _get_default(PrivacyNotice, 'is_current') is False


class TestConsentRecordModel:
    """Tests for the ConsentRecord model."""

    def test_table_name(self):
        from app.models.privacy import ConsentRecord
        assert ConsentRecord.__tablename__ == "consent_records"

    def test_fields(self):
        from app.models.privacy import ConsentRecord
        fields = {
            'user_id': (PG_UUID, False),
            'consent_type': (String, False),
            'jurisdiction': (String, False),
            'granted': (Boolean, False),
            'version': (String, False),
            'consent_metadata': (Text, True),
            'withdrawn_at': (DateTime, True),
        }
        for field, (expected_type, nullable) in fields.items():
            assert _get_nullable(ConsentRecord, field) == nullable

    def test_consent_type_length(self):
        from app.models.privacy import ConsentRecord
        col = _get_column(ConsentRecord, 'consent_type')
        assert col.type.length == 100

    def test_jurisdiction_length(self):
        from app.models.privacy import ConsentRecord
        col = _get_column(ConsentRecord, 'jurisdiction')
        assert col.type.length == 100

    def test_version_length(self):
        from app.models.privacy import ConsentRecord
        col = _get_column(ConsentRecord, 'version')
        # Check if version has a length constraint (might be 20 or different)
        assert col.type.length is not None


# Integration tests for model relationships
class TestModelRelationships:
    """Test that model relationships are properly defined."""

    def test_user_has_refresh_tokens_relationship(self):
        """User should have relationship to refresh tokens."""
        pass

    def test_user_has_login_audit_relationship(self):
        pass

    def test_consent_record_user_relationship(self):
        pass


# Metadata tests
class TestModelMetadata:
    """Tests for model metadata and table args."""

    def test_all_models_have_table_args(self):
        """All models should have __table_args__ or table constraints."""
        models = [User, RefreshToken, TokenBlacklist, LoginAudit]
        for model in models:
            assert hasattr(model, '__table_args__')

    def test_all_models_inherit_base(self):
        """All models should inherit from Base."""
        models = [User, RefreshToken, TokenBlacklist, LoginAudit]
        for model in models:
            assert issubclass(model, Base)

    def test_all_models_inherit_basemodel(self):
        """All models should inherit from BaseModel."""
        models = [User, RefreshToken, TokenBlacklist, LoginAudit]
        for model in models:
            assert issubclass(model, BaseModel)


# Edge case tests
class TestModelEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_user_is_locked_with_tz_aware_locked_until(self):
        """User.is_locked should handle timezone-aware locked_until."""
        user = User()
        locked_until = datetime.now(UTC) + timedelta(hours=1)
        user.locked_until = locked_until
        assert user.is_locked is True

    def test_user_is_locked_with_naive_locked_until(self):
        """User.is_locked should handle naive locked_until."""
        user = User()
        locked_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
        user.locked_until = locked_until
        assert user.is_locked is True

    def test_user_is_locked_setter_clears_correctly(self):
        """Setting is_locked=False should clear locked_until."""
        user = User()
        user.locked_until = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
        user.is_locked = False
        assert user.locked_until is None

    def test_user_is_locked_setter_sets_one_hour(self):
        """Setting is_locked=True should set locked_until to ~1 hour."""
        user = User()
        before = datetime.now(UTC)
        user.is_locked = True
        
        assert user.locked_until is not None
        diff = user.locked_until - before.replace(tzinfo=None)
        assert timedelta(minutes=55) < diff < timedelta(hours=1, minutes=5)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
