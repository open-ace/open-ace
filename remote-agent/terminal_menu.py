#!/usr/bin/env python3
"""Open ACE Terminal - Interactive Tool Selector

Displays a menu of available AI tools (claude, qwen, etc.) with
arrow-key navigation. Selecting an installed tool launches it;
selecting an uninstalled tool installs then launches it.
After a tool exits, the menu reappears.

Runs as the initial PTY process via terminal_server.py.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

TOOLS = [
    {
        "name": "Claude Code",
        "cli": "claude",
        "cmd": "claude --bare",
        "install_cmd": "npm install -g @anthropic-ai/claude-code@latest",
        "env_key": "ANTHROPIC_API_KEY",
    },
    {
        "name": "Qwen Code",
        "cli": "qwen",
        "cmd": "qwen --auth-type openai",
        "install_cmd": "npm install -g @qwen-code/qwen-code@latest",
        "env_key": "OPENAI_API_KEY",
    },
    {
        "name": "Codex",
        "cli": "codex",
        "cmd": "codex",
        "install_cmd": "npm install -g @openai/codex@latest",
        "env_key": "OPENAI_API_KEY",
    },
    {
        "name": "ZCode",
        "cli": "zcode",
        "cmd": "zcode",
        # ZCode ships inside the macOS desktop app; symlink the bundled engine.
        # On Linux, install/symlink the engine per ZCode's docs instead.
        "install_cmd": "sudo ln -s /Applications/ZCode.app/Contents/Resources/glm/zcode.cjs /usr/local/bin/zcode",
        "env_key": "ANTHROPIC_API_KEY",
    },
]

MENU_PATH = os.path.abspath(__file__)
ACTIVE_TERMINAL_PATH = Path.home() / ".open-ace-agent" / "active_terminal.json"
IS_WINDOWS = os.name == "nt"

# ANSI codes
CLEAR = "\x1b[2J\x1b[H"
BOLD_CYAN = "\x1b[1;36m"
BOLD_YELLOW = "\x1b[1;33m"
BOLD_RED = "\x1b[1;31m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"
REVERSE = "\x1b[7m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"


def check_installed(cli_name: str) -> bool:
    return shutil.which(cli_name) is not None


def get_menu_items() -> list[dict]:
    items = []
    for tool in TOOLS:
        installed = check_installed(tool["cli"])
        configured = bool(os.environ.get(tool["env_key"]))
        items.append({**tool, "installed": installed, "configured": configured})
    items.append({"name": "Shell (return to menu on exit)", "is_shell_return": True})
    items.append({"name": "Exit to shell", "is_shell_exit": True})
    return items


def get_label(item: dict) -> str:
    if item.get("is_shell_return") or item.get("is_shell_exit"):
        return item["name"]
    if item["installed"]:
        if item["configured"]:
            return item["name"]
        return f"{item['name']}  {YELLOW}⚠ Not configured{RESET}"
    return f"Install {item['name']}"


def render_menu(items: list[dict], selected: int) -> None:
    lines = [
        "",
        f"  {BOLD_CYAN}========================================{RESET}",
        f"  {BOLD_CYAN}  Open ACE Remote Terminal{RESET}",
        f"  {BOLD_CYAN}========================================{RESET}",
        "",
        "  Select a tool:",
        "",
    ]
    for i, item in enumerate(items):
        label = get_label(item)
        if i == selected:
            lines.append(f"  {REVERSE} > {label} {RESET}")
        else:
            lines.append(f"    {label}")
    lines.append("")
    if IS_WINDOWS:
        lines.append(f"  {DIM}Type a number, then press Enter{RESET}")
    else:
        lines.append(f"  {DIM}↑↓ Navigate   Enter Select{RESET}")
    lines.append("")

    output = CLEAR + "\r\n".join(lines)
    sys.stdout.write(output)
    sys.stdout.flush()


def show_message(message: str) -> None:
    sys.stdout.write(f"\r\n  {message}\r\n\r\n  {DIM}Press any key to continue...{RESET}")
    sys.stdout.flush()


def wait_for_continue() -> None:
    if IS_WINDOWS:
        input("\n  Press Enter to continue...")
        return
    os.read(sys.stdin.fileno(), 1)


def read_key(fd: int) -> str:
    key = os.read(fd, 1).decode("utf-8", errors="replace")
    if key == "\x1b":
        b2 = os.read(fd, 1).decode("utf-8", errors="replace")
        if b2 == "[":
            b3 = os.read(fd, 1).decode("utf-8", errors="replace")
            if b3 == "A":
                return "up"
            if b3 == "B":
                return "down"
        return "escape"
    if key in ("\r", "\n"):
        return "enter"
    return "other"


def get_shell_path() -> str:
    if IS_WINDOWS:
        for candidate in (
            shutil.which("pwsh"),
            shutil.which("powershell"),
            os.environ.get("COMSPEC"),
            "cmd.exe",
        ):
            if candidate:
                return candidate
    return os.environ.get("SHELL") or "/bin/sh"


def get_login_shell_args() -> list[str]:
    shell = get_shell_path()
    if IS_WINDOWS:
        shell_name = os.path.basename(shell).lower()
        if shell_name in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
            return [shell, "-NoLogo"]
        return [shell]
    return [shell, "-l"]


def get_login_shell_command() -> str:
    return " ".join(shlex.quote(part) for part in get_login_shell_args())


def clear_active_terminal() -> None:
    if os.environ.get("OPEN_ACE_TERMINAL_SOURCE") != "ssh_cli":
        return
    try:
        ACTIVE_TERMINAL_PATH.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def exec_command(command: str) -> None:
    os.execvp("/bin/sh", ["/bin/sh", "-c", command])


def handle_select(item: dict) -> None:
    if item.get("is_shell_return"):
        if IS_WINDOWS:
            subprocess.run(get_login_shell_args(), check=False)
            return
        cmd = (
            'echo "Type \\"exit\\" to return to the Open ACE menu. '
            'Type \\"openace menu\\" to restart it anytime."; '
            f"{get_login_shell_command()}; exec {sys.executable} {MENU_PATH}"
        )
        exec_command(cmd)
        return

    if item.get("is_shell_exit"):
        sys.stdout.write("\r\n  Type 'openace menu' to return to the Open ACE menu.\r\n\r\n")
        sys.stdout.flush()
        clear_active_terminal()
        os.execvp(get_login_shell_args()[0], get_login_shell_args())
        return

    if item["installed"] and not item["configured"]:
        show_message(
            f"{BOLD_YELLOW}⚠ {item['name']} is installed but API is not configured.\r\n"
            f"  Please contact your admin to configure the {item['env_key']} token.{RESET}"
        )
        wait_for_continue()
        return

    if IS_WINDOWS:
        if not item["installed"]:
            install_result = subprocess.run(item["install_cmd"], shell=True, check=False)
            if install_result.returncode != 0:
                show_message(f"{BOLD_RED}Failed to install {item['name']}.{RESET}")
                wait_for_continue()
                return
        subprocess.run(item["cmd"], shell=True, check=False)
        return

    if not item["installed"]:
        cmd = f'{item["install_cmd"]} && {item["cmd"]}; exec {sys.executable} {MENU_PATH}'
    else:
        cmd = f'{item["cmd"]}; exec {sys.executable} {MENU_PATH}'
    exec_command(cmd)


def run_windows_menu() -> None:
    while True:
        items = get_menu_items()
        lines = [
            "",
            f"  {BOLD_CYAN}========================================{RESET}",
            f"  {BOLD_CYAN}  Open ACE Remote Terminal{RESET}",
            f"  {BOLD_CYAN}========================================{RESET}",
            "",
            "  Select a tool:",
            "",
        ]
        for index, item in enumerate(items, start=1):
            lines.append(f"    {index}. {get_label(item)}")
        lines.append("")
        lines.append(f"  {DIM}Type a number and press Enter{RESET}")
        lines.append("")

        sys.stdout.write(CLEAR + "\r\n".join(lines))
        sys.stdout.flush()

        choice = input("  Select a tool: ").strip()
        if not choice:
            continue

        try:
            selected = int(choice) - 1
        except ValueError:
            show_message(f"{BOLD_RED}Invalid selection.{RESET}")
            wait_for_continue()
            continue

        if selected < 0 or selected >= len(items):
            show_message(f"{BOLD_RED}Selection out of range.{RESET}")
            wait_for_continue()
            continue

        handle_select(items[selected])


def main() -> None:
    if IS_WINDOWS:
        run_windows_menu()
        return

    items = get_menu_items()

    import termios
    import tty

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        tty.setraw(fd)
        selected = 0
        render_menu(items, selected)

        while True:
            key = read_key(fd)
            if key == "up":
                selected = (selected - 1) % len(items)
                render_menu(items, selected)
            elif key == "down":
                selected = (selected + 1) % len(items)
                render_menu(items, selected)
            elif key == "enter":
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                handle_select(items[selected])
                # If handle_select returns (not configured case), restore raw mode
                tty.setraw(fd)
                render_menu(items, selected)
    except Exception:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        os.execvp("/bin/bash", ["/bin/bash", "-l"])


if __name__ == "__main__":
    main()
