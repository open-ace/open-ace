import uuid

from app.modules.workspace.remote_agent_manager import RemoteAgentManager
from app.modules.workspace.terminal_store import terminal_info_store


def test_terminal_info_uses_main_backend_ws_route():
    manager = RemoteAgentManager.__new__(RemoteAgentManager)
    terminal_id = str(uuid.uuid4())
    machine_id = "machine-main-ws"

    try:
        info = {
            "status": "running",
            "ws_url": "ws://192.168.1.56:37055",
            "token": "remote-token-1",
        }
        manager.store_terminal_info(machine_id, terminal_id, info)

        stored = terminal_info_store.get(machine_id, terminal_id)
        assert stored is not None
        assert stored["ws_url"] == f"/api/remote/terminal/{terminal_id}/ws"
        assert stored["original_ws_url"] == "ws://192.168.1.56:37055"
        assert stored["original_token"] == "remote-token-1"
        assert stored["token"] != "remote-token-1"

        browser_token = stored["token"]
        manager.store_terminal_info(
            machine_id,
            terminal_id,
            {
                "status": "running",
                "ws_url": "ws://192.168.1.56:37056",
                "token": "remote-token-2",
            },
        )

        updated = terminal_info_store.get(machine_id, terminal_id)
        assert updated is not None
        assert updated["token"] == browser_token
        assert updated["original_ws_url"] == "ws://192.168.1.56:37056"
        assert updated["original_token"] == "remote-token-2"
        indexed = terminal_info_store.find_by_terminal_id(terminal_id)
        assert indexed is not None
        assert indexed[0] == machine_id
    finally:
        terminal_info_store.pop(machine_id, terminal_id)
        assert terminal_info_store.find_by_terminal_id(terminal_id) is None


def test_backend_url_prefers_external_url_then_request_host(tmp_path, monkeypatch):
    manager = RemoteAgentManager.__new__(RemoteAgentManager)

    from app.repositories import database

    monkeypatch.setattr(database, "CONFIG_DIR", tmp_path)
    assert manager.get_backend_url("http://192.168.1.21:19888/") == "http://192.168.1.21:19888"

    (tmp_path / "config.json").write_text(
        '{"external_url": "http://public.example:19888", '
        '"server": {"server_url": "http://localhost:19888"}}'
    )
    assert manager.get_backend_url("http://192.168.1.21:19888/") == "http://public.example:19888"


def test_backend_url_preserves_legacy_fallback_when_no_context(tmp_path, monkeypatch):
    manager = RemoteAgentManager.__new__(RemoteAgentManager)

    from app.repositories import database

    monkeypatch.setattr(database, "CONFIG_DIR", tmp_path)
    assert manager.get_backend_url() == "http://localhost:5001"


def test_close_terminal_bridges_closes_active_connections():
    from app.modules.workspace.terminal_ws_bridge import (
        TerminalBridgeConnection,
        _register_bridge,
        close_terminal_bridges,
        get_active_bridge_count,
    )

    class FakeSocket:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    browser_ws = FakeSocket()
    remote_ws = FakeSocket()
    state = TerminalBridgeConnection(
        terminal_id="terminal-close-test",
        browser_ws=browser_ws,
        remote_ws=remote_ws,
    )

    before_count = get_active_bridge_count()
    _register_bridge(state)
    assert get_active_bridge_count() == before_count + 1
    close_terminal_bridges("terminal-close-test")

    assert browser_ws.closed
    assert remote_ws.closed
    assert get_active_bridge_count() == before_count


def test_terminal_store_cleanup_stale_closes_active_bridges():
    from app.modules.workspace.terminal_store import TerminalInfoStore
    from app.modules.workspace.terminal_ws_bridge import TerminalBridgeConnection, _register_bridge

    class FakeSocket:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    store = TerminalInfoStore(ttl=0)
    terminal_id = "terminal-stale-test"
    machine_id = "machine-stale-test"
    browser_ws = FakeSocket()
    remote_ws = FakeSocket()

    store.put(machine_id, terminal_id, {"status": "running"})
    _register_bridge(
        TerminalBridgeConnection(
            terminal_id=terminal_id,
            browser_ws=browser_ws,
            remote_ws=remote_ws,
        )
    )

    assert store.cleanup_stale() == 1
    assert browser_ws.closed
    assert remote_ws.closed
    assert store.find_by_terminal_id(terminal_id) is None
