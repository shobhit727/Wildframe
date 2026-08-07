"""Unit tests for repository layer."""

from datetime import UTC, datetime, timedelta

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
