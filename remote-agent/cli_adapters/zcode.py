"""
Open ACE - ZCode CLI Adapter

Adapter for the ZCode CLI tool. ZCode is the CLI engine bundled with the
ZCode desktop app on macOS and resolves to a Node.js entry script:

    /Applications/ZCode.app/Contents/Resources/glm/zcode.cjs

It is invoked as ``node <engine> ...``. Unlike Claude Code, ZCode does NOT
support the ``--print --input-format stream-json`` stdin protocol or a
``control_request`` SDK control plane. It offers two non-interactive modes:

  * single-shot: ``node <engine> --prompt "<text>" --json``
      one request per process; the response JSON carries token usage.
  * persistent : ``node <engine> app-server``
      a long-lived process speaking the *ZCode Protocol* (a method/params
      dialect, not JSON-RPC) over stdio, with ``session/create|send|events|
      resume|usage|list`` methods.

For autonomous single-task runs the executor uses ``build_single_shot_args``;
for streaming multi-turn sessions the executor talks to ``app-server``.
"""

from __future__ import annotations

import logging
import os
import shutil

from .base import BaseCLIAdapter

logger = logging.getLogger(__name__)

# Path to the CLI engine bundled inside the ZCode desktop app.
_APP_ENGINE = "/Applications/ZCode.app/Contents/Resources/glm/zcode.cjs"


def _resolve_engine() -> str:
    """Return the CLI engine path/invocation.

    Prefer the bundled engine inside the desktop app. Otherwise fall back to
    a ``zcode`` symlink/script on PATH (set up via ``get_install_command``).
    """
    if os.path.isfile(_APP_ENGINE):
        return _APP_ENGINE
    found = shutil.which("zcode")
    if found:
        return found
    return "zcode"


