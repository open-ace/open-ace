#!/usr/bin/env python3
"""Open ACE command-line entry point for remote machines.

This script is installed as ``openace`` by the remote agent installer. It lets
users SSH into a registered remote machine, authenticate to Open ACE with a
session token, then launch the same AI tool menu used by the web terminal.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from cli_settings import apply_cli_settings

AGENT_CONFIG_PATH = AGENT_DIR / "config.json"
CLI_CONFIG_DIR = Path.home() / ".open-ace-cli"
CLI_CONFIG_PATH = CLI_CONFIG_DIR / "config.json"
MENU_PATH = AGENT_DIR / "terminal_menu.py"
ACTIVE_TERMINAL_PATH = AGENT_DIR / "active_terminal.json"
logger = logging.getLogger("openace-cli")


class CliError(Exception):
    """Expected user-facing CLI error."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise CliError(f"Invalid JSON in {path}: {exc}") from exc


def _write_cli_config(config: dict[str, Any]) -> None:
    CLI_CONFIG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp_path = CLI_CONFIG_PATH.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.chmod(tmp_path, stat.S_IRUSR | stat.S_IWUSR)
    tmp_path.replace(CLI_CONFIG_PATH)


def _load_agent_config() -> dict[str, Any]:
    config = _read_json(AGENT_CONFIG_PATH)
    if not config:
        raise CliError(
            f"Remote agent config not found at {AGENT_CONFIG_PATH}. "
            "Install the Open ACE remote agent first."
        )
    return config


def _load_cli_config() -> dict[str, Any]:
    return _read_json(CLI_CONFIG_PATH)


def _server_url() -> str:
    agent_config = _load_agent_config()
    server_url = str(agent_config.get("server_url") or "").rstrip("/")
    if not server_url:
        raise CliError(f"server_url is missing from {AGENT_CONFIG_PATH}")
    return server_url


def _machine_id() -> str:
    agent_config = _load_agent_config()
    machine_id = str(agent_config.get("machine_id") or "")
    if not machine_id:
        raise CliError(f"machine_id is missing from {AGENT_CONFIG_PATH}")
    return machine_id


def _session_token() -> str:
    token = str(_load_cli_config().get("session_token") or "")
    if not token:
        raise CliError("Not logged in. Run: openace login")
    return token


def _login_with_password(server_url: str, username: str, password: str) -> str:
    if not server_url.startswith("https://"):
        print("Warning: Sending credentials over insecure connection.", file=sys.stderr)
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{server_url}/api/auth/login",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if not data.get("success"):
                raise CliError(str(data.get("error") or "Login failed"))
            cookies = resp.headers.get_all("Set-Cookie") or []
            for cookie in cookies:
                for part in cookie.split(";"):
                    part = part.strip()
                    if part.startswith("session_token="):
                        return part.split("=", 1)[1]
            raise CliError("Login succeeded but no session token received")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            err_data = json.loads(raw)
            message = err_data.get("error") or err_data.get("message") or raw
        except json.JSONDecodeError:
            message = raw or exc.reason
        raise CliError(f"Login failed ({exc.code}): {message}") from exc
    except urllib.error.URLError as exc:
        raise CliError(f"Cannot reach Open ACE server: {exc.reason}") from exc


def _request_json(method: str, url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw)
            message = data.get("error") or data.get("message") or raw
        except json.JSONDecodeError:
            message = raw or exc.reason
        raise CliError(f"Open ACE request failed ({exc.code}): {message}") from exc
    except urllib.error.URLError as exc:
        raise CliError(f"Cannot reach Open ACE server: {exc.reason}") from exc


def _start_cli_terminal(work_dir: str) -> dict[str, Any]:
    server_url = _server_url()
    payload = {
        "machine_id": _machine_id(),
        "work_dir": work_dir,
        "source": "ssh_cli",
    }
    data = _request_json(
        "POST",
        f"{server_url}/api/remote/terminal/cli/start",
        _session_token(),
        payload,
    )
    if not data.get("success"):
        raise CliError(str(data.get("error") or "Failed to start Open ACE terminal session"))
    terminal = data.get("terminal") or {}
    if not terminal.get("session_id"):
        raise CliError("Open ACE server response did not include a session_id")
    return terminal


def _session_env(terminal: dict[str, Any]) -> dict[str, str]:
    env = dict(os.environ)
    proxy_url = str(terminal.get("proxy_url") or "")
    tokens = terminal.get("tokens") or {}
    if proxy_url:
        env["ANTHROPIC_BASE_URL"] = proxy_url
        env["OPENAI_BASE_URL"] = proxy_url
    if tokens.get("anthropic"):
        env["ANTHROPIC_API_KEY"] = str(tokens["anthropic"])
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    if tokens.get("openai"):
        env["OPENAI_API_KEY"] = str(tokens["openai"])
        try:
            from cli_adapters.base import collect_custom_envkeys

            env.update(
                collect_custom_envkeys(
                    Path.home() / ".qwen" / "settings.json",
                    str(tokens["openai"]),
                )
            )
        except Exception as exc:
            logger.debug("Failed to collect qwen custom env keys: %s", exc)
    env["OPEN_ACE_TERMINAL_ID"] = str(terminal["session_id"])
    env["OPEN_ACE_TERMINAL_SOURCE"] = str(terminal.get("source") or "ssh_cli")
    return env


