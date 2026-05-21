from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXPECTED_CONSTRAINT = "websockets>=13.0,<17.0"


def test_remote_agent_websockets_constraint_is_bounded():
    requirements = (PROJECT_ROOT / "remote-agent" / "requirements.txt").read_text()

    assert EXPECTED_CONSTRAINT in requirements.splitlines()
    assert "websockets>=12.0" not in requirements


def test_remote_agent_install_hints_match_constraint():
    for script_name in ("terminal_server.py", "websocket_proxy.py"):
        script = (PROJECT_ROOT / "remote-agent" / script_name).read_text()

        assert f"pip install '{EXPECTED_CONSTRAINT}'" in script
