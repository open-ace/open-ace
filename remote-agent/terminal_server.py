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
PROXY_TOKEN = ""
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
    if PROXY_URL and PROXY_TOKEN:
        env["ANTHROPIC_API_KEY"] = PROXY_TOKEN
        env["ANTHROPIC_BASE_URL"] = PROXY_URL
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["TERM"] = "xterm-256color"
    return env


def _write_banner(master_fd: int) -> None:
    """Write a welcome banner to the terminal."""
    banner_lines = [
        "",
        "\033[1;36m========================================\033[0m",
        "\033[1;36m  Open ACE Remote Terminal\033[0m",
        "\033[1;36m========================================\033[0m",
    ]
    if PROXY_URL:
        banner_lines.extend(
            [
                "",
                "\033[2m  Claude Code is pre-configured.\033[0m",
                "\033[2m  Run: claude\033[0m",
                "",
            ]
        )
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

    # Create bashrc with claude alias if proxy is configured
    if PROXY_URL and PROXY_TOKEN:
        bashrc_path = os.path.join(os.path.expanduser("~"), ".bashrc")
        try:
            # Append alias to .bashrc if not already present
            alias_line = "alias claude='claude --bare'"
            try:
                with open(bashrc_path) as f:
                    existing = f.read()
            except FileNotFoundError:
                existing = ""
            if alias_line not in existing:
                with open(bashrc_path, "a") as f:
                    f.write("\n# Open ACE: use --bare mode for proxy\n")
                    f.write(alias_line + "\n")
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
    global AUTH_TOKEN, PROXY_URL, PROXY_TOKEN, WORK_DIR, SHELL_CMD

    parser = argparse.ArgumentParser(description="Open ACE WebSocket Terminal Server")
    parser.add_argument("--token", required=True, help="Authentication token")
    parser.add_argument("--port", type=int, default=0, help="Port to listen on (0=auto)")
    parser.add_argument("--proxy-url", default="", help="Open ACE LLM proxy URL")
    parser.add_argument("--proxy-token", default="", help="Proxy token for LLM auth")
    parser.add_argument("--work-dir", default="", help="Working directory")
    parser.add_argument("--shell", default="", help="Shell command")
    args = parser.parse_args()

    AUTH_TOKEN = args.token
    PROXY_URL = args.proxy_url
    PROXY_TOKEN = args.proxy_token
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
