from __future__ import annotations

import threading
import time

import pytest

from app.modules.workspace import remote_agent_manager as ram_mod
from app.modules.workspace.remote_agent_manager import RemoteAgentManager
from app.repositories.database import Database
from app.repositories.schema_init import load_schema_from_file


@pytest.fixture
def runtime_db(tmp_path, monkeypatch):
    """RemoteAgentManager instances sharing one SQLite runtime database."""
    monkeypatch.setattr(ram_mod, "is_postgresql", lambda: False)
    monkeypatch.setattr(RemoteAgentManager, "_start_heartbeat_monitor", lambda self: None)
    db_path = tmp_path / "remote_runtime.db"
    load_schema_from_file(db_url=f"sqlite:///{db_path}", dialect="sqlite")
    return db_path, Database(db_url=f"sqlite:///{db_path}")


def test_http_polling_commands_survive_web_process_restart(runtime_db):
    db_path, db = runtime_db
    first_pod = RemoteAgentManager(db_path=str(db_path))
    second_pod = RemoteAgentManager(db_path=str(db_path))

    assert first_pod.send_command(
        "machine-1",
        {"type": "command", "command": "send_message", "session_id": "session-1"},
    )

    pending = second_pod.get_pending_commands("machine-1")
    assert [cmd["command"] for cmd in pending] == ["send_message"]
    assert second_pod.get_pending_commands("machine-1") == []
    row = db.fetch_one("SELECT status FROM remote_runtime_commands")
    assert row["status"] == "delivered"


def test_session_output_replays_from_persistent_buffer_after_restart(runtime_db):
    db_path, _ = runtime_db
    first_pod = RemoteAgentManager(db_path=str(db_path))
    second_pod = RemoteAgentManager(db_path=str(db_path))

    first_pod.buffer_output("session-1", {"stream": "stdout", "data": "one"})
    first_pod.buffer_output("session-1", {"stream": "stderr", "data": "two"})

    assert [entry["data"] for entry in second_pod.get_buffered_output("session-1")] == [
        "one",
        "two",
    ]
    assert [
        entry["data"] for entry in second_pod.get_buffered_output("session-1", after_index=1)
    ] == ["two"]


def test_command_response_can_arrive_on_another_web_process(runtime_db):
    db_path, _ = runtime_db
    request_pod = RemoteAgentManager(db_path=str(db_path))
    response_pod = RemoteAgentManager(db_path=str(db_path))
    result_holder: dict[str, dict | None] = {}

    def wait_for_response() -> None:
        result_holder["result"] = request_pod.send_command_with_response(
            "machine-1",
            "get_session_info",
            "session-1",
            timeout=2.0,
        )

    waiter = threading.Thread(target=wait_for_response)
    waiter.start()

    deadline = time.time() + 1.0
    request_id = None
    db = Database(db_url=f"sqlite:///{db_path}")
    while time.time() < deadline:
        row = db.fetch_one("SELECT command_id FROM remote_runtime_commands")
        if row:
            request_id = row["command_id"]
            break
        time.sleep(0.05)

    assert request_id
    response_pod.handle_command_response(
        {"request_id": request_id, "result": {"ok": True, "cwd": "/workspace"}}
    )
    waiter.join(timeout=3.0)

    assert result_holder["result"] == {"ok": True, "cwd": "/workspace"}
