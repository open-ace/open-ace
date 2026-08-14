#!/usr/bin/env python3
"""
openace-gh — Security wrapper for GitHub CLI commands (Issue #2650).

This wrapper enforces a whitelist of allowed gh subcommands and validates
API paths to prevent unauthorized operations.

Usage: openace-gh [--context-file <path>] [gh args...]

Exit codes:
    0  - Success
    64 - Usage error
    65 - Permission denied (command not in whitelist)
    66 - Configuration error
    67 - Path validation failed
    68 - Audit log error
    69 - Command execution failed
    70 - Timeout
    71 - Version incompatible
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Try to import yaml, fall back to basic parsing if not available
try:
    import yaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

# ============================================================================
# Constants
# ============================================================================

WRAPPER_VERSION = "1.0.0"
WRAPPER_NAME = "openace-gh"
ISSUE_REF = "#2650"

# Exit codes (consistent with openace-run-as)
EXIT_SUCCESS = 0
EXIT_USAGE = 64
EXIT_PERMISSION_DENIED = 65
EXIT_CONFIG_ERROR = 66
EXIT_PATH_VALIDATION = 67
EXIT_AUDIT_ERROR = 68
EXIT_COMMAND_FAILED = 69
EXIT_TIMEOUT = 70
EXIT_VERSION_INCOMPATIBLE = 71

# Config paths
DEFAULT_CONFIG_DIR = "/etc/openace"
CONFIG_FILE_NAME = "wrapper.yaml"
GH_COMMANDS_FILE_NAME = "gh-commands.yaml"
GH_API_PATHS_FILE_NAME = "gh-api-paths.yaml"

# Runtime paths
DEFAULT_RUN_DIR = "/var/run/openace"
DEFAULT_LOG_DIR = "/var/log/openace"


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class ParsedGhArgs:
    """Parsed gh arguments structure."""

    repo_arg: str = ""  # -R owner/repo
    command: str = ""
    subcommand: str = ""
    args: list[str] = field(default_factory=list)


@dataclass
class WrapperConfig:
    """Wrapper configuration."""

    log_path: str = ""
    error_log_path: str = ""
    log_level: str = "INFO"
    command_timeout: int = 120
    audit_buffer_size: int = 100
    audit_flush_interval: float = 1.0
    gh_min_version: str = "2.20.0"
    gh_max_version: str = "3.0.0"
    warn_on_untested: bool = True


@dataclass
class GhCommandsConfig:
    """gh commands configuration."""

    allowed_commands: list[dict[str, Any]] = field(default_factory=list)
    forbidden_commands: list[str] = field(default_factory=list)


@dataclass
class GhApiPathsConfig:
    """gh API paths configuration."""

    allowed_paths: list[str] = field(default_factory=list)
    forbidden_methods: list[str] = field(default_factory=list)


# ============================================================================
# Audit Logger (Thread-safe async)
# ============================================================================


class AsyncAuditLogger:
    """Async audit logger with batch writing."""

    def __init__(self, log_path: str, buffer_size: int = 100, flush_interval: float = 1.0):
        self.log_path = log_path
        self.buffer_size = buffer_size
        self.flush_interval = flush_interval
        self.buffer: list[str] = []
        self.last_flush = time.time()
        self._lock = threading.Lock()
        self._initialized = False

        # Try to create log directory
        try:
            log_dir = os.path.dirname(log_path)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, mode=0o755, exist_ok=True)
            self._initialized = True
        except OSError:
            pass

    def log(self, entry: dict[str, Any]) -> None:
        """Add log entry to buffer."""
        if not self._initialized:
            return

        try:
            log_line = json.dumps(entry, separators=(",", ":"))
            with self._lock:
                self.buffer.append(log_line)
                if (
                    len(self.buffer) >= self.buffer_size
                    or time.time() - self.last_flush >= self.flush_interval
                ):
                    self._flush_unlocked()
        except Exception:
            pass

    def _flush_unlocked(self) -> None:
        """Flush buffer to file (must hold lock)."""
        if not self.buffer:
            return
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write("\n".join(self.buffer) + "\n")
            self.buffer.clear()
            self.last_flush = time.time()
        except OSError:
            pass

    def flush(self) -> None:
        """Force flush buffer."""
        with self._lock:
            self._flush_unlocked()


# Global audit logger instance
_audit_logger: AsyncAuditLogger | None = None


def get_audit_logger(config: WrapperConfig) -> AsyncAuditLogger:
    """Get or create global audit logger."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AsyncAuditLogger(
            config.log_path or os.path.join(DEFAULT_LOG_DIR, "wrapper-audit.log"),
            buffer_size=config.audit_buffer_size,
            flush_interval=config.audit_flush_interval,
        )
    return _audit_logger


