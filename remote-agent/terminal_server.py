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
import hmac
import json
import logging
import os
import select
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

if os.name != "nt":
    import fcntl
    import pty
    import termios
else:  # pragma: no cover - exercised by Windows runtime/tests via monkeypatch
    fcntl = None
    pty = None
    termios = None

from cli_adapters.base import collect_custom_envkeys

try:
    import websockets
    from websockets.server import serve
except ImportError:
    print("Error: 'websockets' package is required.", file=sys.stderr)
    print("Install with: pip install 'websockets>=13.0,<17.0'", file=sys.stderr)
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
        self.process: subprocess.Popen[bytes] | None = None
        self._output_buffer: bytearray = bytearray()
        self._active_websockets: set = set()
        self._pty_alive = True
        self._output_lock = asyncio.Lock()
        self._ws_lock = asyncio.Lock()
        self._pty_cols = 80
        self._pty_rows = 24
        self._uses_pty = os.name != "nt"

    def spawn_pty(self) -> bool:
        """Spawn PTY process once at startup."""
        if SHELL_CMD:
            cmd = _parse_shell_command(SHELL_CMD)
        else:
            menu_script = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "terminal_menu.py"
            )
            cmd = [sys.executable, menu_script]
        env = _build_env()
        work_dir = WORK_DIR or os.path.expanduser("~")

        # Update bashrc with aliases (for "Exit to shell" option)
        self._update_shell_profile()

        try:
            if self._uses_pty:
                self.master_fd, self.pty_pid = _spawn_pty(cmd, env, work_dir)
                logger.info(
                    "PTY spawned: pid=%d fd=%d work_dir=%s",
                    self.pty_pid,
                    self.master_fd,
                    work_dir,
                )
            else:
                self.process = _spawn_pipe_process(cmd, env, work_dir)
                self.pty_pid = self.process.pid
                logger.info("Pipe terminal spawned: pid=%d work_dir=%s", self.pty_pid, work_dir)
            return True
        except Exception as e:
            logger.error("Failed to spawn PTY: %s", e)
            return False

    def _update_shell_profile(self) -> None:
        """Update shell profile with AI CLI aliases on Unix shells."""
        if os.name == "nt":
            return
        bashrc_path = os.path.join(os.path.expanduser("~"), ".bashrc")
        try:
            aliases = []
            if ANTHROPIC_TOKEN:
                aliases.append("alias claude='claude --bare'")
            if OPENAI_TOKEN:
                aliases.append("alias qwen='qwen --auth-type openai'")
            openace_cli = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "openace_cli.py",
            )
            if os.path.exists(openace_cli):
                aliases.append(f"alias openace='{sys.executable} {openace_cli}'")
            try:
                with open(bashrc_path) as f:
                    existing = f.read()
            except FileNotFoundError:
                existing = ""
            new_aliases = [a for a in aliases if a not in existing]
            if new_aliases:
                # Create backup before modifying
                backup_path = bashrc_path + ".open-ace-backup"
                if os.path.exists(bashrc_path) and not os.path.exists(backup_path):
                    import shutil

                    shutil.copy2(bashrc_path, backup_path)
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
        if self._uses_pty and self.master_fd is not None:
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
        if not self._uses_pty:
            await self._relay_pipe_output_loop()
            return

        loop = asyncio.get_event_loop()
        while self._pty_alive and self.master_fd is not None:
            try:
                ready, _, _ = await loop.run_in_executor(
                    None, lambda: select.select([self.master_fd], [], [], 0.05)
                )
                if ready:
                    try:
                        data = os.read(self.master_fd, 65536)
                        if data:
                            await self.broadcast_output(data)
                        else:
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

    async def _relay_pipe_output_loop(self) -> None:
        """Read subprocess output continuously and broadcast to WebSockets."""
        loop = asyncio.get_event_loop()
        while self._pty_alive and self.process is not None and self.process.stdout is not None:
            try:
                reader = getattr(self.process.stdout, "read1", self.process.stdout.read)
                data = await loop.run_in_executor(None, reader, 65536)
                if data:
                    await self.broadcast_output(data)
                else:
                    logger.info("Terminal output stream closed (process likely exited)")
                    self._pty_alive = False
                    break
            except Exception as e:
                logger.error("Output relay error: %s", e)
                self._pty_alive = False
                break

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
                # Path-aware guard: the PTY model needs master_fd to write to,
                # while the Windows pipe model writes via process.stdin and never
                # assigns master_fd. The old `master_fd is None` check short-
                # circuited the pipe path on the first message, making the
                # restored Windows terminal receive-only.
                if not self._pty_alive or (self._uses_pty and self.master_fd is None):
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
                        if self._uses_pty:
                            os.write(self.master_fd, message)
                        elif self.process is not None and self.process.stdin is not None:
                            self.process.stdin.write(message)
                            self.process.stdin.flush()
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
        if self._uses_pty and self.pty_pid is not None:
            try:
                os.kill(self.pty_pid, signal.SIGTERM)
                logger.info("Sent SIGTERM to PTY pid=%d", self.pty_pid)
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.warning("Failed to kill PTY: %s", e)
        elif self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
                logger.info("Terminated terminal process pid=%d", self.process.pid)
            except subprocess.TimeoutExpired:
                self.process.kill()
            except Exception as e:
                logger.warning("Failed to kill terminal process: %s", e)
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        if self.process is not None:
            for stream_name in ("stdin", "stdout", "stderr"):
                stream = getattr(self.process, stream_name, None)
                if stream is None:
                    continue
                try:
                    stream.close()
                except Exception:
                    pass
            self.process = None

    def is_pty_alive(self) -> bool:
        """Check if PTY process is still running."""
        if not self._uses_pty:
            if self.process is None:
                return False
            return_code = self.process.poll()
            if return_code is None:
                return True
            logger.info("Terminal process %d exited with status %d", self.process.pid, return_code)
            self._pty_alive = False
            return False

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


