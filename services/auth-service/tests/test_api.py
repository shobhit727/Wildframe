"""Integration tests for Auth Service API endpoints."""

import pytest
from app.core.database import DatabaseManager
from app.main import create_app
from app.models import Base
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest.fixture
async def test_app():
    """Create test FastAPI app with test database."""
    # Use in-memory SQLite for testing
    test_engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"timeout": 15},
    )

    # Create tables
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Override database manager
    original_get_engine = DatabaseManager.get_engine
    DatabaseManager.get_engine = lambda: test_engine

    original_session_factory = DatabaseManager.get_session_factory
    DatabaseManager._session_factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )
    DatabaseManager.get_session_factory = lambda: DatabaseManager._session_factory

    app = create_app()

    yield app

    # Cleanup
    await test_engine.dispose()
    DatabaseManager.get_engine = original_get_engine
    DatabaseManager.get_session_factory = original_session_factory


@pytest.fixture
def client(test_app):
    """Create test client."""
    return TestClient(test_app)


class TestAuthEndpoints:
    """Test authentication endpoints."""

    def test_register_success(self, client):
        """Test successful user registration."""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "newuser@example.com",
                "password": "SecurePass123!",
                "first_name": "John",
                "last_name": "Doe",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "newuser@example.com"
        assert data["first_name"] == "John"
        assert data["last_name"] == "Doe"

    def test_register_duplicate_email(self, client):
        """Test registration with duplicate email."""
        # Register first user
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
                "first_name": "Test",
                "last_name": "User",
            },
        )

        # Try to register with same email
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "password": "DifferentPass456!",
                "first_name": "Another",
                "last_name": "User",
            },
        )

        assert response.status_code == 409

    def test_register_invalid_password(self, client):
        """Test registration with weak password."""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "user@example.com",
                "password": "weak",  # Too short and missing requirements
                "first_name": "John",
                "last_name": "Doe",
            },
        )

        assert response.status_code == 422

    def test_register_invalid_email(self, client):
        """Test registration with invalid email."""
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": "invalid-email",
                "password": "SecurePass123!",
                "first_name": "John",
                "last_name": "Doe",
            },
        )

        assert response.status_code == 422

    def test_login_success(self, client):
        """Test successful login."""
        # Register user first
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
                "first_name": "Test",
                "last_name": "User",
            },
        )

        # Login
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == 900

    def test_login_invalid_email(self, client):
        """Test login with invalid email."""
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "nonexistent@example.com",
                "password": "AnyPassword123!",
            },
        )

        assert response.status_code == 401

    def test_login_invalid_password(self, client):
        """Test login with incorrect password."""
        # Register user
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
                "first_name": "Test",
                "last_name": "User",
            },
        )

        # Login with wrong password
        response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "WrongPassword456!",
            },
        )

        assert response.status_code == 401

    def test_get_current_user(self, client):
        """Test getting current user profile."""
        # Register and login
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
                "first_name": "Test",
                "last_name": "User",
            },
        )

        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
            },
        )

        access_token = login_response.json()["access_token"]

        # Get current user
        response = client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "test@example.com"
        assert data["first_name"] == "Test"

    def test_get_current_user_no_token(self, client):
        """Test getting current user without token."""
        response = client.get("/api/v1/users/me")

        assert response.status_code == 401

    def test_get_current_user_invalid_token(self, client):
        """Test getting current user with invalid token."""
        response = client.get(
            "/api/v1/users/me",
            headers={"Authorization": "Bearer invalid.token.here"},
        )

        assert response.status_code == 401

    def test_refresh_token(self, client):
        """Test token refresh."""
        # Register and login
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
                "first_name": "Test",
                "last_name": "User",
            },
        )

        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
            },
        )

        refresh_token = login_response.json()["refresh_token"]

        # Refresh token
        response = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        # New refresh token should be different
        assert data["refresh_token"] != refresh_token

    def test_logout(self, client):
        """Test logout."""
        # Register and login
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
                "first_name": "Test",
                "last_name": "User",
            },
        )

        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
            },
        )

        refresh_token = login_response.json()["refresh_token"]

        # Logout
        response = client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": refresh_token},
        )

        assert response.status_code == 204

    def test_change_password(self, client):
        """Test changing password."""
        # Register and login
        client.post(
            "/api/v1/auth/register",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
                "first_name": "Test",
                "last_name": "User",
            },
        )

        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
            },
        )

        access_token = login_response.json()["access_token"]

        # Change password
        response = client.post(
            "/api/v1/users/change-password",
            json={
                "old_password": "SecurePass123!",
                "new_password": "NewSecurePass456!",
            },
            headers={"Authorization": f"Bearer {access_token}"},
        )

        assert response.status_code == 204

        # Verify old password doesn't work
        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "SecurePass123!",
            },
        )
        assert login_response.status_code == 401

        # Verify new password works
        login_response = client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "NewSecurePass456!",
            },
        )
        assert login_response.status_code == 200