def log_audit(
    event: str,
    actor: str,
    target_user: str | None,
    wrapper: str,
    command: str,
    args_hash: str,
    context_file: str | None,
    result: str,
    duration_ms: int,
    exit_code: int,
    gh_version: str,
    config: WrapperConfig,
) -> None:
    """Write audit log entry."""
    logger = get_audit_logger(config)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor": actor,
        "target_user": target_user or "",
        "wrapper": wrapper,
        "wrapper_version": WRAPPER_VERSION,
        "command": command,
        "args_hash": args_hash,
        "context_file": context_file or "",
        "result": result,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "gh_version": gh_version,
    }
    logger.log(entry)


# ============================================================================
# Configuration Loading
# ============================================================================


def find_config_dir() -> str:
    """Find configuration directory."""
    env_config_dir = os.environ.get("OPENACE_CONFIG_DIR")
    if env_config_dir and os.path.isdir(env_config_dir):
        return env_config_dir

    for path in [DEFAULT_CONFIG_DIR, "/app/config/openace"]:
        if os.path.isdir(path):
            return path

    return DEFAULT_CONFIG_DIR


def parse_yaml_simple(content: str) -> dict[str, Any]:
    """Simple YAML parser for basic config files."""
    result: dict[str, Any] = {}
    current_section = result

    for line in content.split("\n"):
        line = line.rstrip()

        if not line or line.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip())
        line = line.strip()

        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if value == "":
                current_section[key] = {}
            elif value.startswith("["):
                items = []
                for item in value[1:-1].split(","):
                    item = item.strip().strip("\"'")
                    if item:
                        items.append(item)
                current_section[key] = items
            elif value.isdigit():
                current_section[key] = int(value)
            elif value.replace(".", "").isdigit():
                current_section[key] = float(value)
            elif value.lower() in ("true", "false"):
                current_section[key] = value.lower() == "true"
            elif value.startswith('"') and value.endswith('"'):
                current_section[key] = value[1:-1]
            elif value.startswith("'") and value.endswith("'"):
                current_section[key] = value[1:-1]
            else:
                current_section[key] = value

    return result


def load_config_yaml(config_path: str) -> dict[str, Any]:
    """Load YAML configuration file."""
    if not os.path.exists(config_path):
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()

        if YAML_AVAILABLE:
            return yaml.safe_load(content) or {}
        else:
            return parse_yaml_simple(content)
    except Exception:
        return {}


def load_wrapper_config(config_dir: str) -> WrapperConfig:
    """Load main wrapper configuration."""
    config_path = os.path.join(config_dir, CONFIG_FILE_NAME)
    raw_config = load_config_yaml(config_path)

    config = WrapperConfig()

    general = raw_config.get("general", {})
    config.log_path = general.get("log_path", "")
    config.error_log_path = general.get("error_log_path", "")
    config.log_level = general.get("log_level", "INFO")
    config.command_timeout = general.get("command_timeout", 120)
    config.audit_buffer_size = general.get("audit_buffer_size", 100)
    config.audit_flush_interval = general.get("audit_flush_interval", 1.0)

    version_compat = raw_config.get("version_compat", {})
    config.gh_min_version = version_compat.get("gh_min_version", "2.20.0")
    config.gh_max_version = version_compat.get("gh_max_version", "3.0.0")
    config.warn_on_untested = version_compat.get("warn_on_untested", True)

    return config


def load_gh_commands_config(config_dir: str) -> GhCommandsConfig:
    """Load gh commands configuration."""
    config_path = os.path.join(config_dir, GH_COMMANDS_FILE_NAME)
    raw_config = load_config_yaml(config_path)

    commands_config = GhCommandsConfig()

    allowed = raw_config.get("allowed_commands", [])
    commands_config.allowed_commands = allowed if isinstance(allowed, list) else []

    forbidden = raw_config.get("forbidden_commands", [])
    commands_config.forbidden_commands = forbidden if isinstance(forbidden, list) else []

    return commands_config


