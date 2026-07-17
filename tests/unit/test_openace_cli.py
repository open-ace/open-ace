#!/usr/bin/env python3
"""Unit tests for the SSH/local Open ACE CLI entry point."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def load_openace_cli():
    module_path = Path(__file__).resolve().parents[2] / "remote-agent" / "openace_cli.py"
    spec = importlib.util.spec_from_file_location("openace_cli", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_session_env_sets_proxy_tokens(monkeypatch):
    openace_cli = load_openace_cli()
    monkeypatch.setattr(openace_cli.os, "environ", {})

    env = openace_cli._session_env(
        {
            "session_id": "term-123",
            "proxy_url": "https://openace.example/api/remote/llm-proxy",
            "source": "ssh_cli",
            "tokens": {
                "anthropic": "anthropic-proxy-token",
                "openai": "openai-proxy-token",
            },
        }
    )

    assert env["ANTHROPIC_API_KEY"] == "anthropic-proxy-token"
    assert env["ANTHROPIC_BASE_URL"] == "https://openace.example/api/remote/llm-proxy"
    assert env["OPENAI_API_KEY"] == "openai-proxy-token"
    assert env["OPENAI_BASE_URL"] == "https://openace.example/api/remote/llm-proxy"
    assert env["OPEN_ACE_TERMINAL_ID"] == "term-123"
    assert env["OPEN_ACE_TERMINAL_SOURCE"] == "ssh_cli"


def test_start_cli_terminal_posts_machine_and_work_dir(monkeypatch):
    openace_cli = load_openace_cli()
    calls = []

    monkeypatch.setattr(openace_cli, "_server_url", lambda: "https://openace.example")
    monkeypatch.setattr(openace_cli, "_machine_id", lambda: "machine-123")
    monkeypatch.setattr(openace_cli, "_session_token", lambda: "session-token")

    def fake_request(method, url, token, payload):
        calls.append((method, url, token, payload))
        return {"success": True, "terminal": {"session_id": "term-123"}}

    monkeypatch.setattr(openace_cli, "_request_json", fake_request)

    terminal = openace_cli._start_cli_terminal("/repo")

    assert terminal["session_id"] == "term-123"
    assert calls == [
        (
            "POST",
            "https://openace.example/api/remote/terminal/cli/start",
            "session-token",
            {"machine_id": "machine-123", "work_dir": "/repo", "source": "ssh_cli"},
        )
    ]


def test_apply_local_cli_settings_uses_proxy_v1(monkeypatch):
    openace_cli = load_openace_cli()
    applied = []

    def fake_apply(cli_settings, proxy_base_url):
        applied.append((cli_settings, proxy_base_url))

    monkeypatch.setattr(openace_cli, "apply_cli_settings", fake_apply)

    openace_cli._apply_local_cli_settings(
        {
            "proxy_url": "https://openace.example/api/remote/llm-proxy",
            "cli_settings": {"codex-cli": {"model_provider": "openace"}},
        }
    )

    assert applied == [
        (
            {"codex-cli": {"model_provider": "openace"}},
            "https://openace.example/api/remote/llm-proxy/v1",
        )
    ]


def test_login_writes_session_token(monkeypatch, tmp_path):
    openace_cli = load_openace_cli()
    config_path = tmp_path / "config.json"

    monkeypatch.setattr(openace_cli, "CLI_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(openace_cli, "CLI_CONFIG_PATH", config_path)
    monkeypatch.setattr(openace_cli, "_server_url", lambda: "https://openace.example")
    monkeypatch.setattr(openace_cli, "_machine_id", lambda: "machine-123")

    args = type("Args", (), {"token": "session-token"})()

    assert openace_cli.cmd_login(args) == 0
    assert '"session_token": "session-token"' in config_path.read_text(encoding="utf-8")


def test_login_with_password_extracts_session_token(monkeypatch, tmp_path):
    import builtins

    openace_cli = load_openace_cli()
    config_path = tmp_path / "config.json"

    monkeypatch.setattr(openace_cli, "CLI_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(openace_cli, "CLI_CONFIG_PATH", config_path)
    monkeypatch.setattr(openace_cli, "_server_url", lambda: "https://openace.example")
    monkeypatch.setattr(openace_cli, "_machine_id", lambda: "machine-123")

    monkeypatch.setattr(builtins, "input", lambda _: "testuser")
    monkeypatch.setattr(openace_cli.getpass, "getpass", lambda _: "testpass")

    class FakeResp:
        headers = type("H", (), {"get_all": lambda s, k=None: ["session_token=abc123; Path=/"]})()

        def read(self):
            return b'{"success": true, "user": {"username": "testuser"}}'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=30):
        assert "/api/auth/login" in req.full_url, f"Unexpected URL: {req.full_url}"
        body = json.loads(req.data)
        assert body["username"] == "testuser"
        assert body["password"] == "testpass"
        return FakeResp()

    monkeypatch.setattr(openace_cli.urllib.request, "urlopen", fake_urlopen)

    args = type("Args", (), {"token": None})()
    assert openace_cli.cmd_login(args) == 0
    assert '"session_token": "abc123"' in config_path.read_text(encoding="utf-8")


def test_write_active_terminal_metadata(monkeypatch, tmp_path):
    openace_cli = load_openace_cli()
    active_path = tmp_path / "active_terminal.json"
    monkeypatch.setattr(openace_cli, "ACTIVE_TERMINAL_PATH", active_path)

    openace_cli._write_active_terminal({"session_id": "term-123", "source": "ssh_cli"})

    raw = active_path.read_text(encoding="utf-8")
    assert '"terminal_id": "term-123"' in raw
    assert '"source": "ssh_cli"' in raw


def test_clear_active_terminal_metadata(monkeypatch, tmp_path):
    openace_cli = load_openace_cli()
    active_path = tmp_path / "active_terminal.json"
    active_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(openace_cli, "ACTIVE_TERMINAL_PATH", active_path)

    openace_cli._clear_active_terminal()

    assert not active_path.exists()


def test_cmd_menu_execs_terminal_menu_on_windows(monkeypatch):
    openace_cli = load_openace_cli()
    calls = {}

    monkeypatch.setattr(openace_cli.os, "name", "nt", raising=False)
    monkeypatch.setattr(
        openace_cli,
        "_start_cli_terminal",
        lambda work_dir: {"session_id": "term-123", "proxy_url": "https://openace.example"},
    )
    monkeypatch.setattr(openace_cli, "_apply_local_cli_settings", lambda terminal: None)
    monkeypatch.setattr(openace_cli, "_write_active_terminal", lambda terminal: None)
    monkeypatch.setattr(openace_cli, "_session_env", lambda terminal: {"ENV": "1"})

    def fake_execvpe(file, argv, env):
        calls["file"] = file
        calls["argv"] = argv
        calls["env"] = env
        raise RuntimeError("stop")

    monkeypatch.setattr(openace_cli.os, "execvpe", fake_execvpe)

    args = type("Args", (), {"work_dir": "/repo"})()
    try:
        openace_cli.cmd_menu(args)
    except RuntimeError as exc:
        assert str(exc) == "stop"
    else:  # pragma: no cover - defensive
        raise AssertionError("cmd_menu did not attempt to exec the terminal menu")

    assert calls["file"] == openace_cli.sys.executable
    assert calls["argv"] == [openace_cli.sys.executable, str(openace_cli.MENU_PATH)]
    assert calls["env"] == {"ENV": "1"}
