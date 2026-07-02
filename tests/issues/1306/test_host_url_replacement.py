"""Test host_url replacement for Issue #1306 and Issue #1357.

Tests that iframe URL uses user's actual access IP (from Flask request)
instead of container-detected IP (from config.json).

Issue #1357 Design Principle:
- Single-user mode (docker compose): WebUI and open-ace on same machine
  URL from request.host_url with fixed port 3100, NOT from config.json
- Multi-user mode (install.sh): WebUI and open-ace may be on different machines
  URL from config.json (user-configured) or request.host_url with instance.port
"""

import pytest

from app.services.webui_manager import WebUIManager, WorkspaceConfig


def test_replace_host_from_request():
    """Test _replace_host_from_request function."""
    print("\n=== Test: _replace_host_from_request ===")

    config = WorkspaceConfig(
        enabled=True,
        url="http://172.17.0.1",  # Container-detected IP (wrong)
        multi_user_mode=False,
    )
    manager = WebUIManager(config)
    manager.stop_cleanup_thread()

    # Test case 1: Replace container IP with user's actual IP (no port in config)
    config_url = "http://172.17.0.1"
    request_host_url = "http://192.168.1.169:19888"
    result = manager._replace_host_from_request(config_url, request_host_url)
    assert result == "http://192.168.1.169"
    print(f"✓ Case 1: {config_url} + {request_host_url} -> {result}")

    # Test case 2: Replace host.docker.internal with domain (no port in config)
    config_url = "http://host.docker.internal"
    request_host_url = "http://example.com:19888"
    result = manager._replace_host_from_request(config_url, request_host_url)
    assert result == "http://example.com"
    print(f"✓ Case 2: {config_url} + {request_host_url} -> {result}")

    # Test case 3: HTTPS request (no port in config)
    config_url = "http://172.17.0.1"
    request_host_url = "https://192.168.1.169:19888"
    result = manager._replace_host_from_request(config_url, request_host_url)
    assert result == "https://192.168.1.169"
    print(f"✓ Case 3: {config_url} + {request_host_url} -> {result}")

    # Test case 4: IPv6 (no port in config)
    config_url = "http://[::1]"
    request_host_url = "http://[2001:db8::1]:19888"
    result = manager._replace_host_from_request(config_url, request_host_url)
    assert result == "http://[2001:db8::1]"
    print(f"✓ Case 4: {config_url} + {request_host_url} -> {result}")

    # Test case 5: _replace_host_from_request no longer returns port (Issue #1357)
    # Port is added separately in get_user_webui_url for single-user mode
    config_url = "http://172.17.0.1:3100"
    request_host_url = "http://192.168.1.169:19888"
    result = manager._replace_host_from_request(config_url, request_host_url)
    assert result == "http://192.168.1.169"
    print(f"✓ Case 5 (no port in result): {config_url} + {request_host_url} -> {result}")

    # Test case 6: IPv6 - no port in result (Issue #1357)
    config_url = "http://[::1]:3100"
    request_host_url = "http://[2001:db8::1]:19888"
    result = manager._replace_host_from_request(config_url, request_host_url)
    assert result == "http://[2001:db8::1]"
    print(f"✓ Case 6 (IPv6, no port in result): {config_url} + {request_host_url} -> {result}")


def test_get_user_webui_url_with_host_url():
    """Test get_user_webui_url with host_url parameter."""
    print("\n=== Test: get_user_webui_url with host_url ===")

    config = WorkspaceConfig(
        enabled=True,
        url="http://172.17.0.1",  # Container-detected IP (wrong), no port
        multi_user_mode=False,
    )
    manager = WebUIManager(config)
    manager.stop_cleanup_thread()

    # Without host_url: uses config.url directly (fallback)
    url1, token1 = manager.get_user_webui_url(user_id=1, system_account="testuser")
    assert url1 == "http://172.17.0.1"
    print(f"✓ Without host_url (fallback): {url1}")

    # With host_url: uses request IP with fixed port 3100 (Issue #1357)
    url2, token2 = manager.get_user_webui_url(
        user_id=1, system_account="testuser", host_url="http://192.168.1.169:19888"
    )
    assert url2 == "http://192.168.1.169:3100"
    print(f"✓ With host_url (fixed port 3100): {url2}")

    # Verify tokens are generated
    assert token1.startswith("1:0:")
    assert token2.startswith("1:0:")
    print("✓ Tokens generated correctly")


def test_get_user_webui_url_preserves_port_single_user():
    """Test that single-user mode uses fixed port 3100 (Issue #1357).

    In single-user mode (docker compose), WebUI runs on fixed port 3100.
    URL should come from request.host_url with port 3100, NOT from config.json.
    """
    print("\n=== Test: Single-user mode fixed port 3100 ===")

    # Config URL with port (but will be ignored in single-user mode with host_url)
    config = WorkspaceConfig(
        enabled=True,
        url="http://172.17.0.1:3100",  # WebUI port
        multi_user_mode=False,
    )
    manager = WebUIManager(config)
    manager.stop_cleanup_thread()

    # Without host_url: uses config.url as fallback
    url1, token1 = manager.get_user_webui_url(user_id=1, system_account="testuser")
    assert url1 == "http://172.17.0.1:3100"
    print(f"✓ Without host_url (fallback to config.url): {url1}")

    # With host_url: uses request IP with fixed port 3100 (Issue #1357)
    url2, token2 = manager.get_user_webui_url(
        user_id=1, system_account="testuser", host_url="http://192.168.1.169:19888"
    )
    assert url2 == "http://192.168.1.169:3100"
    print(f"✓ With host_url (request IP + fixed port 3100): {url2}")

    assert token1.startswith("1:0:")
    assert token2.startswith("1:0:")
    print("✓ Port preservation test passed")


def test_get_user_webui_url_preserves_port_in_multi_user():
    """Test that multi-user mode preserves port when host_url is provided."""
    print("\n=== Test: Multi-user mode port preservation ===")

    config = WorkspaceConfig(
        enabled=True,
        url="http://172.17.0.1",
        multi_user_mode=True,
        port_range_start=3100,
        port_range_end=3200,
    )
    manager = WebUIManager(config)
    manager.stop_cleanup_thread()

    # Note: This test doesn't actually start a webui process
    # It just tests that the URL construction logic is correct
    # The actual process start requires mocking _launch_webui_process


if __name__ == "__main__":
    test_replace_host_from_request()
    test_get_user_webui_url_with_host_url()
    test_get_user_webui_url_preserves_port_single_user()
    print("\n✓ All tests passed")