class ZCodeAdapter(BaseCLIAdapter):
    """Adapter for the ZCode CLI tool."""

    EXECUTABLE = "zcode"
    DISPLAY_NAME = "ZCode"
    ENGINE = _resolve_engine()

    def get_install_command(self) -> str:
        """Return the command to install ZCode.

        ZCode ships with the ZCode desktop app. To expose the engine on PATH
        as ``zcode``, symlink the bundled engine:

            sudo ln -s {engine} /usr/local/bin/zcode
        """
        return (
            f"sudo ln -s {_APP_ENGINE} /usr/local/bin/zcode "
            f"|| echo 'ZCode ships with the ZCode desktop app; "
            f"see app bundle at {_APP_ENGINE}'"
        )

    def check_installed(self) -> bool:
        """Check if the ZCode engine is available."""
        return os.path.isfile(_APP_ENGINE) or shutil.which("zcode") is not None

    def get_env_vars(self, proxy_url: str, proxy_token: str) -> dict[str, str]:
        """
        Get environment variables for ZCode.

        ZCode's ``anthropic`` provider factory reads both ``ANTHROPIC_BASE_URL``
        and ``ANTHROPIC_API_KEY`` from the environment (verified in zcode.cjs's
        ``wJ``/``sMe`` factories), mirroring the Claude adapter. We set both so
        the per-session proxy token authenticates every request through the Open
        ACE LLM proxy, and the base URL routes to it. This takes precedence over
        any credential in ``~/.zcode/cli/config.json``.
        """
        base = proxy_url.rstrip("/")
        return {
            "ANTHROPIC_API_KEY": proxy_token,
            "ANTHROPIC_BASE_URL": base,
        }

    def _uses_bundled_engine(self) -> bool:
        """Whether the engine is the bundled app script (needs ``node`` prefix)."""
        return self.ENGINE == _APP_ENGINE or self.ENGINE.endswith(".cjs")

    def _base_cmd(self) -> list[str]:
        """Prefix with ``node`` when invoking the bundled .cjs engine."""
        if self._uses_bundled_engine():
            return ["node", self.ENGINE]
        return [self.ENGINE]

    def build_start_args(
        self,
        session_id: str,
        project_path: str,
        model: str | None = None,
        permission_mode: str | None = None,
        allowed_tools: list[str] | None = None,
        resume: bool = False,
    ) -> list[str]:
        """
        Build command-line arguments to start ZCode (persistent ``app-server``).

        The persistent mode keeps a single process alive for the whole agent
        session; the executor then drives it via the ZCode Protocol over stdio.
        ``--cwd`` and ``--mode`` set the workspace and permission posture.
        """
        args = self._base_cmd() + [
            "app-server",
            "--cwd",
            project_path,
        ]
        mode = self._map_permission_mode(permission_mode)
        args += ["--mode", mode]
        # NOTE: the CLI has no --model flag for headless/app-server modes (passing
        # it makes zcode print help and exit). The active model is selected via
        # ~/.zcode/cli/config.json (written by write_zcode_settings).
        return args

    def build_single_shot_args(
        self, prompt: str, project_path: str, model: str | None = None
    ) -> list[str]:
        """
        Build args for a single-shot prompt execution (autonomous runner).

        Emits machine-readable JSON (``--json``) with a ``usage`` block and a
        ``projection`` status. ``--mode yolo`` lets the agent run tools
        autonomously without confirmation prompts. The model is taken from
        ~/.zcode/cli/config.json (there is no headless --model flag).
        """
        args = self._base_cmd() + [
            "--cwd",
            project_path,
            "--prompt",
            prompt,
            "--mode",
            "yolo",
            "--json",
            "--no-color",
        ]
        return args

    def build_resume_args(
        self,
        session_id: str,
        project_path: str,
        prompt: str,
        model: str | None = None,
    ) -> list[str]:
        """
        Build args to resume a ZCode session in single-shot mode.

        This is the single-shot resume entry point used by the autonomous runner
        (one process per resumed turn); the persistent app-server path resumes
        via session/resume in ZCodeAppServerSession.start instead.

        ``--resume <sess_id>`` restores full conversation context; ``--prompt``
        is required (it drives the resumed turn).
        """
        args = self._base_cmd() + [
            "--cwd",
            project_path,
            "--resume",
            session_id,
            "--prompt",
            prompt,
            "--mode",
            "yolo",
            "--json",
            "--no-color",
        ]
        return args

    @staticmethod
    def _map_permission_mode(permission_mode: str | None) -> str:
        """Map Open ACE permission modes to ZCode modes.

        ZCode modes: ``build`` (ask before edits), ``edit`` (auto-edit),
        ``plan`` (read-only planning), ``yolo`` (fully autonomous).
        """
        mode_map = {
            "bypass": "yolo",
            "full-auto": "yolo",
            "auto": "build",
            "auto-edit": "edit",
            "plan": "plan",
        }
        return mode_map.get(permission_mode or "", "yolo")

    def provides_full_command(self) -> bool:
        """Return True; build_start_args returns a self-contained command.

        The command starts with ``node <engine.cjs>`` (the bundled engine is
        not on PATH), so callers must use the args verbatim rather than
        resolving an executable via shutil.which.
        """
        return True

    def supports_stdin_input(self) -> bool:
        """
        Return False; the tool does not use the stream-json stdin protocol.

        Persistent sessions are driven through ``app-server`` (a separate
        stdio protocol handled by ``ZCodeAppServerSession``), not the generic
        stdin pipe used for Claude/Qwen. Single-shot runs take the prompt on
        the command line.
        """
        return False

    def get_display_name(self) -> str:
        """Return the display name for this CLI tool."""
        return self.DISPLAY_NAME

    def get_executable_name(self) -> str:
        """Return the executable name."""
        return self.EXECUTABLE

    def get_settings_path(self) -> str:
        """Return the path to the ZCode config.json."""
        return os.path.expanduser("~/.zcode/cli/config.json")