def _spawn_pipe_process(
    shell_cmd: list[str], env: dict[str, str], work_dir: str
) -> subprocess.Popen[bytes]:
    """Spawn a subprocess with stdin/stdout pipes for Windows-compatible terminal I/O."""
    cwd = work_dir or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")

    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return subprocess.Popen(
        shell_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        cwd=cwd,
        bufsize=0,
        creationflags=creationflags,
    )


def _parse_shell_command(command: str) -> list[str]:
    """Split a user-provided shell command into argv with platform-aware rules."""
    try:
        return shlex.split(command, posix=os.name != "nt")
    except ValueError:
        return [command]


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

            # Fallback: inject custom envKeys from qwen settings
            # (e.g. BAILIAN_CODING_PLAN_API_KEY) so the proxy token
            # is available regardless of which envKey the CLI reads.
            try:
                settings_path = Path.home() / ".qwen" / "settings.json"
                env.update(collect_custom_envkeys(settings_path, OPENAI_TOKEN))
            except Exception:
                pass  # Non-critical fallback
    env["TERM"] = "xterm-256color"
    # Pass terminal ID to child processes for accurate session-terminal association
    if TERMINAL_ID:
        env["OPEN_ACE_TERMINAL_ID"] = TERMINAL_ID
    return env


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
    # websockets >= 13 stores path in request.path; older versions used websocket.path
    raw_path = ""
    if hasattr(websocket, "request") and websocket.request is not None:
        raw_path = websocket.request.path
    elif hasattr(websocket, "path"):
        raw_path = websocket.path
    params = urllib.parse.parse_qs(urllib.parse.urlparse(raw_path).query)
    token = params.get("token", [None])[0]
    if not token or not hmac.compare_digest(token, AUTH_TOKEN):
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
    parser.add_argument("--terminal-id", default="", help="Terminal session ID for persistence")
    parser.add_argument("--port", type=int, default=0, help="Port to listen on (0=auto)")
    parser.add_argument("--proxy-url", default="", help="Open ACE LLM proxy URL")
    parser.add_argument("--work-dir", default="", help="Working directory")
    parser.add_argument("--shell", default="", help="Shell command")
    args = parser.parse_args()

    # Read tokens from environment variables (not CLI args, to avoid ps aux exposure)
    AUTH_TOKEN = os.environ.get("OPEN_ACE_TERMINAL_TOKEN", "")
    TERMINAL_ID = args.terminal_id
    PROXY_URL = args.proxy_url
    ANTHROPIC_TOKEN = os.environ.get("OPEN_ANTHROPIC_TOKEN", "")
    OPENAI_TOKEN = os.environ.get("OPEN_OPENAI_TOKEN", "")
    WORK_DIR = args.work_dir
    SHELL_CMD = args.shell

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            # Log to file to avoid filling stderr pipe buffer
            logging.FileHandler(
                os.path.join(
                    tempfile.gettempdir(),
                    f"terminal_server_{args.terminal_id[:8]}.log",
                )
            ),
        ],
    )

    port = args.port

    # Set up signal handlers for graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown_handler():
        logger.info("Received shutdown signal, cleaning up...")
        if _terminal_server:
            _terminal_server.kill_pty()
        loop.stop()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown_handler)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _shutdown_handler())

    try:
        loop.run_until_complete(_run_server(port))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
