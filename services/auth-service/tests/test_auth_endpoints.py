"""
Integration tests for Auth Service API endpoints.
Tests the complete flow from HTTP request to database operations.
"""

import pytest
from app.core.database import get_db
from app.main import app
from httpx import AsyncClient


@pytest_asyncio.fixture
async def client(db_session):
    """Create test HTTP client."""
    async def override_get_db():
        yield db_session
    
    app.dependency_overrides[get_db] = override_get_db
    
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client
    
    app.dependency_overrides.clear()


@pytest.mark.asyncio
class TestAuthEndpoints:
    """Integration tests for auth endpoints."""
    
    async def test_register_endpoint(self, client, db_session):
        """Test user registration endpoint."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "newuser@example.com",
                "password": "SecurePassword123!"
            }
        )
        
        assert response.status_code == 201
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
    
    async def test_register_duplicate_email(self, client, db_session):
        """Test registration with duplicate email."""
        email = "test@example.com"
        
        # First registration
        await client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePassword123!"
            }
        )
        
        # Second registration with same email
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "DifferentPassword456!"
            }
        )
        
        assert response.status_code == 400
    
    async def test_login_endpoint(self, client, db_session):
        """Test user login endpoint."""
        email = "testuser@example.com"
        password = "SecurePassword123!"
        
        # Register user
        await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password}
        )
        
        # Login
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
    
    async def test_login_invalid_credentials(self, client, db_session):
        """Test login with invalid credentials."""
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "nonexistent@example.com",
                "password": "wrongpassword"
            }
        )
        
        assert response.status_code == 401
    
    async def test_get_current_user(self, client, db_session):
        """Test getting current user information."""
        email = "testuser@example.com"
        password = "SecurePassword123!"
        
        # Register and login
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password}
        )
        token = register_response.json()["access_token"]
        
        # Get user info
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == email
    
    async def test_logout_endpoint(self, client, db_session):
        """Test user logout."""
        email = "testuser@example.com"
        password = "SecurePassword123!"
        
        # Register and login
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password}
        )
        token = register_response.json()["access_token"]
        
        # Logout
        response = await client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 204
        
        # Try to use token after logout (should fail)
        response = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 401
    
    async def test_change_password(self, client, db_session):
        """Test password change."""
        email = "testuser@example.com"
        old_password = "SecurePassword123!"
        new_password = "NewPassword456!"
        
        # Register and login
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": old_password}
        )
        token = register_response.json()["access_token"]
        
        # Change password
        response = await client.post(
            "/api/v1/auth/change-password",
            json={
                "current_password": old_password,
                "new_password": new_password
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        
        assert response.status_code == 204
        
        # Try login with old password (should fail)
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": old_password}
        )
        assert response.status_code == 401
        
        # Login with new password (should succeed)
        response = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": new_password}
        )
        assert response.status_code == 200
    
    async def test_refresh_token(self, client, db_session):
        """Test token refresh."""
        email = "testuser@example.com"
        password = "SecurePassword123!"
        
        # Register
        register_response = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password}
        )
        refresh_token = register_response.json()["refresh_token"]
        
        # Refresh token
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh_token}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data


# Decorator to mark async tests
def pytest_asyncio():
    """Pytest asyncio configuration."""
