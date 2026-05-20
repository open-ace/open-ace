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


def test_render_launch_progress_for_installed_tool(capsys):
    terminal_menu = load_terminal_menu()

    terminal_menu.render_launch_progress({"name": "Claude Code", "installed": True})

    output = capsys.readouterr().out
    assert "Starting" in output
    assert "Claude Code" in output
    assert "[===========         ]" in output
    assert "55%" in output
    assert "Waiting for the CLI interface" in output
    assert "Ctrl+C" in output


def test_render_launch_progress_for_installing_tool(capsys):
    terminal_menu = load_terminal_menu()

    terminal_menu.render_launch_progress({"name": "Qwen Code", "installed": False})

    output = capsys.readouterr().out
    assert "Installing" in output
    assert "Qwen Code" in output
    assert "[=======             ]" in output
    assert "35%" in output
    assert "Installer output will appear below" in output


def test_handle_select_renders_progress_before_exec(monkeypatch, capsys):
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

    output = capsys.readouterr().out
    assert "Starting" in output
    assert "Claude Code" in output
    assert executed_commands == [
        f"claude --bare; exec {terminal_menu.sys.executable} {terminal_menu.MENU_PATH}"
    ]
