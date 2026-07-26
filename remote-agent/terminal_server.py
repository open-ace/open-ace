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

    # POSIX stubs for the Windows-only Job Object / taskkill helpers (real
    # implementations live in the ``else`` branch below). Defining the names
    # here keeps static analysis happy; every call site guards on
    # ``os.name == "nt"`` / ``_uses_pty`` so these never execute on POSIX.

    def _create_kill_on_close_job():  # type: ignore[no-redef]  # pragma: no cover
        return None

    def _assign_pid_to_job(handle, pid):  # type: ignore[no-redef]  # pragma: no cover
        return False

    def _close_native_handle(handle):  # type: ignore[no-redef]  # pragma: no cover
        return False

    def _taskkill_tree(pid):  # type: ignore[no-redef]  # pragma: no cover
        return False

else:  # pragma: no cover - exercised by Windows runtime/tests via monkeypatch
    fcntl = None
    pty = None
    termios = None

    import ctypes
    from ctypes import wintypes

    # Win32 Job Object support. Binding the shell process tree to a Job with
    # JOB_OBJECT_LIMIT_KILL_ON_CLOSE guarantees the whole tree (including
    # grandchildren that inherit the stdout write-end) is reaped when this
    # server exits -- including hard-kill and crash paths where kill_pty()
    # and taskkill never run. That write-end cleanup is also what lets the
    # blocking relay reader receive EOF and return (see kill_pty docstring).
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_TERMINATE = 0x0001

    class IO_COUNTERS(ctypes.Structure):  # pragma: no cover
        """I/O accounting counters for a job object."""

        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # pragma: no cover
        """Basic resource limits for a job object."""

        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_ulonglong),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # pragma: no cover
        """Extended resource limits including memory and I/O accounting."""

        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def _create_kill_on_close_job():  # type: ignore[no-redef]  # pragma: no cover
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _kernel32.SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            err = ctypes.WinError(ctypes.get_last_error())
            _kernel32.CloseHandle(handle)
            raise err
        return handle

    def _assign_pid_to_job(handle, pid):  # type: ignore[no-redef]  # pragma: no cover
        proc_handle = _kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not proc_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not _kernel32.AssignProcessToJobObject(handle, proc_handle):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _kernel32.CloseHandle(proc_handle)

    def _close_native_handle(handle):  # type: ignore[no-redef]  # pragma: no cover
        return bool(_kernel32.CloseHandle(handle))

    def _taskkill_tree(pid):  # type: ignore[no-redef]  # pragma: no cover
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return True


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