def load_gh_api_paths_config(config_dir: str) -> GhApiPathsConfig:
    """Load gh API paths configuration."""
    config_path = os.path.join(config_dir, GH_API_PATHS_FILE_NAME)
    raw_config = load_config_yaml(config_path)

    api_config = GhApiPathsConfig()

    allowed = raw_config.get("allowed_paths", [])
    api_config.allowed_paths = allowed if isinstance(allowed, list) else []

    forbidden = raw_config.get("forbidden_methods", [])
    api_config.forbidden_methods = forbidden if isinstance(forbidden, list) else []

    return api_config


# ============================================================================
# Argument Parsing
# ============================================================================


def parse_gh_arguments(args: list[str]) -> ParsedGhArgs:
    """
    Parse gh arguments.

    gh command format: gh [-R owner/repo] <command> [<subcommand>] [args...]
    """
    result = ParsedGhArgs()
    i = 0

    while i < len(args):
        arg = args[i]

        # Handle -R flag
        if arg == "-R" and i + 1 < len(args):
            i += 1
            result.repo_arg = args[i]
            i += 1
            continue

        # Handle --repo flag
        if arg.startswith("--repo="):
            result.repo_arg = arg.split("=", 1)[1]
            i += 1
            continue

        # First non-flag argument is the command
        if not arg.startswith("-"):
            if not result.command:
                result.command = arg
            elif not result.subcommand:
                # Second argument might be a subcommand
                result.subcommand = arg
            else:
                result.args.append(arg)
        else:
            result.args.append(arg)

        i += 1

    return result


def is_command_allowed(
    command: str, subcommand: str | None, commands_config: GhCommandsConfig
) -> tuple[bool, str]:
    """
    Check if a command is allowed.

    Returns: (is_allowed, reason)
    """
    full_command = f"{command} {subcommand}" if subcommand else command

    # Check forbidden list
    for forbidden in commands_config.forbidden_commands:
        if forbidden.startswith("!"):
            forbidden_name = forbidden[1:]
        else:
            forbidden_name = forbidden
        if full_command == forbidden_name or command == forbidden_name:
            return False, f"Command '{full_command}' is explicitly forbidden"

    # Check allowed list
    for allowed_cmd in commands_config.allowed_commands:
        if isinstance(allowed_cmd, dict) and allowed_cmd.get("command") == command:
            # Check if subcommand is required and allowed
            subcommands = allowed_cmd.get("subcommands", [])
            if subcommands:
                if subcommand and subcommand in subcommands:
                    return True, ""
                elif not subcommand:
                    return False, f"Command '{command}' requires a subcommand"
            else:
                # No subcommand required
                return True, ""

    return False, f"Command '{full_command}' is not in whitelist"


def is_admin_merge_allowed(commands_config: GhCommandsConfig) -> bool:
    """Check if --admin merge is allowed."""
    for cmd in commands_config.allowed_commands:
        if isinstance(cmd, dict) and cmd.get("command") == "pr":
            admin_merge = cmd.get("admin_merge", {})
            if admin_merge.get("enabled", False):
                return True
            env_var = admin_merge.get("env_var", "")
            if env_var and os.environ.get(env_var) == "1":
                return True
    return False


def match_api_path(path: str, patterns: list[str]) -> bool:
    """Check if API path matches any pattern."""
    for pattern in patterns:
        # Convert pattern to regex
        # * matches single segment (no /)
        regex_pattern = "^" + pattern.replace("/", r"\/").replace("*", r"[^\/]+") + "$"
        if re.match(regex_pattern, path):
            return True
    return False


def is_api_path_allowed(
    api_path: str, method: str | None, api_config: GhApiPathsConfig
) -> tuple[bool, str]:
    """
    Check if API path and method are allowed.

    Returns: (is_allowed, reason)
    """
    # Check forbidden methods
    if method and method.upper() in api_config.forbidden_methods:
        return False, f"HTTP method '{method}' is forbidden"

    # Check if path matches allowed patterns
    if match_api_path(api_path, api_config.allowed_paths):
        return True, ""

    return False, f"API path '{api_path}' is not whitelisted"


def extract_api_args(args: list[str]) -> tuple[str, str | None]:
    """Extract API path and method from args."""
    api_path = ""
    method = None

    i = 0
    while i < len(args):
        arg = args[i]

        if arg == "-X" and i + 1 < len(args):
            i += 1
            method = args[i].upper()
        elif arg.startswith("--method="):
            method = arg.split("=", 1)[1].upper()
        elif not arg.startswith("-") and not api_path:
            api_path = arg

        i += 1

    return api_path, method


