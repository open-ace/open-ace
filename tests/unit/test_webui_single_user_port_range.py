"""The single-user WebUI launch must honor the configured port range.

Registered leftover ("single-user 3100 launch limit", #3379 acceptance tail
/ handbook §5.8): the single-user instance hardcoded port 3100 regardless of
``workspace.port_range_start``. Two concrete shapes broke:

1. **Offset-range deployments** — the #3379 acceptance runs the single-user
   stack beside the multi-user stack with ``WORKSPACE_PORT_RANGE``
   13100-13200 (host 3100-3200 is held by the multi-user stack, a second
   docker-proxy bind aborts the ``up``). The hardcoded 3100 bound an
   UNPUBLISHED container port while the URL advertised ``<host>:3100`` —
   unreachable at its own advertised address (handbook §5.8 declared the
   consequence: "the single-user webui is not reachable at its advertised
   host URL").
2. **Busy 3100** — no availability pre-check ran, and the connect-based
   readiness probe succeeds against ANY listener, so a foreign service on
   3100 would be wired in as "our webui".

The fix: the single-user port is the FIRST FREE port of the configured
range (same ``_is_port_available`` probe the multi-user ``allocate_port``
uses), and the URL/token follow the instance's actual port. Default
deployments are unchanged (the default range starts at 3100).

The "many launches" reading was investigated and is NOT a defect:
single-user mode has exactly ONE shared instance (``_single_user_instance``,
never reaped), reused idempotently by every user — there is no per-launch
allocation to exhaust. The exhaustion error raised here can only fire when
the ENTIRE configured range is occupied.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.webui_manager import WebUIManager, WorkspaceConfig

pytestmark = [pytest.mark.regression, pytest.mark.issue(3379)]


def _manager(**config_kwargs) -> WebUIManager:
    config = WorkspaceConfig(
        enabled=True,
        url="http://localhost:8080",
        multi_user_mode=False,
        **config_kwargs,
    )
    manager = WebUIManager(config)
    manager.stop_cleanup_thread()
    return manager


def _stub_launch(manager):
    """Stub the process launch + readiness (no real webui, no listener).

    The fake process carries os.getpid() so WebUIInstance.is_alive()'s
    os.kill(pid, 0) probe sees it alive — the reuse scenario depends on it
    (same pattern as test_webui_manager_42.test_manager_instance_limit)."""
    import os

    def fake_launch(user_id, system_account, port, base_url):
        process = MagicMock()
        process.pid = os.getpid()
        return process, MagicMock()

    manager._launch_webui_process = MagicMock(side_effect=fake_launch)
    manager._wait_for_service_ready = MagicMock(return_value=True)


def test_single_user_port_follows_configured_range_start():
    """Offset-range deployment (§5.8 acceptance shape): the instance binds
    and ADVERTISES the range's start — the URL is reachable at the host's
    published mapping instead of the historical :3100."""
    manager = _manager(port_range_start=13100, port_range_end=13200)
    _stub_launch(manager)
    manager._is_port_available = MagicMock(return_value=True)

    url, token = manager.get_user_webui_url(user_id=1, system_account="open-ace")

    assert url == "http://localhost:13100", (
        "the advertised port must follow the configured range, not the "
        "hardcoded 3100 (an offset-range stack publishes 13100-13200 only)"
    )
    assert manager._single_user_instance.port == 13100
    assert token.startswith("v2:1:13100:"), "the token embeds the same port"


def test_single_user_skips_busy_start_port():
    """A busy range start falls through to the next free port — no silent
    wiring onto a foreign listener occupying the historical 3100."""
    manager = _manager(port_range_start=3100, port_range_end=3200)
    _stub_launch(manager)
    manager._is_port_available = lambda port: port != 3100

    url, _token = manager.get_user_webui_url(user_id=1, system_account="open-ace")

    assert url == "http://localhost:3101"
    assert manager._single_user_instance.port == 3101


def test_single_user_exhausted_range_raises():
    """Whole range busy → the loud ValueError (same shape as the multi-user
    allocate_port exhaustion), not a false-positive readiness against a
    foreign listener."""
    manager = _manager(port_range_start=3100, port_range_end=3102)
    _stub_launch(manager)
    manager._is_port_available = MagicMock(return_value=False)

    with pytest.raises(ValueError, match=r"No available ports in range 3100-3102"):
        manager.get_user_webui_url(user_id=1, system_account="open-ace")
    # Nothing was half-registered.
    assert manager._single_user_instance is None


def test_single_user_default_range_starts_at_3100():
    """Default deployments are unchanged: default range start is 3100, so
    the shared instance still lands on 3100 when free."""
    manager = _manager()
    _stub_launch(manager)
    manager._is_port_available = MagicMock(return_value=True)

    url, _token = manager.get_user_webui_url(user_id=1, system_account="open-ace")
    assert url == "http://localhost:3100"


def test_single_user_reuses_instance_port_across_users():
    """One shared instance: the SECOND user's URL/token reuse the already
    running instance's port (no re-allocation, no drift between users)."""
    manager = _manager(port_range_start=13100, port_range_end=13200)
    _stub_launch(manager)
    manager._is_port_available = MagicMock(return_value=True)

    url1, _ = manager.get_user_webui_url(user_id=1, system_account="open-ace")
    # Second hit: make every port look busy — reuse must not re-allocate.
    manager._is_port_available = MagicMock(return_value=False)
    url2, token2 = manager.get_user_webui_url(user_id=2, system_account="open-ace")

    assert url2 == url1 == "http://localhost:13100"
    assert token2.startswith("v2:2:13100:")
    manager._launch_webui_process.assert_called_once()
