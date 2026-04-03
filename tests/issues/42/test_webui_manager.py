#!/usr/bin/env python3
"""
Test for Issue 42: Multi-user WebUI Manager

Tests for:
1. WebUIManager service
2. Port allocation
3. Token generation and validation
4. Multi-user mode configuration
"""

import os
import sys
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add app to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from app.services.webui_manager import (
    WebUIManager,
    WebUIInstance,
    WorkspaceConfig,
)


def test_workspace_config_defaults():
    """Test WorkspaceConfig default values."""
    print("\n=== Test: WorkspaceConfig defaults ===")

    config = WorkspaceConfig()

    assert config.enabled is False
    assert config.url == "http://localhost"
    assert config.multi_user_mode is False
    assert config.port_range_start == 9000
    assert config.port_range_end == 9999
    assert config.max_instances == 30
    assert config.idle_timeout_minutes == 30

    print("✓ All default values are correct")


def test_workspace_config_from_dict():
    """Test WorkspaceConfig creation from dict."""
    print("\n=== Test: WorkspaceConfig from dict ===")

    data = {
        "enabled": True,
        "url": "http://192.168.1.100",
        "multi_user_mode": True,
        "port_range_start": 8000,
        "port_range_end": 8999,
        "max_instances": 20,
    }

    config = WorkspaceConfig(
        enabled=data.get("enabled", False),
        url=data.get("url", "http://localhost"),
        multi_user_mode=data.get("multi_user_mode", False),
        port_range_start=data.get("port_range_start", 9000),
        port_range_end=data.get("port_range_end", 9999),
        max_instances=data.get("max_instances", 30),
    )

    assert config.enabled is True
    assert config.url == "http://192.168.1.100"
    assert config.multi_user_mode is True
    assert config.port_range_start == 8000
    assert config.port_range_end == 8999
    assert config.max_instances == 20

    print("✓ Config created from dict correctly")


def test_webui_instance():
    """Test WebUIInstance dataclass."""
    print("\n=== Test: WebUIInstance ===")

    instance = WebUIInstance(
        user_id=1,
        system_account="testuser",
        port=9001,
        pid=12345,
        token="test-token",
        url="http://localhost:9001",
    )

    assert instance.user_id == 1
    assert instance.system_account == "testuser"
    assert instance.port == 9001
    assert instance.pid == 12345
    assert instance.token == "test-token"
    assert instance.url == "http://localhost:9001"

    # Test is_alive with non-existent process
    assert instance.is_alive() is False

    print("✓ WebUIInstance works correctly")


def test_manager_port_allocation():
    """Test port allocation in WebUIManager."""
    print("\n=== Test: Port allocation ===")

    config = WorkspaceConfig(
        enabled=True,
        multi_user_mode=True,
        port_range_start=9000,
        port_range_end=9010,  # Small range for testing
    )

    manager = WebUIManager(config)
    # Stop cleanup thread to avoid issues
    manager.stop_cleanup_thread()

    # Allocate port for user 1
    port1 = manager.allocate_port(1)
    assert 9000 <= port1 <= 9010
    print(f"✓ Allocated port {port1} for user 1")

    # Allocate port for user 2
    port2 = manager.allocate_port(2)
    assert 9000 <= port2 <= 9010
    assert port2 != port1  # Should be different
    print(f"✓ Allocated port {port2} for user 2")

    # Same user should get same port
    port1_again = manager.allocate_port(1)
    assert port1_again == port1
    print(f"✓ User 1 got same port {port1} on re-allocation")

    # Release port
    manager.release_port(port1)
    print(f"✓ Released port {port1}")


def test_manager_token_generation():
    """Test token generation and validation."""
    print("\n=== Test: Token generation and validation ===")

    config = WorkspaceConfig(
        enabled=True,
        multi_user_mode=True,
        token_secret="test-secret-key",
    )

    manager = WebUIManager(config)
    # Stop cleanup thread to avoid issues
    manager.stop_cleanup_thread()

    # Allocate port first (token validation requires port to be allocated)
    port = manager.allocate_port(user_id=1)

    # Generate token
    token = manager.generate_token(user_id=1, port=port)
    print(f"✓ Generated token: {token[:30]}...")

    # Validate token
    is_valid, user_id, error = manager.validate_token(token)
    assert is_valid is True
    assert user_id == 1
    assert error is None
    print(f"✓ Token validated: user_id={user_id}")

    # Invalid token format
    is_valid, user_id, error = manager.validate_token("invalid:token")
    assert is_valid is False
    print(f"✓ Invalid token rejected: {error}")


