"""Quick test script for Auth Service core functionality."""
import sys
sys.path.insert(0, '/home/phoenix/Desktop/wildframe/services/auth-service')

# Test imports
try:
    from app.security import PasswordManager, TokenManager
    from app.schemas import UserRegisterRequest, UserLoginRequest, TokenResponse
    print("✅ All imports successful")
except Exception as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)

# Test PasswordManager
try:
    pm = PasswordManager()
    password = "SecurePass123!"
    hashed = pm.hash_password(password)
    
    assert len(hashed) > 0, "Hash should not be empty"
    assert hashed != password, "Hash should not equal password"
    assert pm.verify_password(password, hashed), "Verification should succeed"
    assert not pm.verify_password("WrongPassword456!", hashed), "Wrong password should fail"
    print("✅ PasswordManager tests passed")
except Exception as e:
    print(f"❌ PasswordManager test failed: {e}")
    sys.exit(1)

# Test TokenManager
try:
    from uuid import uuid4
    tm = TokenManager()
    user_id = uuid4()
    email = "test@example.com"
    
    # Test access token
    access_token = TokenManager.create_access_token(user_id, email)
    assert access_token, "Access token should be created"
    assert len(access_token) > 50, "Token should be long"
    print(f"  ✓ Access token created: {access_token[:50]}...")
    
    # Verify access token
    payload = TokenManager.verify_token(access_token, token_type="access")
    assert payload["user_id"] == str(user_id), "User ID should match"
    assert payload["email"] == email, "Email should match"
    print("  ✓ Access token verified")
    
    # Test refresh token
    refresh_token = TokenManager.create_refresh_token(user_id)
    assert refresh_token, "Refresh token should be created"
    print(f"  ✓ Refresh token created: {refresh_token[:50]}...")
    
    # Verify refresh token
    payload = TokenManager.verify_token(refresh_token, token_type="refresh")
    assert payload["user_id"] == str(user_id), "User ID should match"
    print("  ✓ Refresh token verified")
    
    print("✅ TokenManager tests passed")
except Exception as e:
    print(f"❌ TokenManager test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test Schema validation
try:
    # Valid registration
    reg_req = UserRegisterRequest(
        email="newuser@example.com",
        password="SecurePass123!",
        first_name="John",
        last_name="Doe"
    )
    assert reg_req.email == "newuser@example.com"
    print("  ✓ Valid registration request created")
    
    # Valid login
    login_req = UserLoginRequest(
        email="user@example.com",
        password="SecurePass123!"
    )
    assert login_req.email == "user@example.com"
    print("  ✓ Valid login request created")
    
    # Token response
    token_resp = TokenResponse(
        access_token="token123",
        refresh_token="refresh123",
        token_type="bearer",
        expires_in=900
    )
    assert token_resp.token_type == "bearer"
    print("  ✓ Token response created")
    
    print("✅ Schema validation tests passed")
except Exception as e:
    print(f"❌ Schema validation test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "="*60)
print("✅ ALL AUTH SERVICE CORE TESTS PASSED")
print("="*60)
