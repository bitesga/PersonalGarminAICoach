"""Unit tests for user_management registration and verification flows."""

import sys
from pathlib import Path

# Add parent dirs to path for imports
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "core"))

from core import user_management as um


def test_register_user():
    """Test registration of a new user."""
    did = "test_register_001"
    # Clean up first
    try:
        users = um._load_users()
        if did in users:
            users.pop(did)
            um._save_users(users)
    except Exception:
        pass

    # Register
    user = um.register_user(did)
    assert user is not None
    assert user["discord_id"] == did
    assert user["verified"] is False
    assert user["verification_code"] is not None
    print(f"[PASS] test_register_user: registered {did}")


def test_register_existing_user_unchanged():
    """Test that registering an already-verified user doesn't change them."""
    did = "test_existing_001"
    # Clean up
    try:
        users = um._load_users()
        if did in users:
            users.pop(did)
            um._save_users(users)
    except Exception:
        pass

    # Register and verify
    user1 = um.register_user(did)
    code = user1["verification_code"]
    um.verify_user(did, code)

    # Register again - should return existing verified user
    user2 = um.register_user(did)
    assert user2["verified"] is True
    assert "verification_code" not in user2  # Should be removed after verification
    print(f"[PASS] test_register_existing_user_unchanged: verified user unchanged")


def test_verify_user():
    """Test verification with correct code."""
    did = "test_verify_001"
    # Clean up
    try:
        users = um._load_users()
        if did in users:
            users.pop(did)
            um._save_users(users)
    except Exception:
        pass

    # Register
    user = um.register_user(did)
    code = user["verification_code"]
    
    # Verify
    ok = um.verify_user(did, code)
    assert ok is True
    
    # Check user is marked verified
    verified_user = um.get_user(did)
    assert verified_user["verified"] is True
    assert "verification_code" not in verified_user
    print(f"[PASS] test_verify_user: verified with correct code")


def test_verify_user_wrong_code():
    """Test verification fails with incorrect code."""
    did = "test_verify_wrong_001"
    # Clean up
    try:
        users = um._load_users()
        if did in users:
            users.pop(did)
            um._save_users(users)
    except Exception:
        pass

    # Register
    user = um.register_user(did)
    
    # Try wrong code
    ok = um.verify_user(did, "000000")
    assert ok is False
    
    # User should still be unverified
    unverified_user = um.get_user(did)
    assert unverified_user["verified"] is False
    print(f"[PASS] test_verify_user_wrong_code: rejected wrong code")


def test_get_user_nonexistent():
    """Test getting a nonexistent user returns None."""
    did = "test_nonexistent_12345"
    user = um.get_user(did)
    assert user is None
    print(f"[PASS] test_get_user_nonexistent: returned None")


def test_update_user():
    """Test updating user data."""
    did = "test_update_001"
    # Clean up
    try:
        users = um._load_users()
        if did in users:
            users.pop(did)
            um._save_users(users)
    except Exception:
        pass

    # Register
    user = um.register_user(did)
    
    # Update
    updated = um.update_user(did, {"garmin_connected": True, "profile": {"goal": "Marathon"}})
    assert updated is not None
    assert updated["garmin_connected"] is True
    assert updated["profile"]["goal"] == "Marathon"
    print(f"[PASS] test_update_user: updated user data")


def test_list_users():
    """Test listing all users."""
    # Register a test user if needed
    did = "test_list_001"
    try:
        users = um._load_users()
        if did not in users:
            um.register_user(did)
    except Exception:
        pass
    
    users = um.list_users()
    assert isinstance(users, list)
    assert len(users) > 0
    print(f"[PASS] test_list_users: listed {len(users)} users")


def test_verification_code_format():
    """Test that verification codes are numeric strings of expected length."""
    did = "test_code_format_001"
    # Clean up
    try:
        users = um._load_users()
        if did in users:
            users.pop(did)
            um._save_users(users)
    except Exception:
        pass

    user = um.register_user(did)
    code = user["verification_code"]
    assert isinstance(code, str)
    assert code.isdigit()
    assert len(code) == 6  # Default format: 6 digits
    print(f"[PASS] test_verification_code_format: code is 6-digit numeric string")


if __name__ == "__main__":
    test_register_user()
    test_register_existing_user_unchanged()
    test_verify_user()
    test_verify_user_wrong_code()
    test_get_user_nonexistent()
    test_update_user()
    test_list_users()
    test_verification_code_format()
    print("\n[SUCCESS] All tests passed!")
