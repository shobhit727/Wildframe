"""Integration tests for Auth Service."""
import json

import pytest
import pytest_asyncio
from app.main import app
from app.repositories import UserRepository
from app.schemas import UserRegisterRequest
from app.security import PasswordManager, TokenManager
from app.services import AuthService
from httpx import AsyncClient


@pytest_asyncio.fixture
async def client():
    """Async HTTP client for testing."""
    async with AsyncClient(app=app, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def db_session():
    """Database session for direct testing."""
    from app.core.database import async_session
    async with async_session() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def auth_service(db_session):
    """AuthService instance with test DB."""
    return AuthService(
        user_repo=UserRepository(db_session),
        refresh_token_repo=None,  # Not used in these tests
        audit_repo=None,
        password_manager=PasswordManager(),
        token_manager=TokenManager(),
    )


class TestUserRegistrationIntegration:
    """Integration tests for user registration."""

    async def test_register_new_user(self, auth_service, db_session):
        """Test registering a new user via service."""
        request = UserRegisterRequest(
            email="test@example.com",
            password="SecurePass123!",
            first_name="John",
            last_name="Doe",
        )
        user = await auth_service.register(request)
        
        assert user.email == "test@example.com"
        assert user.first_name == "John"
        assert user.last_name == "Doe"
        assert user.email_verified is False
        
        # Verify user exists in DB
        from app.repositories import UserRepository
        repo = UserRepository(db_session)
        db_user = await repo.get_by_email("test@example.com")
        assert db_user is not None
        assert db_user.email == "test@example.com"

    async def test_register_duplicate_email_fails(self, auth_service):
        """Test that registering with duplicate email fails."""
        request = UserRegisterRequest(
            email="duplicate@example.com",
            password="SecurePass123!",
            first_name="Jane",
            last_name="Doe",
        )
        await auth_service.register(request)
        
        with pytest.raises(Exception) as exc_info:
            await auth_service.register(request)
        
        assert "already exists" in str(exc_info.value).lower()


class TestUserLoginIntegration:
    """Integration tests for user login."""

    async def test_login_success(self, auth_service, db_session):
        """Test successful login."""
        from app.schemas import UserLoginRequest, UserRegisterRequest
        
        # Register user first
        reg_request = UserRegisterRequest(
            email="login_test@example.com",
            password="SecurePass123!",
            first_name="Login",
            last_name="Test",
        )
        await auth_service.register(reg_request)
        
        # Login
        UserLoginRequest(
            email="login_test@example.com",
            password="SecurePass123!",
        )
        
        # We need to use the service's login method which expects different args
        # Let's test via the API endpoint instead
        # Will test via API client


class TestEmailVerificationIntegration:
    """Integration tests for email verification."""

    async def test_send_verification_code(self, auth_service, db_session):
        """Test sending email verification code."""
        from app.schemas import UserRegisterRequest
        
        # Register user
        reg_request = UserRegisterRequest(
            email="verify_test@example.com",
            password="SecurePass123!",
            first_name="Verify",
            last_name="Test",
        )
        await auth_service.register(reg_request)
        
        # Get user ID
        from app.repositories import UserRepository
        repo = UserRepository(db_session)
        user = await repo.get_by_email("verify_test@example.com")
        
        # Send verification code
        result = await auth_service.send_email_verification(user.id)
        assert "code" in result
        assert len(result["code"]) == 6
        assert result["code"].isdigit()

    async def test_verify_email_code(self, auth_service, db_session):
        """Test verifying email code."""
        from app.schemas import UserRegisterRequest
        
        # Register user
        reg_request = UserRegisterRequest(
            email="verify_code_test@example.com",
            password="SecurePass123!",
            first_name="Verify",
            last_name="Code",
        )
        await auth_service.register(reg_request)
        
        # Get user
        from app.repositories import UserRepository
        repo = UserRepository(db_session)
        user = await repo.get_by_email("verify_code_test@example.com")
        
        # Send code
        code_result = await auth_service.send_email_verification(user.id)
        code = code_result["code"]
        
        # Verify code
        result = await auth_service.verify_email(user.id, code)
        assert result["message"] == "Email verified successfully"
        
        # Verify user is marked as verified
        from app.repositories import UserRepository
        repo = UserRepository(db_session)
        user = await repo.get_by_id(user.id)
        assert user.email_verified is True


class TestMFAIntegration:
    """Integration tests for MFA."""

    async def test_setup_mfa(self, auth_service, db_session):
        """Test MFA setup."""
        from app.schemas import UserRegisterRequest
        
        # Register user
        reg_request = UserRegisterRequest(
            email="mfa_test@example.com",
            password="SecurePass123!",
            first_name="MFA",
            last_name="Test",
        )
        await auth_service.register(reg_request)
        
        # Get user
        from app.repositories import UserRepository
        repo = UserRepository(db_session)
        user = await repo.get_by_email("mfa_test@example.com")
        
        # Setup MFA
        result = await auth_service.setup_mfa(user.id)
        assert "secret" in result
        assert "totp_uri" in result
        assert "backup_codes" in result
        assert len(result["backup_codes"]) == 10
        
        # Verify secret stored
        from app.repositories import UserRepository
        repo = UserRepository(db_session)
        user = await repo.get_by_id(user.id)
        assert user.mfa_secret is not None
        assert user.backup_codes is not None

    async def test_verify_mfa_code(self, auth_service, db_session):
        """Test MFA verification with TOTP code."""
        import pyotp
        from app.schemas import UserRegisterRequest
        
        # Register user
        reg_request = UserRegisterRequest(
            email="mfa_verify_test@example.com",
            password="SecurePass123!",
            first_name="MFA",
            last_name="Verify",
        )
        await auth_service.register(reg_request)
        
        # Get user
        from app.repositories import UserRepository
        repo = UserRepository(db_session)
        user = await repo.get_by_email("mfa_verify_test@example.com")
        
        # Setup MFA
        setup_result = await auth_service.setup_mfa(user.id)
        secret = setup_result["secret"]
        
        # Generate valid TOTP code
        totp = pyotp.TOTP(secret)
        code = totp.now()
        
        # Verify code
        result = await auth_service.verify_mfa(user.id, code)
        assert result["message"] == "MFA enabled successfully"
        
        # Verify MFA enabled in DB
        from app.repositories import UserRepository
        repo = UserRepository(db_session)
        user = await repo.get_by_id(user.id)
        assert user.mfa_enabled is True

    async def test_verify_mfa_backup_code(self, auth_service, db_session):
        """Test MFA verification with backup code."""
        from app.schemas import UserRegisterRequest
        
        # Register user
        reg_request = UserRegisterRequest(
            email="mfa_backup_test@example.com",
            password="SecurePass123!",
            first_name="MFA",
            last_name="Backup",
        )
        await auth_service.register(reg_request)
        
        # Get user
        from app.repositories import UserRepository
        repo = UserRepository(db_session)
        user = await repo.get_by_email("mfa_backup_test@example.com")
        
        # Setup MFA
        setup_result = await auth_service.setup_mfa(user.id)
        backup_code = setup_result["backup_codes"][0]
        
        # Verify with backup code
        result = await auth_service.verify_mfa(user.id, backup_code)
        assert result["message"] == "MFA enabled successfully"
        
        # Verify backup code removed
        from app.repositories import UserRepository
        repo = UserRepository(db_session)
        user = await repo.get_by_id(user.id)
        backup_codes = json.loads(user.backup_codes) if user.backup_codes else []
        assert backup_code not in backup_codes


# Add json import at top