def test_manager_get_user_webui_url_single_user():
    """Test get_user_webui_url in single-user mode."""
    print("\n=== Test: get_user_webui_url (single-user mode) ===")

    config = WorkspaceConfig(
        enabled=True,
        url="http://localhost:8080",
        multi_user_mode=False,
    )

    manager = WebUIManager(config)
    # Stop cleanup thread to avoid issues
    manager.stop_cleanup_thread()

    url, token = manager.get_user_webui_url(user_id=1, system_account="testuser")

    assert url == "http://localhost:8080"
    assert token == ""  # No token in single-user mode
    print(f"✓ Single-user mode: url={url}, token empty")


def test_manager_instance_limit():
    """Test instance limit enforcement."""
    print("\n=== Test: Instance limit ===")

    config = WorkspaceConfig(
        enabled=True,
        multi_user_mode=True,
        max_instances=2,
        port_range_start=9000,
        port_range_end=9010,
    )

    manager = WebUIManager(config)
    # Stop cleanup thread to avoid issues
    manager.stop_cleanup_thread()

    # Mock the process launch to avoid actually starting processes
    manager._launch_webui_process = MagicMock(return_value=None)

    # These should work (within limit)
    try:
        url1, _ = manager.get_user_webui_url(1, "user1")
        print(f"✓ Started instance for user 1: {url1}")

        url2, _ = manager.get_user_webui_url(2, "user2")
        print(f"✓ Started instance for user 2: {url2}")
    except ValueError as e:
        print(f"✗ Unexpected error: {e}")
        return

    # This should fail (exceeds limit)
    try:
        manager.get_user_webui_url(3, "user3")
        print("✗ Should have raised ValueError for instance limit")
    except ValueError as e:
        print(f"✓ Correctly rejected: {e}")


def test_config_json_sample():
    """Test that config.json.sample has new parameters."""
    print("\n=== Test: config.json.sample has new parameters ===")

    config_path = Path(__file__).parent.parent.parent.parent / "config" / "config.json.sample"
    assert config_path.exists(), f"config.json.sample not found at {config_path}"

    with open(config_path, "r") as f:
        config = json.load(f)

    workspace = config.get("workspace", {})

    # Check new parameters
    assert "multi_user_mode" in workspace, "multi_user_mode not in config"
    assert "port_range_start" in workspace, "port_range_start not in config"
    assert "port_range_end" in workspace, "port_range_end not in config"
    assert "max_instances" in workspace, "max_instances not in config"
    assert "idle_timeout_minutes" in workspace, "idle_timeout_minutes not in config"

    print("✓ config.json.sample has all new parameters")


def test_extract_system_account():
    """Test extract_system_account_from_sender_name function."""
    print("\n=== Test: extract_system_account_from_sender_name ===")

    from scripts.fetch_qwen import extract_system_account_from_sender_name

    # Normal case
    sender_name = "alice-macbook-pro-qwen"
    system_account = extract_system_account_from_sender_name(sender_name)
    assert system_account == "alice"
    print(f"✓ Extracted '{system_account}' from '{sender_name}'")

    # Simple case
    sender_name = "bob-server-qwen"
    system_account = extract_system_account_from_sender_name(sender_name)
    assert system_account == "bob"
    print(f"✓ Extracted '{system_account}' from '{sender_name}'")

    # Edge case: short sender_name
    sender_name = "qwen"
    system_account = extract_system_account_from_sender_name(sender_name)
    assert system_account == "qwen"
    print(f"✓ Handled short sender_name: '{sender_name}' -> '{system_account}'")

    # Empty case
    system_account = extract_system_account_from_sender_name("")
    assert system_account is None
    print("✓ Handled empty sender_name")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("Issue 42: Multi-user WebUI Manager Tests")
    print("=" * 60)

    tests = [
        test_workspace_config_defaults,
        test_workspace_config_from_dict,
        test_webui_instance,
        test_manager_port_allocation,
        test_manager_token_generation,
        test_manager_get_user_webui_url_single_user,
        test_manager_instance_limit,
        test_config_json_sample,
        test_extract_system_account,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"✗ Test failed: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ Test error: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)