def _apply_local_cli_settings(terminal: dict[str, Any]) -> None:
    """Apply CLI settings returned by /terminal/cli/start before launching tools."""
    cli_settings = terminal.get("cli_settings") or {}
    proxy_url = str(terminal.get("proxy_url") or "").rstrip("/")
    if not cli_settings or not proxy_url:
        return
    apply_cli_settings(cli_settings, proxy_base_url=f"{proxy_url}/v1")


def _write_active_terminal(terminal: dict[str, Any]) -> None:
    payload = {
        "terminal_id": terminal["session_id"],
        "source": terminal.get("source") or "ssh_cli",
        "updated_at": time.time(),
    }
    ACTIVE_TERMINAL_PATH.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(ACTIVE_TERMINAL_PATH, stat.S_IRUSR | stat.S_IWUSR)


def _clear_active_terminal() -> None:
    try:
        ACTIVE_TERMINAL_PATH.unlink()
    except FileNotFoundError:
        pass


def _windows_shell_args() -> list[str]:
    for candidate in (
        shutil.which("pwsh"),
        shutil.which("powershell"),
        os.environ.get("COMSPEC"),
        "cmd.exe",
    ):
        if not candidate:
            continue
        shell_name = os.path.basename(candidate).lower()
        if shell_name in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
            return [candidate, "-NoLogo"]
        return [candidate]
    return ["cmd.exe"]


def cmd_login(args: argparse.Namespace) -> int:
    server_url = _server_url()
    machine_id = _machine_id()

    if args.token:
        token = args.token
    else:
        username = input("Username: ").strip()
        password = getpass.getpass("Password: ")
        if not username or not password:
            raise CliError("Username and password are required")
        token = _login_with_password(server_url, username, password)

    config = _load_cli_config()
    config["session_token"] = token
    config["server_url"] = server_url
    config["machine_id"] = machine_id
    _write_cli_config(config)
    print(f"Logged in for server {server_url} on machine {machine_id}.")
    return 0


def cmd_logout(_: argparse.Namespace) -> int:
    if CLI_CONFIG_PATH.exists():
        CLI_CONFIG_PATH.unlink()
    print("Logged out.")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    agent_config = _load_agent_config()
    cli_config = _load_cli_config()
    print(f"Server: {agent_config.get('server_url', '')}")
    print(f"Machine: {agent_config.get('machine_id', '')}")
    print(f"Logged in: {'yes' if cli_config.get('session_token') else 'no'}")
    print(f"Menu: {MENU_PATH}")
    return 0


def cmd_menu(args: argparse.Namespace) -> int:
    work_dir = os.path.abspath(args.work_dir or os.getcwd())
    terminal = _start_cli_terminal(work_dir)
    _apply_local_cli_settings(terminal)
    _write_active_terminal(terminal)
    env = _session_env(terminal)
    os.execvpe(sys.executable, [sys.executable, str(MENU_PATH)], env)


def cmd_shell(args: argparse.Namespace) -> int:
    work_dir = os.path.abspath(args.work_dir or os.getcwd())
    terminal = _start_cli_terminal(work_dir)
    _apply_local_cli_settings(terminal)
    _write_active_terminal(terminal)
    env = _session_env(terminal)

    try:
        if os.name == "nt":
            subprocess.run(_windows_shell_args(), env=env, cwd=work_dir, check=False)
        else:
            # Unix/Linux: login shell
            shell = os.environ.get("SHELL") or "/bin/sh"
            subprocess.run([shell, "-l"], env=env, cwd=work_dir, check=False)
    finally:
        _clear_active_terminal()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openace", description="Open ACE remote CLI")
    sub = parser.add_subparsers(dest="command")

    login = sub.add_parser("login", help="Log in with username/password or a session token")
    login.add_argument("--token", help="Open ACE session token (skip username/password prompt)")
    login.set_defaults(func=cmd_login)

    logout = sub.add_parser("logout", help="Remove stored Open ACE credentials")
    logout.set_defaults(func=cmd_logout)

    status = sub.add_parser("status", help="Show local Open ACE CLI status")
    status.set_defaults(func=cmd_status)

    menu = sub.add_parser("menu", help="Start the Open ACE AI tool menu")
    menu.add_argument("--work-dir", default="", help="Working directory for the session")
    menu.set_defaults(func=cmd_menu)

    shell = sub.add_parser("shell", help="Start a shell with Open ACE proxy credentials")
    shell.add_argument("--work-dir", default="", help="Working directory for the session")
    shell.set_defaults(func=cmd_shell)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        args = parser.parse_args(["menu"])
    try:
        return int(args.func(args))
    except CliError as exc:
        print(f"openace: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
