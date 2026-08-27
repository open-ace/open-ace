"""Host URL replacement tests for the WebUI manager (Issues #1306 and #1357).

Tests that the iframe URL uses the user's actual access IP (from the Flask
request) instead of the container-detected IP (from config.json).

Issue #1357 Design Principle:
- Single-user mode (docker compose): WebUI and open-ace on same machine
  URL from request.host_url with fixed port 3100, NOT from config.json
- Multi-user mode (install.sh): WebUI and open-ace may be on different machines
  URL from config.json (user-configured) or request.host_url with instance.port

Migrated from tests/issues/1306/test_host_url_replacement.py. The former
zero-assertion placeholder test_get_user_webui_url_preserves_port_in_multi_user
now pins the multi-user port-preservation behavior of get_user_webui_url with
real assertions (fake request context + a stubbed live instance).
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask, request

from app.services.webui_manager import WebUIManager, WorkspaceConfig

pytestmark = [pytest.mark.regression, pytest.mark.issue(1306)]


def test_replace_host_from_request():
    """Test _replace_host_from_request function."""
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

    # Test case 2: Replace host.docker.internal with domain (no port in config)
    config_url = "http://host.docker.internal"
    request_host_url = "http://example.com:19888"
    result = manager._replace_host_from_request(config_url, request_host_url)
    assert result == "http://example.com"

    # Test case 3: HTTPS request (no port in config)
    config_url = "http://172.17.0.1"
    request_host_url = "https://192.168.1.169:19888"
    result = manager._replace_host_from_request(config_url, request_host_url)
    assert result == "https://192.168.1.169"

    # Test case 4: IPv6 (no port in config)
    config_url = "http://[::1]"
    request_host_url = "http://[2001:db8::1]:19888"
    result = manager._replace_host_from_request(config_url, request_host_url)
    assert result == "http://[2001:db8::1]"

    # Test case 5: _replace_host_from_request no longer returns port (Issue #1357)
    # Port is added separately in get_user_webui_url for single-user mode
    config_url = "http://172.17.0.1:3100"
    request_host_url = "http://192.168.1.169:19888"
    result = manager._replace_host_from_request(config_url, request_host_url)
    assert result == "http://192.168.1.169"

    # Test case 6: IPv6 - no port in result (Issue #1357)
    config_url = "http://[::1]:3100"
    request_host_url = "http://[2001:db8::1]:19888"
    result = manager._replace_host_from_request(config_url, request_host_url)
    assert result == "http://[2001:db8::1]"


def test_get_user_webui_url_with_host_url():
    """Test get_user_webui_url with host_url parameter."""
    config = WorkspaceConfig(
        enabled=True,
        url="http://172.17.0.1",  # Container-detected IP (wrong), no port
        multi_user_mode=False,
    )
    manager = WebUIManager(config)
    manager.stop_cleanup_thread()

    # Mock _launch_webui_process and _wait_for_service_ready to avoid starting
    # a real WebUI process in test environment (Issue #3129)
    with (
        patch.object(WebUIManager, "_launch_webui_process", return_value=(MagicMock(), {})),
        patch.object(WebUIManager, "_wait_for_service_ready", return_value=True),
    ):
        # Without host_url: uses config.url directly (fallback)
        url1, token1 = manager.get_user_webui_url(user_id=1, system_account="testuser")
        assert url1 == "http://172.17.0.1:3100"

        # With host_url: uses request IP with fixed port 3100 (Issue #1357)
        url2, token2 = manager.get_user_webui_url(
            user_id=1, system_account="testuser", host_url="http://192.168.1.169:19888"
        )
        assert url2 == "http://192.168.1.169:3100"

        # Verify tokens are generated
        assert token1.startswith("v2:1:")
        assert token2.startswith("v2:1:")


def test_get_user_webui_url_preserves_port_single_user():
    """Test that single-user mode uses fixed port 3100 (Issue #1357).

    In single-user mode (docker compose), WebUI runs on fixed port 3100.
    URL should come from request.host_url with port 3100, NOT from config.json.
    """
    # Config URL with port (but will be ignored in single-user mode with host_url)
    config = WorkspaceConfig(
        enabled=True,
        url="http://172.17.0.1:3100",  # WebUI port
        multi_user_mode=False,
    )
    manager = WebUIManager(config)
    manager.stop_cleanup_thread()

    # Mock _launch_webui_process and _wait_for_service_ready to avoid starting
    # a real WebUI process in test environment (Issue #3129)
    with (
        patch.object(WebUIManager, "_launch_webui_process", return_value=(MagicMock(), {})),
        patch.object(WebUIManager, "_wait_for_service_ready", return_value=True),
    ):
        # Without host_url: uses config.url as fallback (with port 3100)
        url1, token1 = manager.get_user_webui_url(user_id=1, system_account="testuser")
        assert url1 == "http://172.17.0.1:3100"

        # With host_url: uses request IP with fixed port 3100 (Issue #1357)
        url2, token2 = manager.get_user_webui_url(
            user_id=1, system_account="testuser", host_url="http://192.168.1.169:19888"
        )
        assert url2 == "http://192.168.1.169:3100"

        assert token1.startswith("v2:1:")
        assert token2.startswith("v2:1:")


def test_get_user_webui_url_preserves_port_in_multi_user():
    """Multi-user mode preserves the instance's own port when host_url is given.

    This test doesn't start a real webui process; the URL construction logic
    is exercised with a stubbed live instance (and, for the fresh-instance
    branch, with _launch_webui_process / readiness mocks).

    Issue #1357 multi-user principle: the host comes from the request (the
    browser-visible address), while the port stays the *instance's* dynamic
    port from the configured port range — NOT the single-user fixed 3100 and
    NOT the container-detected config.url host.
    """
    config = WorkspaceConfig(
        enabled=True,
        url="http://172.17.0.1",
        multi_user_mode=True,
        port_range_start=3100,
        port_range_end=3200,
    )
    manager = WebUIManager(config)
    manager.stop_cleanup_thread()

    # ── Existing-instance branch: url = <request host>:<instance.port> ──
    # Inject a live instance with a distinct dynamic port and a stale
    # (container-IP) stored url; is_alive() stubbed True so the manager
    # reuses it instead of restarting.
    instance = MagicMock()
    instance.is_alive.return_value = True
    instance.port = 3123
    instance.token = "instance-token"
    instance.url = "http://172.17.0.1:3123"  # stale container-IP url
    manager._instances = {1: instance}

    # Fake request context: the browser hits 192.168.1.169:19888 while the
    # container-detected config.url says 172.17.0.1.
    app = Flask(__name__)
    with app.test_request_context(
        "/", base_url="http://192.168.1.169:19888", headers={"Host": "192.168.1.169:19888"}
    ):
        host_url = request.host_url  # "http://192.168.1.169:19888/"
        url, token = manager.get_user_webui_url(
            user_id=1, system_account="testuser", host_url=host_url
        )

    # Host replaced from the request, port taken from the instance's own
    # dynamic port (3123 — not the single-user fixed 3100).
    assert (
        url == "http://192.168.1.169:3123"
    ), "multi-user URL must be <request host>:<instance.port>"
    assert "172.17.0.1" not in url, "container-detected IP must not leak into the URL"
    assert token == "instance-token", "live instance must keep its own token"

    # Without host_url, the stored instance.url (user/machine-configured) is
    # returned unchanged — multi-user does NOT rewrite it to port 3100.
    url_no_host, token_no_host = manager.get_user_webui_url(user_id=1, system_account="testuser")
    assert url_no_host == "http://172.17.0.1:3123"
    assert token_no_host == "instance-token"

    # ── Fresh-instance branch: port comes from the configured port range ──
    manager2 = WebUIManager(config)
    manager2.stop_cleanup_thread()
    with (
        patch.object(
            WebUIManager, "_launch_webui_process", return_value=(MagicMock(), {})
        ) as mock_launch,
        patch.object(WebUIManager, "_wait_for_service_ready", return_value=True),
        patch.object(WebUIManager, "_is_port_available", return_value=True),
    ):
        url2, token2 = manager2.get_user_webui_url(
            user_id=7, system_account="testuser", host_url="http://192.168.1.169:19888"
        )

    assert url2 == "http://192.168.1.169:3100", (
        "a fresh instance must use port_range_start (3100), not fixed 3100 "
        "via the single-user path nor a request port"
    )
    assert manager2._instances[7].port == 3100
    assert manager2._instances[7].url == "http://192.168.1.169:3100"
    assert token2 == manager2._instances[7].token
    # The launched process receives the request-derived base URL (host only).
    assert mock_launch.call_args.args[3] == "http://192.168.1.169"
