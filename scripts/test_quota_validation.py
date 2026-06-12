"""
Quick test script to verify quota validation logic works correctly.

Run this to test the validation without pytest:
    python scripts/test_quota_validation.py
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.schemas.quota import (
    validate_token_quota,
    validate_request_quota,
    validate_quota_update,
    MAX_TOKEN_QUOTA,
    MAX_REQUEST_QUOTA,
)


def test_basic_validation():
    """Test basic validation cases."""
    print("Testing quota validation...")

    # Test None (unlimited)
    print("\n1. Testing None (unlimited):")
    is_valid, error = validate_token_quota(None, "daily_token_quota")
    assert is_valid, f"None should be valid, got error: {error}"
    print("   ✓ None is valid")

    # Test zero
    print("\n2. Testing zero:")
    is_valid, error = validate_token_quota(0, "daily_token_quota")
    assert is_valid, f"Zero should be valid, got error: {error}"
    print("   ✓ Zero is valid")

    # Test valid value within limit
    print("\n3. Testing valid value (100M):")
    is_valid, error = validate_token_quota(100, "daily_token_quota")
    assert is_valid, f"100 should be valid, got error: {error}"
    print("   ✓ 100M is valid")

    # Test value at max limit
    print("\n4. Testing value at max limit ({MAX_TOKEN_QUOTA}M):")
    is_valid, error = validate_token_quota(MAX_TOKEN_QUOTA, "daily_token_quota")
    assert is_valid, f"Max limit should be valid, got error: {error}"
    print(f"   ✓ {MAX_TOKEN_QUOTA}M is valid")

    # Test negative value
    print("\n5. Testing negative value (-1):")
    is_valid, error = validate_token_quota(-1, "daily_token_quota")
    assert not is_valid, "Negative value should be invalid"
    assert "negative" in error, f"Error message should mention 'negative', got: {error}"
    print("   ✓ Negative value correctly rejected")

    # Test value exceeding max
    print("\n6. Testing value exceeding max ({MAX_TOKEN_QUOTA + 1}M):")
    is_valid, error = validate_token_quota(MAX_TOKEN_QUOTA + 1, "daily_token_quota")
    assert not is_valid, "Value exceeding max should be invalid"
    assert "exceeds maximum limit" in error, f"Error message should mention limit, got: {error}"
    print("   ✓ Value exceeding max correctly rejected")

    # Test very large value (simulating 1e21 issue)
    print("\n7. Testing very large value (1e21):")
    huge_value = 10**21
    is_valid, error = validate_token_quota(huge_value, "daily_token_quota")
    assert not is_valid, "Huge value should be invalid"
    assert "exceeds maximum limit" in error, f"Error message should mention limit, got: {error}"
    print(f"   ✓ Huge value {huge_value} correctly rejected")

    # Test request quota
    print("\n8. Testing request quota validation:")
    is_valid, error = validate_request_quota(1000, "daily_request_quota")
    assert is_valid, f"1000 requests should be valid, got error: {error}"
    print("   ✓ Valid request quota accepted")

    is_valid, error = validate_request_quota(MAX_REQUEST_QUOTA + 1, "daily_request_quota")
    assert not is_valid, "Request quota exceeding max should be invalid"
    print("   ✓ Request quota exceeding max rejected")

    # Test complete update validation
    print("\n9. Testing complete quota update validation:")
    is_valid, errors = validate_quota_update(
        daily_token_quota=100,
        monthly_token_quota=300,
        daily_request_quota=1000,
        monthly_request_quota=30000,
    )
    assert is_valid, f"Valid update should pass, got errors: {errors}"
    print("   ✓ Valid complete update accepted")

    is_valid, errors = validate_quota_update(
        daily_token_quota=-1,  # Invalid
        monthly_token_quota=MAX_TOKEN_QUOTA + 1,  # Invalid
        daily_request_quota=1000,
        monthly_request_quota=30000,
    )
    assert not is_valid, "Update with invalid values should fail"
    assert "daily_token_quota" in errors, "Should have error for daily_token_quota"
    assert "monthly_token_quota" in errors, "Should have error for monthly_token_quota"
    print("   ✓ Invalid complete update rejected with detailed errors")

    print("\n✅ All tests passed!")


if __name__ == "__main__":
    try:
        test_basic_validation()
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)