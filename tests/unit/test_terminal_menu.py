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


def test_zcode_shown_on_all_platforms(monkeypatch):
    """ZCode should appear in menu on all platforms (Windows, Linux, macOS)."""
    terminal_menu = load_terminal_menu()
    monkeypatch.setattr(terminal_menu, "check_installed", lambda _: False)

    items = terminal_menu.get_menu_items()
    zcode_items = [item for item in items if item.get("cli") == "zcode"]
    assert len(zcode_items) == 1, "ZCode should appear in menu on all platforms"


def test_zcode_shows_manual_instructions_on_windows(monkeypatch):
    """ZCode should show manual installation instructions on Windows."""
    terminal_menu = load_terminal_menu()
    shown_messages = []

    def fake_show_message(msg):
        shown_messages.append(msg)

    monkeypatch.setattr(terminal_menu, "IS_WINDOWS", True)
    monkeypatch.setattr(terminal_menu, "show_message", fake_show_message)
    monkeypatch.setattr(terminal_menu, "wait_for_continue", lambda: None)

    # Find ZCode tool definition
    items = terminal_menu.get_menu_items()
    zcode_item = next(item for item in items if item.get("cli") == "zcode")
    zcode_item["installed"] = False
    zcode_item["configured"] = True

    terminal_menu.handle_select(zcode_item)

    # Should show manual instructions, not execute install command
    assert len(shown_messages) == 1
    assert "manual setup" in shown_messages[0].lower() or "requires manual" in shown_messages[0].lower()


def test_npm_tools_show_error_when_nodejs_missing_on_windows(monkeypatch):
    """npm-based tools should show error when Node.js is not installed."""
    terminal_menu = load_terminal_menu()
    shown_messages = []

    def fake_show_message(msg):
        shown_messages.append(msg)

    monkeypatch.setattr(terminal_menu, "IS_WINDOWS", True)
    monkeypatch.setattr(terminal_menu, "show_message", fake_show_message)
    monkeypatch.setattr(terminal_menu, "wait_for_continue", lambda: None)
    monkeypatch.setattr(terminal_menu.shutil, "which", lambda _: None)  # npm not found

    # Claude Code uses npm install
    claude_item = {
        "name": "Claude Code",
        "cli": "claude",
        "cmd": "claude --bare",
        "install_cmd": "npm install -g @anthropic-ai/claude-code@latest",
        "installed": False,
        "configured": True,
    }

    terminal_menu.handle_select(claude_item)

    # Should show Node.js missing error
    assert len(shown_messages) == 1
    assert "Node.js" in shown_messages[0] or "npm" in shown_messages[0]


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