# ============================================================================
# Version Checking
# ============================================================================


def parse_version(version_str: str) -> tuple[int, ...]:
    """Parse version string into tuple of integers."""
    match = re.search(r"(\d+\.\d+\.\d+)", version_str)
    if match:
        version = match.group(1)
        return tuple(int(x) for x in version.split("."))

    parts = re.findall(r"\d+", version_str)
    return tuple(int(x) for x in parts) if parts else (0, 0, 0)


def check_version_compatibility(
    tool: str, min_version: str, max_version: str, warn_on_untested: bool
) -> tuple[bool, str]:
    """Check gh version compatibility."""
    try:
        result = subprocess.run(
            [tool, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return False, f"Cannot get {tool} version"

        version_str = result.stdout.strip()
        version = parse_version(version_str)
        min_v = parse_version(min_version)
        max_v = parse_version(max_version)

        if version < min_v:
            return False, f"{tool} version {version_str} too old (min: {min_version})"

        if version > max_v and warn_on_untested:
            return True, f"WARNING: untested {tool} version {version_str}"

        return True, f"OK ({version_str})"

    except subprocess.TimeoutExpired:
        return False, f"Timeout getting {tool} version"
    except FileNotFoundError:
        return False, f"{tool} binary not found"


# ============================================================================
# Self-Check Mode
# ============================================================================


def run_self_check(config: WrapperConfig) -> int:
    """Run self-check and report status."""
    print(f"{WRAPPER_NAME} v{WRAPPER_VERSION} self-check")
    print(f"Issue: {ISSUE_REF}")
    print()

    errors = []

    # Check gh version
    is_compatible, message = check_version_compatibility(
        "gh", config.gh_min_version, config.gh_max_version, config.warn_on_untested
    )
    print(f"  gh version: {message}")
    if not is_compatible:
        errors.append("gh version incompatible")

    # Check configuration
    config_dir = find_config_dir()
    print(f"  config directory: {config_dir}")
    if not os.path.isdir(config_dir):
        errors.append(f"Config directory not found: {config_dir}")

    # Check config files
    wrapper_config_path = os.path.join(config_dir, CONFIG_FILE_NAME)
    if os.path.exists(wrapper_config_path):
        print(f"  wrapper config: {wrapper_config_path} (OK)")
    else:
        print(f"  wrapper config: {wrapper_config_path} (NOT FOUND - using defaults)")

    gh_commands_path = os.path.join(config_dir, GH_COMMANDS_FILE_NAME)
    if os.path.exists(gh_commands_path):
        print(f"  gh commands config: {gh_commands_path} (OK)")
    else:
        print(f"  gh commands config: {gh_commands_path} (NOT FOUND - using defaults)")

    gh_api_paths_path = os.path.join(config_dir, GH_API_PATHS_FILE_NAME)
    if os.path.exists(gh_api_paths_path):
        print(f"  gh api paths config: {gh_api_paths_path} (OK)")
    else:
        print(f"  gh api paths config: {gh_api_paths_path} (NOT FOUND - using defaults)")

    print()
    if errors:
        print("Self-check FAILED:")
        for error in errors:
            print(f"  - {error}")
        return EXIT_CONFIG_ERROR

    print("Self-check PASSED")
    return EXIT_SUCCESS


# ============================================================================
# Main Entry Point
# ============================================================================


def compute_args_hash(args: list[str]) -> str:
    """Compute hash of arguments for audit logging."""
    args_str = " ".join(args)
    return hashlib.sha256(args_str.encode()).hexdigest()[:16]


def get_current_user() -> str:
    """Get current username."""
    try:
        import pwd

        return pwd.getpwuid(os.getuid()).pw_name
    except Exception:
        return str(os.getuid())


def get_gh_version() -> str:
    """Get gh version string."""
    try:
        result = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


def main() -> int:
    """Main entry point."""
    # Parse wrapper-specific arguments
    parser = argparse.ArgumentParser(
        description="openace-gh security wrapper",
        add_help=False,
    )
    parser.add_argument("--context-file", help="Path to trusted context file")
    parser.add_argument("--self-check", action="store_true", help="Run self-check")
    parser.add_argument("gh_args", nargs="*", help="gh arguments")

    try:
        wrapper_args, remaining = parser.parse_known_args()
    except SystemExit:
        return EXIT_USAGE

    # Load configuration
    config_dir = find_config_dir()
    config = load_wrapper_config(config_dir)
    commands_config = load_gh_commands_config(config_dir)
    api_config = load_gh_api_paths_config(config_dir)

    # Self-check mode
    if wrapper_args.self_check:
        return run_self_check(config)

    # Determine gh arguments
    gh_args = wrapper_args.gh_args + remaining
    if not gh_args:
        print("Usage: openace-gh [--context-file <path>] [gh args...]", file=sys.stderr)
        return EXIT_USAGE

    # Parse gh arguments
    parsed_args = parse_gh_arguments(gh_args)

    # Check for --version or --help
    if "--version" in gh_args or "--help" in gh_args:
        result = subprocess.run(["gh"] + gh_args)
        return result.returncode

    # Validate we have a command
    if not parsed_args.command:
        print("No gh command specified", file=sys.stderr)
        return EXIT_USAGE

    # Check version compatibility
    is_compatible, version_message = check_version_compatibility(
        "gh", config.gh_min_version, config.gh_max_version, config.warn_on_untested
    )
    if not is_compatible:
        print(f"gh version incompatible: {version_message}", file=sys.stderr)
        return EXIT_VERSION_INCOMPATIBLE
    if "WARNING" in version_message:
        print(f"WARNING: {version_message}", file=sys.stderr)

    # Validate command is allowed
    is_allowed, reason = is_command_allowed(
        parsed_args.command, parsed_args.subcommand, commands_config
    )
    if not is_allowed:
        print(f"Permission denied: {reason}", file=sys.stderr)
        return EXIT_PERMISSION_DENIED

    # Special handling for 'api' command
    if parsed_args.command == "api":
        api_path, method = extract_api_args(parsed_args.args)
        if api_path:
            is_allowed, reason = is_api_path_allowed(api_path, method, api_config)
            if not is_allowed:
                print(f"Permission denied: {reason}", file=sys.stderr)
                return EXIT_PERMISSION_DENIED

    # Check for --admin merge flag
    if parsed_args.command == "pr" and parsed_args.subcommand == "merge":
        if "--admin" in parsed_args.args:
            if not is_admin_merge_allowed(commands_config):
                print(
                    "Permission denied: --admin requires OPENACE_ALLOW_ADMIN_MERGE=1",
                    file=sys.stderr,
                )
                return EXIT_PERMISSION_DENIED

    # Execute gh command
    actor = get_current_user()
    target_user = None
    args_hash = compute_args_hash(gh_args)
    # Note: cwd intentionally not logged to avoid information leakage in multi-user environments
    gh_version = get_gh_version()

    start_time = time.time()

    try:
        result = subprocess.run(
            ["gh"] + gh_args,
            timeout=config.command_timeout,
        )
        duration_ms = int((time.time() - start_time) * 1000)

        # Log audit
        full_command = f"{parsed_args.command} {parsed_args.subcommand}" if parsed_args.subcommand else parsed_args.command
        log_audit(
            event="gh_exec",
            actor=actor,
            target_user=target_user,
            wrapper=WRAPPER_NAME,
            command=full_command,
            args_hash=args_hash,
            context_file=wrapper_args.context_file,
            result="success" if result.returncode == 0 else "failure",
            duration_ms=duration_ms,
            exit_code=result.returncode,
            gh_version=gh_version,
            config=config,
        )

        # Flush audit logger
        if _audit_logger:
            _audit_logger.flush()

        return result.returncode if result.returncode != 0 else EXIT_SUCCESS

    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start_time) * 1000)
        log_audit(
            event="gh_timeout",
            actor=actor,
            target_user=target_user,
            wrapper=WRAPPER_NAME,
            command=parsed_args.command,
            args_hash=args_hash,
            context_file=wrapper_args.context_file,
            result="timeout",
            duration_ms=duration_ms,
            exit_code=EXIT_TIMEOUT,
            gh_version=gh_version,
            config=config,
        )
        print(f"Command timed out after {config.command_timeout}s", file=sys.stderr)
        return EXIT_TIMEOUT

    except FileNotFoundError:
        print("gh binary not found", file=sys.stderr)
        return EXIT_COMMAND_FAILED


if __name__ == "__main__":
    sys.exit(main())