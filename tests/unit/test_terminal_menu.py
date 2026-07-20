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


def test_zcode_shown_on_linux_macos(monkeypatch):
    """ZCode should appear in menu on Linux and macOS platforms."""
    terminal_menu = load_terminal_menu()
    monkeypatch.setattr(terminal_menu, "check_installed", lambda _: False)

    # Mock platform.system to return "Linux"
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    items = terminal_menu.get_menu_items()
    zcode_items = [item for item in items if item.get("cli") == "zcode"]
    assert len(zcode_items) == 1, "ZCode should appear in menu on Linux"

    # Mock platform.system to return "Darwin" (macOS)
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    items = terminal_menu.get_menu_items()
    zcode_items = [item for item in items if item.get("cli") == "zcode"]
    assert len(zcode_items) == 1, "ZCode should appear in menu on macOS"


def test_zcode_hidden_on_windows(monkeypatch):
    """ZCode should NOT appear in menu on Windows (it's macOS-only)."""
    terminal_menu = load_terminal_menu()
    monkeypatch.setattr(terminal_menu, "check_installed", lambda _: False)

    # Mock platform.system to return "Windows"
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    items = terminal_menu.get_menu_items()
    zcode_items = [item for item in items if item.get("cli") == "zcode"]
    assert len(zcode_items) == 0, "ZCode should NOT appear in menu on Windows"

    # Verify other tools are still shown
    claude_items = [item for item in items if item.get("cli") == "claude"]
    assert len(claude_items) == 1, "Claude Code should still appear on Windows"


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


def test_handle_select_runs_command_via_subprocess_on_windows(monkeypatch):
    terminal_menu = load_terminal_menu()
    calls = []

    class Result:
        returncode = 0

    def fake_run(command, shell=False, check=False):
        calls.append((command, shell, check))
        return Result()

    monkeypatch.setattr(terminal_menu, "IS_WINDOWS", True)
    monkeypatch.setattr(terminal_menu.subprocess, "run", fake_run)

    terminal_menu.handle_select(
        {
            "name": "Claude Code",
            "installed": True,
            "configured": True,
            "cmd": "claude --bare",
        }
    )

    assert calls == [("claude --bare", True, False)]
