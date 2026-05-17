"""
Open ACE Remote Agent - WebSocket Terminal Server

Standalone asyncio WebSocket server that provides web-based terminal access.
Each WebSocket connection spawns a PTY process with pre-configured environment
for Claude Code (ANTHROPIC_API_KEY/BASE_URL pointing to Open-ACE proxy).

Started as a subprocess by the remote agent when a terminal session is requested.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import logging
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import urllib.parse

try:
    import websockets
    from websockets.server import serve
except ImportError:
    print("Error: 'websockets' package is required.", file=sys.stderr)
    print("Install with: pip install websockets>=12.0", file=sys.stderr)
    sys.exit(1)

logger = logging.getLogger("openace-terminal-server")

# Globals set from CLI args
AUTH_TOKEN = ""
PROXY_URL = ""
ANTHROPIC_TOKEN = ""
OPENAI_TOKEN = ""
WORK_DIR = ""
SHELL_CMD = ""


class TerminalSession:
    """Manages a PTY process bridged to a single WebSocket connection."""

    def __init__(self, websocket, master_fd: int, pid: int):
        self.websocket = websocket
        self.master_fd = master_fd
        self.pid = pid
        self._running = True

    async def relay_output(self) -> None:
        """Read PTY output and send to WebSocket."""
        while self._running:
            try:
                ready, _, _ = select.select([self.master_fd], [], [], 0.05)
                if ready:
                    data = os.read(self.master_fd, 65536)
                    if data:
                        await self.websocket.send(data)
                    else:
                        self._running = False
                        break
                await asyncio.sleep(0.01)
            except OSError:
                self._running = False
                break

    async def relay_input(self) -> None:
        """Read WebSocket messages and write to PTY."""
        while self._running:
            try:
                message = await self.websocket.recv()
                if isinstance(message, str):
                    # JSON control message (resize, etc.)
                    try:
                        import json

                        ctrl = json.loads(message)
                        if ctrl.get("type") == "resize":
                            cols = ctrl.get("cols", 80)
                            rows = ctrl.get("rows", 24)
                            self.resize(cols, rows)
                        continue
                    except (json.JSONDecodeError, ValueError):
                        # Not JSON, treat as raw text input
                        message = message.encode("utf-8")
                if isinstance(message, bytes):
                    os.write(self.master_fd, message)
            except websockets.exceptions.ConnectionClosed:
                self._running = False
                break
            except Exception:
                self._running = False
                break

    def resize(self, cols: int, rows: int) -> None:
        """Resize the PTY terminal."""
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
        except Exception as e:
            logger.debug("Resize failed: %s", e)

    def kill(self) -> None:
        """Terminate the PTY process."""
        self._running = False
        try:
            os.kill(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _spawn_pty(shell_cmd: list[str], env: dict[str, str], work_dir: str) -> tuple[int, int]:
    """Fork a PTY process and return (master_fd, child_pid)."""
    pid, master_fd = pty.fork()
    if pid == 0:
        # Child process
        try:
            if work_dir:
                os.chdir(work_dir)
        except OSError:
            os.chdir(os.path.expanduser("~"))
        try:
            os.execvpe(shell_cmd[0], shell_cmd, env)
        except FileNotFoundError:
            print(f"Shell not found: {shell_cmd[0]}", file=sys.stderr)
            os._exit(1)
    return master_fd, pid


def _build_env() -> dict[str, str]:
    """Build environment variables for the terminal process."""
    env = dict(os.environ)
    if PROXY_URL:
        # Anthropic/Claude Code configuration
        if ANTHROPIC_TOKEN:
            env["ANTHROPIC_API_KEY"] = ANTHROPIC_TOKEN
            env["ANTHROPIC_BASE_URL"] = PROXY_URL
            env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        # OpenAI/Qwen configuration
        if OPENAI_TOKEN:
            env["OPENAI_API_KEY"] = OPENAI_TOKEN
            env["OPENAI_BASE_URL"] = PROXY_URL
    env["TERM"] = "xterm-256color"
    return env


def _check_cli_installed(cli_name: str) -> bool:
    """Check if a CLI tool is installed."""
    try:
        result = subprocess.run(
            ["which", cli_name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _write_banner(master_fd: int) -> None:
    """Write a welcome banner to the terminal."""
    # Check which CLI tools are installed
    claude_installed = _check_cli_installed("claude")
    qwen_installed = _check_cli_installed("qwen")

    # Use simpler formatting without ANSI codes to avoid encoding issues
    banner_lines = [
        "",
        "========================================",
        "  Open ACE Remote Terminal",
        "========================================",
    ]
    if PROXY_URL:
        banner_lines.append("")
        if ANTHROPIC_TOKEN or OPENAI_TOKEN:
            banner_lines.append("  AI assistants:")
            if ANTHROPIC_TOKEN:
                status = "[installed]" if claude_installed else "[NOT installed]"
                banner_lines.append(f"    claude  {status}")
            if OPENAI_TOKEN:
                status = "[installed]" if qwen_installed else "[NOT installed]"
                banner_lines.append(f"    qwen    {status}")
            banner_lines.append("")

            # Show run commands or install hints
            installed_clis = []
            missing_clis = []
            if ANTHROPIC_TOKEN:
                if claude_installed:
                    installed_clis.append("claude")
                else:
                    missing_clis.append(
                        ("claude", "curl -fsSL https://claude.ai/install.sh | bash")
                    )
            if OPENAI_TOKEN:
                if qwen_installed:
                    installed_clis.append("qwen")
                else:
                    missing_clis.append(("qwen", "npm install -g @qwen-code/qwen-code@latest"))

            if installed_clis:
                banner_lines.append(f"  Run: {', '.join(installed_clis)}")
            if missing_clis:
                banner_lines.append("")
                banner_lines.append("  Install missing tools:")
                for name, cmd in missing_clis:
                    banner_lines.append(f"    {cmd}")
        banner_lines.append("")
    banner_lines.append("")
    banner = "\r\n".join(banner_lines)
    try:
        os.write(master_fd, banner.encode("utf-8"))
    except OSError:
        pass


async def _handle_connection(websocket) -> None:
    """Handle a new WebSocket connection."""
    # Get request path - websockets 15.0.1 uses websocket.path attribute
    # (remove_path_argument skips the path param when it has a default value)
    raw_path = getattr(websocket, "path", "")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(raw_path).query)
    token = params.get("token", [None])[0]
    if not token or token != AUTH_TOKEN:
        logger.warning("Rejected connection: invalid token")
        await websocket.close(4001, "Authentication failed")
        return

    # Parse terminal size from query params
    cols = int(params.get("cols", ["80"])[0])
    rows = int(params.get("rows", ["24"])[0])

    # Build shell command
    shell = SHELL_CMD or os.environ.get("SHELL", "/bin/bash")
    cmd = [shell, "-l"]  # -l for login shell to load profiles

    env = _build_env()
    work_dir = WORK_DIR or os.path.expanduser("~")

    # Update bashrc with aliases if tokens are configured
    bashrc_path = os.path.join(os.path.expanduser("~"), ".bashrc")
    try:
        aliases = []
        if ANTHROPIC_TOKEN:
            aliases.append("alias claude='claude --bare'")
        if OPENAI_TOKEN:
            # qwen uses --auth-type openai and reads OPENAI_API_KEY env var
            aliases.append("alias qwen='qwen --auth-type openai'")
        try:
            with open(bashrc_path) as f:
                existing = f.read()
        except FileNotFoundError:
            existing = ""
        new_aliases = [a for a in aliases if a not in existing]
        if new_aliases:
            with open(bashrc_path, "a") as f:
                f.write("\n# Open ACE: AI assistant aliases for proxy\n")
                for alias in new_aliases:
                    f.write(alias + "\n")
    except Exception as e:
        logger.warning("Failed to update bashrc: %s", e)

    try:
        master_fd, pid = _spawn_pty(cmd, env, work_dir)
    except Exception as e:
        logger.error("Failed to spawn PTY: %s", e)
        await websocket.close(1011, "Failed to create terminal")
        return

    session = TerminalSession(websocket, master_fd, pid)
    session.resize(cols, rows)

    # Write welcome banner
    _write_banner(master_fd)

    logger.info("Terminal session started: pid=%d cols=%d rows=%d", pid, cols, rows)

    try:
        await asyncio.gather(session.relay_output(), session.relay_input())
    finally:
        session.kill()
        try:
            os.close(master_fd)
        except OSError:
            pass
        logger.info("Terminal session ended: pid=%d", pid)


async def _run_server(port: int) -> None:
    """Start the WebSocket server."""
    async with serve(_handle_connection, "0.0.0.0", port, subprotocols=["binary"]) as server:
        actual_port = server.sockets[0].getsockname()[1]
        logger.info("Terminal server listening on ws://0.0.0.0:%d", actual_port)
        print(f"READY:{actual_port}", flush=True)
        await asyncio.Future()  # Block forever


def main() -> None:
    global AUTH_TOKEN, PROXY_URL, ANTHROPIC_TOKEN, OPENAI_TOKEN, WORK_DIR, SHELL_CMD

    parser = argparse.ArgumentParser(description="Open ACE WebSocket Terminal Server")
    parser.add_argument("--token", required=True, help="Authentication token")
    parser.add_argument("--port", type=int, default=0, help="Port to listen on (0=auto)")
    parser.add_argument("--proxy-url", default="", help="Open ACE LLM proxy URL")
    parser.add_argument(
        "--anthropic-token",
        default="",
        help="Proxy token for Anthropic/Claude API (or --proxy-token for backward compat)",
    )
    parser.add_argument("--openai-token", default="", help="Proxy token for OpenAI/Qwen API")
    parser.add_argument(
        "--proxy-token",
        default="",
        help="(Deprecated) Single proxy token, use --anthropic-token instead",
    )
    parser.add_argument("--work-dir", default="", help="Working directory")
    parser.add_argument("--shell", default="", help="Shell command")
    args = parser.parse_args()

    AUTH_TOKEN = args.token
    PROXY_URL = args.proxy_url
    # Support backward compat: --proxy-token maps to --anthropic-token
    ANTHROPIC_TOKEN = args.anthropic_token or args.proxy_token
    OPENAI_TOKEN = args.openai_token
    WORK_DIR = args.work_dir
    SHELL_CMD = args.shell

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    port = args.port

    asyncio.run(_run_server(port))


if __name__ == "__main__":
    main()
