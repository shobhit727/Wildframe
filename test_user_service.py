"""Quick test script for User Service core functionality."""
import sys
sys.path.insert(0, '/home/phoenix/Desktop/wildframe/services/user-service')

# Test imports
try:
    from app.schemas import (
        UserProfileUpdateRequest,
        UserProfileResponse,
        UserDeviceResponse,
        UserPreferenceResponse
    )
    print("✅ User Service imports successful")
except Exception as e:
    print(f"❌ Import failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# Test schema creation
try:
    from uuid import uuid4
    from datetime import datetime
    
    user_id = uuid4()
    
    # Test profile update request
    profile_update = UserProfileUpdateRequest(
        avatar_url="https://example.com/avatar.jpg",
        bio="Test bio",
        phone_number="+1234567890",
        country="US",
        language="en",
        public_profile=True
    )
    assert profile_update.bio == "Test bio"
    print("  ✓ User profile update request created")
    
    # Test device response
    device_response = UserDeviceResponse(
        id=uuid4(),
        user_id=user_id,
        device_id="device-123",
        device_name="MacBook Pro",
        device_type="web",
        is_active=True,
        is_trusted=True,
        last_active_at=datetime.now()
    )
    assert device_response.device_name == "MacBook Pro"
    print("  ✓ User device response created")
    
    # Test preference response
    pref_response = UserPreferenceResponse(
        id=uuid4(),
        user_id=user_id,
        theme="dark",
        language="en",
        autoplay_enabled=True,
        default_playback_quality="1080p"
    )
    assert pref_response.theme == "dark"
    print("  ✓ User preference response created")
    
    print("✅ User Service schema tests passed")
except Exception as e:
    print(f"❌ Schema test failed: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print("\n" + "="*60)
print("✅ ALL USER SERVICE CORE TESTS PASSED")
print("="*60)