# Upper bound for waiting on the output relay task during shutdown. If the
# blocking reader is stuck (dual-failure residual: no Job AND taskkill failed,
# so a grandchild keeps the stdout write-end open), we abandon the wait here
# rather than hang _run_server's finally. A truly wedged process is still
# reaped by the agent-layer proc.kill() watchdog; we deliberately do not use
# os._exit() (atexit ordering / graceful-close short-circuit risk). See
# kill_pty() docstring for the residual statement.
OUTPUT_TASK_SHUTDOWN_TIMEOUT = 8.0


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
        # Win32 Job Object handle binding the shell process tree (Windows pipe
        # mode only). None on POSIX or when Job binding failed. Closing this
        # handle triggers KILL_ON_JOB_CLOSE and reaps the whole tree -- the
        # only cleanup that covers hard-kill/crash paths where kill_pty() and
        # taskkill cannot run.
        self._job_handle = None
        # One-shot guard so resize_pty() warns once about the pipe-mode resize
        # limitation instead of spamming on every keystroke-driven resize.
        self._resize_warned = False

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
                self._bind_job_for_process()
            return True
        except Exception as e:
            logger.error("Failed to spawn PTY: %s", e)
            return False

    def _bind_job_for_process(self) -> None:
        """Bind the spawned shell tree to a Win32 Job Object (Windows only).

        With JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, closing the handle (on normal
        shutdown, hard-kill, or crash) instructs the kernel to kill the entire
        process tree including grandchildren -- the only mechanism that covers
        paths where kill_pty()/taskkill cannot run, and the lever that closes
        the stdout write-ends so the blocking relay reader can receive EOF.
        POSIX is a no-op.

        Residual: if Job creation fails here AND a later taskkill /T also
        fails in kill_pty(), the shell tree may be orphaned and the relay
        reader may stay pinned; see kill_pty() docstring.
        """
        if os.name != "nt" or self.process is None:
            return
        handle = None
        try:
            handle = _create_kill_on_close_job()
            _assign_pid_to_job(handle, self.process.pid)
            self._job_handle = handle
            handle = None  # ownership transferred to the instance
            logger.info("Bound shell tree to Job Object (pid=%d)", self.process.pid)
        except Exception as e:
            # Fallback: run without a Job; the soft-kill path falls back to
            # taskkill /T. Never let Job failure prevent shell startup.
            logger.warning(
                "Job Object binding failed for pid=%d (falling back to taskkill /T): %s",
                self.process.pid,
                e,
            )
            self._job_handle = None
        finally:
            if handle is not None:
                try:
                    _close_native_handle(handle)
                except Exception:
                    pass

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
        """Resize the PTY terminal.

        On Unix (PTY mode) this applies a TIOCSWINSZ ioctl. On Windows (pipe
        mode) there is no tty to resize -- the piped shell keeps wrapping at
        the original width -- so we only record the requested size and warn
        once. Writing an ANSI size sequence is deliberately avoided: stdin is
        a pipe, not a tty, so those bytes would be consumed as shell input and
        pollute the session. Real resize support requires ConPTY
        (pywinpty/winpty or the Win32 API), tracked as future work.
        """
        self._pty_cols = cols
        self._pty_rows = rows
        if self._uses_pty and self.master_fd is not None:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            except Exception as e:
                logger.debug("Resize failed: %s", e)
        elif not self._uses_pty and not self._resize_warned:
            logger.info(
                "Terminal resize requested to %dx%d but Windows pipe mode has no tty; "
                "the shell will keep wrapping at the original width.",
                cols,
                rows,
            )
            self._resize_warned = True

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
                # Prefer read1() ("return as soon as any data is available, do
                # not block waiting to fill the buffer"). With bufsize=0 the
                # pipe stdout is typically a raw io.FileIO which has NO read1,
                # so we fall back to FileIO.read(n) -- itself a single readinto
                # that also returns as soon as any bytes are available. The
                # fallback therefore does NOT widen the blocking window (it
                # never blocks until 65536 bytes arrive); the earlier "read
                # blocks to fill the buffer" reasoning was wrong. Either way,
                # neither read1 nor read returns until the write-end closes
                # (EOF), so the reliable unblock lever is the process-tree kill
                # in kill_pty() (which closes every write-end), not the choice
                # of read call here. Verified by inspecting stdout's type at
                # runtime; no extra non-blocking refactor introduced.
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
        """Terminate the terminal process tree.

        Soft-kill ordering (Windows pipe mode):

        1. Kill the *whole tree* first -- close the Job handle
           (KILL_ON_JOB_CLOSE), or when no Job is bound fall back to
           ``taskkill /T /F /PID``. Closing every stdout write-end is what
           lets the blocking relay reader receive EOF and return; merely
           closing the read-end from another thread is unreliable (POSIX
           close-in-use is UB; Windows CloseHandle vs a pending ReadFile
           races), so the tree kill is the lever, not the read-end close.
        2. Bounded ``wait()`` for the tree to exit so write-ends drain.
        3. Close stream handles as cleanup -- never relied on to interrupt a
           concurrent blocking read.

        Residual (deliberately accepted, see plan #1825): if Job binding
        failed AND ``taskkill /T`` also fails, (i) the shell tree is orphaned
        (the original F2 lives in this narrow window -- agent's proc.kill()
        targets terminal_server, not the orphaned shell, so it needs manual
        cleanup), and (ii) the relay reader stays pinned until the process is
        force-killed. Agent-initiated stop is reaped by
        ``agent._stop_terminal_process`` proc.kill(); however agent shutdown
        intentionally leaves terminal servers running (agent.py: "terminal
        servers left running"), so a natural-exit under dual-failure may
        wedge terminal_server until the terminal is restarted. Dual-failure
        itself is a narrow case (Job almost always succeeds; taskkill ships
        with Windows), so no os._exit() escape hatch is introduced.
        """
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
            pid = self.process.pid
            # 1. Tree kill -- the lever that closes write-ends -> reader EOF.
            if self._job_handle is not None:
                try:
                    _close_native_handle(self._job_handle)
                    logger.info("Closed Job handle to kill shell tree pid=%d", pid)
                except Exception as e:
                    logger.warning("Job close failed for pid=%d: %s", pid, e)
                self._job_handle = None
            else:
                # No Job bound: fall back to taskkill /T immediately.
                try:
                    _taskkill_tree(pid)
                except Exception as e:
                    logger.warning("taskkill /T failed for pid=%d: %s", pid, e)
            # 2. Bounded wait for the tree to exit.
            try:
                self.process.wait(timeout=5)
                logger.info("Terminated terminal process tree pid=%d", pid)
            except subprocess.TimeoutExpired:
                # Job (or initial taskkill) did not clear the tree in time:
                # the tree-kill signalled failure, so escalate with taskkill /T.
                logger.warning(
                    "Terminal process pid=%d did not exit in 5s; escalating with taskkill /T",
                    pid,
                )
                try:
                    _taskkill_tree(pid)
                except Exception as e:
                    logger.warning("taskkill /T escalation failed for pid=%d: %s", pid, e)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logger.warning("Terminal process pid=%d still alive after escalation", pid)
            except Exception as e:
                logger.warning("Failed to kill terminal process: %s", e)
        # 3. Close remaining handles (cleanup; not relied on to interrupt reads).
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
        # Idempotency guard: clear the Job handle even if the branch above was
        # skipped (e.g. a second call with process already None), preventing
        # double CloseHandle from _run_server finally / signal handler / explicit.
        if self._job_handle is not None:
            try:
                _close_native_handle(self._job_handle)
            except Exception:
                pass
            self._job_handle = None

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
        # Cancel output task and clean up. The relay reader may be blocked in
        # an executor thread on a read that never returns EOF (dual-failure
        # residual: no Job AND taskkill failed). Bounding the wait keeps
        # _run_server's finally from hanging; a truly wedged process is still
        # reaped by the agent-layer proc.kill() watchdog.
        output_task.cancel()
        try:
            await asyncio.wait_for(output_task, timeout=OUTPUT_TASK_SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(
                "Output relay task did not finish within %.1fs; "
                "leaving cleanup to the agent-layer proc.kill() watchdog",
                OUTPUT_TASK_SHUTDOWN_TIMEOUT,
            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Output relay task ended with error during shutdown: %s", e)
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
