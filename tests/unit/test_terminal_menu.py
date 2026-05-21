#!/usr/bin/env python3
"""Unit tests for the remote terminal menu."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def load_terminal_menu():
    module_path = Path(__file__).resolve().parents[2] / "remote-agent" / "terminal_menu.py"
    spec = importlib.util.spec_from_file_location("terminal_menu", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_handle_select_execs_command(monkeypatch):
    terminal_menu = load_terminal_menu()
    executed_commands = []

    def fake_exec_command(command):
        executed_commands.append(command)

    monkeypatch.setattr(terminal_menu, "exec_command", fake_exec_command)

    terminal_menu.handle_select(
        {
            "name": "Claude Code",
            "installed": True,
            "configured": True,
            "cmd": "claude --bare",
        }
    )

    assert executed_commands == [
        f"claude --bare; exec {terminal_menu.sys.executable} {terminal_menu.MENU_PATH}"
    ]


def test_handle_shell_return_executes_shell_then_menu(monkeypatch):
    terminal_menu = load_terminal_menu()
    executed_commands = []

    def fake_exec_command(command):
        executed_commands.append(command)

    monkeypatch.setattr(terminal_menu, "exec_command", fake_exec_command)
    monkeypatch.setenv("SHELL", "/bin/zsh")

    terminal_menu.handle_select({"name": "Shell", "is_shell_return": True})

    assert len(executed_commands) == 1
    assert "/bin/zsh -l" in executed_commands[0]
    assert f"exec {terminal_menu.sys.executable} {terminal_menu.MENU_PATH}" in executed_commands[0]


def test_menu_includes_shell_return_and_permanent_exit(monkeypatch):
    terminal_menu = load_terminal_menu()
    monkeypatch.setattr(terminal_menu, "check_installed", lambda _: False)

    items = terminal_menu.get_menu_items()

    assert any(item.get("is_shell_return") for item in items)
    assert any(item.get("is_shell_exit") for item in items)
    assert any(item["name"] == "Claude Code" and not item["installed"] for item in items)
