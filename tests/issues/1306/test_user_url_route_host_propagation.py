"""Route-level regression test for Issue #1306.

The existing tests in test_host_url_replacement.py cover the *manager* layer
(`WebUIManager._replace_host_from_request` / `get_user_webui_url`), but the
regression that caused blank workspace iframes happened at the *route wiring*
layer: `app/routes/workspace.py::get_user_webui_url` stopped forwarding
`request.host_url` to the manager, so the manager fell back to the
container-detected `config.url` (e.g. 0.250.250.254) which the browser cannot
reach.

These tests lock down the wiring: a request to `/api/workspace/user-url` with a
custom Host header must return a URL built from that host, not from `config.url`.
"""

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

# The container-detected IP that browsers cannot reach. Config.url is seeded
# with this to prove the route overrides it with the request host.
UNREACHABLE_CONTAINER_IP = "http://0.250.250.254"

MOCK_USER = {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "role": "admin",
    "tenant_id": 1,
    "must_change_password": False,
}


@pytest.fixture
def workspace_app():
    """Flask app with only the workspace blueprint registered (url_prefix
    /api/workspace, matching the real registration)."""
    from app.routes.workspace import workspace_bp

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(workspace_bp, url_prefix="/api/workspace")
    return app


def _stub_manager():
    """A real WebUIManager configured with a wrong (container-detected) IP.

    Using a real manager (not a MagicMock) means the assertion exercises the
    actual _replace_host_from_request code path — only the factory that
    supplies the manager is mocked.
    """
    from app.services.webui_manager import WebUIManager, WorkspaceConfig

    manager = WebUIManager(
        WorkspaceConfig(
            enabled=True,
            url=UNREACHABLE_CONTAINER_IP,
            multi_user_mode=False,
        )
    )
    manager.stop_cleanup_thread()
    return manager


def _authed_get(client, path, host):
    """GET with auth stubbed so the blueprint's load_user before_request passes.

    Patches _extract_token / _load_user_from_token (module-level imports in
    workspace.py) so the real auth chain runs end-to-end and sets g.user.
    """
    with (
        patch("app.routes.workspace._extract_token", return_value="test-token"),
        patch("app.routes.workspace._load_user_from_token", return_value=MOCK_USER),
    ):
        return client.get(path, headers={"Host": host})


@patch("app.services.webui_manager.get_webui_manager")
@patch("app.repositories.user_repo.UserRepository.get_user_by_id")
def test_user_url_route_uses_request_host_not_config_ip(
    mock_get_user, mock_get_manager, workspace_app
):
    """GET /api/workspace/user-url must derive url/openace_url from Host header.

    Regression: the route previously dropped the host_url argument, so the
    iframe URL fell back to config.url's container IP.
    """
    mock_get_manager.return_value = _stub_manager()
    mock_get_user.return_value = MOCK_USER

    resp = _authed_get(
        workspace_app.test_client(),
        "/api/workspace/user-url",
        host="my-host.example:19888",
    )

    assert resp.status_code == 200
    data = resp.get_json()

    # url must be built from the request host (single-user fixed port 3100),
    # NOT the container IP.
    assert data["url"] == "http://my-host.example:3100", data["url"]
    assert UNREACHABLE_CONTAINER_IP not in data["url"]

    # openace_url must also follow the request host.
    assert data["openace_url"] == "http://my-host.example:19888", data["openace_url"]
    assert UNREACHABLE_CONTAINER_IP not in data["openace_url"]


@patch("app.services.webui_manager.get_webui_manager")
@patch("app.repositories.user_repo.UserRepository.get_user_by_id")
def test_user_url_route_uses_localhost_in_default_case(
    mock_get_user, mock_get_manager, workspace_app
):
    """The common local-dev case: Host=localhost:19888 -> url=localhost:3100."""
    mock_get_manager.return_value = _stub_manager()
    mock_get_user.return_value = MOCK_USER

    resp = _authed_get(
        workspace_app.test_client(),
        "/api/workspace/user-url",
        host="localhost:19888",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["url"] == "http://localhost:3100", data["url"]
    assert data["openace_url"] == "http://localhost:19888", data["openace_url"]


@patch("app.services.webui_manager.get_webui_manager")
@patch("app.repositories.user_repo.UserRepository.get_user_by_id")
def test_user_url_route_multi_user_uses_request_host_and_instance_port(
    mock_get_user, mock_get_manager, workspace_app
):
    """Multi-user mode: url must use the request host + the instance's port.

    Locks down the multi-user route wiring (webui_manager.py:541-549): when an
    instance already exists and host_url is provided, the returned URL is built
    from the browser-visible host and the instance's own port — NOT from the
    container-detected IP stored on the instance.
    """
    from app.services.webui_manager import WebUIManager, WorkspaceConfig

    manager = WebUIManager(
        WorkspaceConfig(
            enabled=True,
            url=UNREACHABLE_CONTAINER_IP,
            multi_user_mode=True,
        )
    )
    manager.stop_cleanup_thread()

    # Inject a live instance with a distinct port and a stale (container-IP) url.
    # is_alive() is stubbed True so the manager reuses it instead of restarting.
    instance = MagicMock()
    instance.is_alive.return_value = True
    instance.port = 3123
    instance.token = "instance-token"
    instance.url = f"{UNREACHABLE_CONTAINER_IP}:3123"  # stale container-IP url
    manager._instances = {MOCK_USER["id"]: instance}

    mock_get_manager.return_value = manager
    mock_get_user.return_value = MOCK_USER

    resp = _authed_get(
        workspace_app.test_client(),
        "/api/workspace/user-url",
        host="my-host.example:19888",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    # host replaced from request, port taken from the instance (3123, not 3100)
    assert data["url"] == "http://my-host.example:3123", data["url"]
    assert UNREACHABLE_CONTAINER_IP not in data["url"]
    assert data["openace_url"] == "http://my-host.example:19888", data["openace_url"]
