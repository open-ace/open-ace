#!/usr/bin/env python3
"""Unit tests for the SSH/local Open ACE CLI entry point."""

from __future__ import annotations

import importlib.util
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


def test_write_active_terminal_metadata(monkeypatch, tmp_path):
    openace_cli = load_openace_cli()
    active_path = tmp_path / "active_terminal.json"
    monkeypatch.setattr(openace_cli, "ACTIVE_TERMINAL_PATH", active_path)

    openace_cli._write_active_terminal({"session_id": "term-123", "source": "ssh_cli"})

    raw = active_path.read_text(encoding="utf-8")
    assert '"terminal_id": "term-123"' in raw
    assert '"source": "ssh_cli"' in raw
