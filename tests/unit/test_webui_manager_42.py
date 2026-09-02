#!/usr/bin/env python3
"""
Test for Issue 42: Multi-user WebUI Manager

Tests for:
1. WebUIManager service
2. Port allocation
3. Token generation and validation
4. Multi-user mode configuration

Migrated from tests/issues/42/test_webui_manager.py (#2429 batch 16) with the
R2 repair: ``test_manager_get_user_webui_url_single_user`` used to spawn a real
WebUI process; its launch/readiness seams are now stubbed so the test is
deterministic (see that test's comment for the full story).
"""

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.webui_manager import WebUIInstance, WebUIManager, WorkspaceConfig

pytestmark = [pytest.mark.regression, pytest.mark.issue(42)]


def test_workspace_config_defaults():
    """Test WorkspaceConfig default values."""
    print("\n=== Test: WorkspaceConfig defaults ===")

    config = WorkspaceConfig()

    assert config.enabled is False
    assert config.url == "http://localhost"
    assert config.multi_user_mode is False
    assert config.port_range_start == 3100
    assert config.port_range_end == 3200
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
        port_range_start=data.get("port_range_start", 3100),
        port_range_end=data.get("port_range_end", 3200),
        max_instances=data.get("max_instances", 30),
    )

    assert config.enabled is True
    assert config.url == "http://192.168.1.100"
    assert config.multi_user_mode is True
    assert config.port_range_start == 8000
    assert config.port_range_end == 8999
    assert config.max_instances == 20

    print("✓ Config created from dict correctly")


def test_webui_instance(monkeypatch):
    """Test WebUIInstance dataclass.

    is_alive() must be verified deterministically (#3305): it probes the pid
    with os.kill(pid, 0), and the hardcoded pid 12345 is genuinely occupied
    on some busy CI runners, which made this test fail there (the probe
    succeeds, the HTTP health check fails but stays under the 10-failure
    death budget, and is_alive() returns True). Stub the signal so each
    branch of is_alive() is exercised on demand instead of hoping the
    ambient pid table cooperates.
    """
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

    # Dead branch: the signal says no such process.
    def _no_such_process(pid, sig):
        raise ProcessLookupError(f"simulated dead pid {pid}")

    monkeypatch.setattr(os, "kill", _no_such_process)
    assert instance.is_alive() is False

    # Alive branch: the pid is signalable and the health cache is fresh, so
    # is_alive() returns True from the TTL cache without any HTTP traffic.
    # The failure-count guard pins that: any HTTP probe (success or failure)
    # mutates the counter, so it staying 0 proves the cache path was taken.
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    instance._last_health_check = time.time()
    assert instance.is_alive() is True
    assert instance._consecutive_health_failures == 0

    # No pid at all.
    assert WebUIInstance(user_id=2, system_account="other", port=9002).is_alive() is False

    print("✓ WebUIInstance works correctly")


def test_manager_port_allocation():
    """Test port allocation in WebUIManager."""
    print("\n=== Test: Port allocation ===")

    config = WorkspaceConfig(
        enabled=True,
        multi_user_mode=True,
        port_range_start=3100,
        port_range_end=3110,  # Small range for testing
    )

    manager = WebUIManager(config)
    # Stop cleanup thread to avoid issues
    manager.stop_cleanup_thread()

    # Allocate port for user 1
    port1 = manager.allocate_port(1)
    assert 3100 <= port1 <= 3110
    print(f"✓ Allocated port {port1} for user 1")

    # Allocate port for user 2
    port2 = manager.allocate_port(2)
    assert 3100 <= port2 <= 3110
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

    # R2 repair (#2429 batch 16): the original test spawned a REAL WebUI
    # process here. Without a qwen-code-webui executable the launch returns
    # None and _start_single_user_instance raises ValueError ("Failed to
    # launch single-user WebUI process"), making the test env-dependent.
    # Stub the launch/readiness seams (same pattern as
    # test_manager_instance_limit) so the single-user start path runs
    # deterministically without any real subprocess.
    def fake_launch(user_id, system_account, port, base_url):
        process = MagicMock()
        process.pid = os.getpid()  # signalable pid for WebUIInstance.is_alive()
        return process, MagicMock()

    manager._launch_webui_process = MagicMock(side_effect=fake_launch)
    manager._wait_for_service_ready = MagicMock(return_value=True)

    url, token = manager.get_user_webui_url(user_id=1, system_account="testuser")

    # Single-user mode pins the WebUI to the fixed port 3100 (Issue #3129):
    # the config URL's host is kept but its port is replaced with 3100.
    assert url == "http://localhost:3100"
    # Token is generated for iframe auth in cross-origin API calls
    # v2 format: v2:user_id:port:timestamp:random:signature
    assert token.startswith("v2:1:3100:")
    print(f"✓ Single-user mode: url={url}, token={token[:20]}...")


def test_manager_instance_limit():
    """Test instance limit enforcement."""
    print("\n=== Test: Instance limit ===")

    config = WorkspaceConfig(
        enabled=True,
        multi_user_mode=True,
        max_instances=2,
        port_range_start=3100,
        port_range_end=3110,
    )

    manager = WebUIManager(config)
    # Stop cleanup thread to avoid issues
    manager.stop_cleanup_thread()

    # Mock the process launch to avoid actually starting processes.
    # _start_instance_internal unpacks (process, model_pool) and treats a
    # None process as a launch failure. WebUIInstance.is_alive() signals the
    # pid with os.kill(pid, 0), so every fake must carry a signalable pid;
    # the HTTP health probe then fails but stays under the 10-failure death
    # budget, keeping each instance alive for the limit accounting. The
    # readiness wait is stubbed so no real listener is required.
    def fake_launch(user_id, system_account, port, base_url):
        process = MagicMock()
        process.pid = os.getpid()
        return process, MagicMock()

    manager._launch_webui_process = MagicMock(side_effect=fake_launch)
    manager._wait_for_service_ready = MagicMock(return_value=True)

    # Within limit: two instances start and are registered
    url1, _ = manager.get_user_webui_url(1, "user1")
    assert url1 == "http://localhost:3100", f"unexpected url for user 1: {url1}"

    url2, _ = manager.get_user_webui_url(2, "user2")
    assert url2 == "http://localhost:3101", f"unexpected url for user 2: {url2}"

    # Third instance exceeds max_instances=2 and is rejected
    with pytest.raises(ValueError, match=r"Maximum instances \(2\) reached"):
        manager.get_user_webui_url(3, "user3")


def test_config_json_sample():
    """Test that config.json.sample has new parameters."""
    print("\n=== Test: config.json.sample has new parameters ===")

    config_path = Path(__file__).resolve().parents[2] / "config" / "config.json.sample"
    assert config_path.exists(), f"config.json.sample not found at {config_path}"

    with open(config_path) as f:
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
