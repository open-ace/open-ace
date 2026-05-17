"""
Open ACE Remote Agent - WebSocket Terminal Server (Single-PTY Mode)

Standalone asyncio WebSocket server that provides web-based terminal access.
PTY process is created once at startup and persists across WebSocket reconnections.
This allows users to refresh browser and resume their terminal session.

Started as a subprocess by the remote agent when a terminal session is requested.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
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
TERMINAL_ID = ""  # For identification in logs and persistence

# Output history buffer size (for reconnection screen restore)
OUTPUT_HISTORY_SIZE = 64 * 1024  # 64 KB


class SinglePtyTerminalServer:
    """
    Single-PTY terminal server with WebSocket reconnection support.

    PTY is created at startup and persists across WebSocket connections.
    When a WebSocket disconnects (browser refresh), the PTY continues running.
    New WebSocket connections receive buffered output history for screen restore.
    """

    def __init__(self):
        self.master_fd: int | None = None
        self.pty_pid: int | None = None
        self._output_buffer: bytearray = bytearray()
        self._active_websockets: set = set()
        self._pty_alive = True
        self._output_lock = asyncio.Lock()
        self._ws_lock = asyncio.Lock()
        self._pty_cols = 80
        self._pty_rows = 24

    def spawn_pty(self) -> bool:
        """Spawn PTY process once at startup."""
        if SHELL_CMD:
            cmd = [SHELL_CMD]
        else:
            menu_script = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "terminal_menu.py"
            )
            cmd = [sys.executable, menu_script]
        env = _build_env()
        work_dir = WORK_DIR or os.path.expanduser("~")

        # Update bashrc with aliases (for "Exit to shell" option)
        self._update_bashrc()

        try:
            self.master_fd, self.pty_pid = _spawn_pty(cmd, env, work_dir)
            logger.info(
                "PTY spawned: pid=%d fd=%d work_dir=%s", self.pty_pid, self.master_fd, work_dir
            )
            return True
        except Exception as e:
            logger.error("Failed to spawn PTY: %s", e)
            return False

    def _update_bashrc(self) -> None:
        """Update bashrc with AI CLI aliases."""
        bashrc_path = os.path.join(os.path.expanduser("~"), ".bashrc")
        try:
            aliases = []
            if ANTHROPIC_TOKEN:
                aliases.append("alias claude='claude --bare'")
            if OPENAI_TOKEN:
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

    def resize_pty(self, cols: int, rows: int) -> None:
        """Resize the PTY terminal."""
        self._pty_cols = cols
        self._pty_rows = rows
        if self.master_fd is not None:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except Exception as e:
                logger.debug("Resize failed: %s", e)

    async def add_websocket(self, websocket) -> bool:
        """Add a WebSocket connection to the terminal."""
        async with self._ws_lock:
            self._active_websockets.add(websocket)
            logger.info("WebSocket connected, active count: %d", len(self._active_websockets))
        return True

    async def remove_websocket(self, websocket) -> None:
        """Remove a WebSocket connection (PTY keeps running)."""
        async with self._ws_lock:
            self._active_websockets.discard(websocket)
            logger.info("WebSocket disconnected, active count: %d", len(self._active_websockets))

    async def send_history_to_websocket(self, websocket) -> None:
        """Send buffered output history to a new WebSocket connection."""
        async with self._output_lock:
            if len(self._output_buffer) > 0:
                # Send last N bytes of history for screen restore
                history = bytes(self._output_buffer[-OUTPUT_HISTORY_SIZE:])
                try:
                    await websocket.send(history)
                    logger.debug("Sent %d bytes of history to new connection", len(history))
                except Exception as e:
                    logger.warning("Failed to send history: %s", e)

    async def broadcast_output(self, data: bytes) -> None:
        """Broadcast PTY output to all active WebSockets and buffer it."""
        # Buffer the output for reconnection
        async with self._output_lock:
            self._output_buffer.extend(data)
            # Limit buffer size
            if len(self._output_buffer) > OUTPUT_HISTORY_SIZE * 2:
                self._output_buffer = self._output_buffer[-OUTPUT_HISTORY_SIZE:]

        # Broadcast to all active WebSockets
        async with self._ws_lock:
            dead_sockets = []
            for ws in self._active_websockets:
                try:
                    await ws.send(data)
                except Exception:
                    dead_sockets.append(ws)
            # Remove dead sockets
            for ws in dead_sockets:
                self._active_websockets.discard(ws)

    async def relay_output_loop(self) -> None:
        """Read PTY output continuously and broadcast to WebSockets."""
        while self._pty_alive and self.master_fd is not None:
            try:
                ready, _, _ = select.select([self.master_fd], [], [], 0.1)
                if ready:
                    try:
                        data = os.read(self.master_fd, 65536)
                        if data:
                            await self.broadcast_output(data)
                        else:
                            # PTY closed (process exited)
                            logger.info("PTY output stream closed (process likely exited)")
                            self._pty_alive = False
                            break
                    except OSError as e:
                        logger.info("PTY read error: %s (process likely exited)", e)
                        self._pty_alive = False
                        break
                await asyncio.sleep(0.01)
            except Exception as e:
                logger.error("Output relay error: %s", e)
                self._pty_alive = False
                break

        # PTY exited - notify all WebSockets
        if not self._pty_alive:
            await self._notify_pty_exit()

    async def _notify_pty_exit(self) -> None:
        """Notify all WebSockets that PTY has exited."""
        async with self._ws_lock:
            for ws in self._active_websockets:
                try:
                    await ws.send(b"\r\n\x1b[33m[Terminal process exited]\x1b[0m\r\n")
                except Exception:
                    pass

    async def handle_websocket_input(self, websocket) -> None:
        """Handle input from a single WebSocket connection."""
        try:
            async for message in websocket:
                if not self._pty_alive or self.master_fd is None:
                    break

                if isinstance(message, str):
                    # JSON control message (resize, etc.)
                    try:
                        ctrl = json.loads(message)
                        if ctrl.get("type") == "resize":
                            cols = ctrl.get("cols", 80)
                            rows = ctrl.get("rows", 24)
                            self.resize_pty(cols, rows)
                        continue
                    except (json.JSONDecodeError, ValueError):
                        # Not JSON, treat as raw text input
                        message = message.encode("utf-8")

                if isinstance(message, bytes):
                    try:
                        os.write(self.master_fd, message)
                    except OSError as e:
                        logger.warning("PTY write error: %s", e)
                        break
        except websockets.exceptions.ConnectionClosed:
            logger.debug("WebSocket connection closed normally")
        except Exception as e:
            logger.warning("WebSocket input error: %s", e)

    def kill_pty(self) -> None:
        """Terminate the PTY process."""
        self._pty_alive = False
        if self.pty_pid is not None:
            try:
                os.kill(self.pty_pid, signal.SIGTERM)
                logger.info("Sent SIGTERM to PTY pid=%d", self.pty_pid)
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.warning("Failed to kill PTY: %s", e)
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None

    def is_pty_alive(self) -> bool:
        """Check if PTY process is still running."""
        if self.pty_pid is None:
            return False
        try:
            # Check if process exists (doesn't raise if process is zombie)
            pid, status = os.waitpid(self.pty_pid, os.WNOHANG)
            if pid != 0:
                # Process has exited
                logger.info("PTY process %d exited with status %d", pid, status)
                self._pty_alive = False
                return False
            return True
        except ChildProcessError:
            # No child process
            self._pty_alive = False
            return False


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


# Global terminal server instance
_terminal_server: SinglePtyTerminalServer | None = None


async def _handle_connection(websocket) -> None:
    """Handle a new WebSocket connection - attach to existing PTY."""
    global _terminal_server

    if _terminal_server is None:
        logger.error("Terminal server not initialized")
        await websocket.close(1011, "Server not ready")
        return

    # Authenticate
    raw_path = getattr(websocket, "path", "")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(raw_path).query)
    token = params.get("token", [None])[0]
    if not token or token != AUTH_TOKEN:
        logger.warning("Rejected connection: invalid token")
        await websocket.close(4001, "Authentication failed")
        return

    # Check if PTY is alive
    if not _terminal_server.is_pty_alive():
        logger.warning("PTY has exited, rejecting connection")
        await websocket.close(1011, "Terminal process has exited")
        return

    # Parse terminal size from query params
    cols = int(params.get("cols", ["80"])[0])
    rows = int(params.get("rows", ["24"])[0])

    # Resize PTY to match client
    _terminal_server.resize_pty(cols, rows)

    # Add this WebSocket to active set
    await _terminal_server.add_websocket(websocket)

    try:
        # Send buffered history for screen restore
        await _terminal_server.send_history_to_websocket(websocket)

        # Handle input from this WebSocket
        await _terminal_server.handle_websocket_input(websocket)
    finally:
        # Remove WebSocket (PTY keeps running for reconnection)
        await _terminal_server.remove_websocket(websocket)


async def _run_server(port: int) -> None:
    """Start the WebSocket server with a persistent PTY."""
    global _terminal_server

    # Create and spawn PTY once
    _terminal_server = SinglePtyTerminalServer()
    if not _terminal_server.spawn_pty():
        logger.error("Failed to spawn PTY, exiting")
        return

    # Start output relay loop
    output_task = asyncio.create_task(_terminal_server.relay_output_loop())

    try:
        async with serve(_handle_connection, "0.0.0.0", port, subprotocols=["binary"]) as server:
            actual_port = server.sockets[0].getsockname()[1]
            logger.info("Terminal server listening on ws://0.0.0.0:%d", actual_port)
            logger.info("PTY pid=%d ready for connections", _terminal_server.pty_pid)
            print(f"READY:{actual_port}", flush=True)

            # Wait until PTY exits or server is stopped
            while _terminal_server.is_pty_alive():
                await asyncio.sleep(1)

            logger.info("PTY process exited, shutting down server")

    finally:
        # Cancel output task and clean up
        output_task.cancel()
        try:
            await output_task
        except asyncio.CancelledError:
            pass
        _terminal_server.kill_pty()


def main() -> None:
    global AUTH_TOKEN, PROXY_URL, ANTHROPIC_TOKEN, OPENAI_TOKEN, WORK_DIR, SHELL_CMD, TERMINAL_ID

    parser = argparse.ArgumentParser(description="Open ACE WebSocket Terminal Server")
    parser.add_argument("--token", required=True, help="Authentication token")
    parser.add_argument("--terminal-id", default="", help="Terminal session ID for persistence")
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
    TERMINAL_ID = args.terminal_id
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
