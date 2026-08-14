#!/usr/bin/env python3
"""
openace-git — Security wrapper for git commands (Issue #2650).

This wrapper enforces a whitelist of allowed git subcommands and validates
parameters to prevent RCE attacks via -c alias.* and other dangerous patterns.

Usage: openace-git [--context-file <path>] [git args...]

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
import logging
import os
import re
import signal
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
WRAPPER_NAME = "openace-git"
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

# Known git global options and their argument counts
KNOWN_GLOBAL_OPTS: dict[str, int] = {
    "-c": 1,
    "-C": 1,
    "--git-dir": 1,
    "--work-tree": 1,
    "--namespace": 1,
    "--super-prefix": 1,
    "--exec-path": 1,
    "--man-path": 1,
    "--info-path": 1,
    "--paginate": 0,
    "--no-pager": 0,
    "--bare": 0,
    "--version": 0,
    "--help": 0,
    "-h": 0,
}

# Forbidden -c config prefixes (RCE vectors)
FORBIDDEN_C_PREFIXES = ["alias.", "core.hooksPath"]
ALLOWED_HOOKS_VALUES = ["", "/dev/null"]

# Config paths
DEFAULT_CONFIG_DIR = "/etc/openace"
CONFIG_FILE_NAME = "wrapper.yaml"
GIT_VERBS_FILE_NAME = "git-verbs.yaml"

# Runtime paths
DEFAULT_RUN_DIR = "/var/run/openace"
DEFAULT_LOG_DIR = "/var/log/openace"

# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class ParsedGitArgs:
    """Parsed git arguments structure."""

    global_opts: list[str] = field(default_factory=list)
    subcommand: str = ""
    subcommand_args: list[str] = field(default_factory=list)
    c_args: list[str] = field(default_factory=list)


@dataclass
class TrustedContext:
    """Trusted git context from caller."""

    git_dir: str = ""
    work_tree: str = ""
    common_dir: str = ""
    git_identity: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    pid: int = 0


@dataclass
class WrapperConfig:
    """Wrapper configuration."""

    log_path: str = ""
    error_log_path: str = ""
    log_level: str = "INFO"
    command_timeout: int = 120
    audit_buffer_size: int = 100
    audit_flush_interval: float = 1.0
    allowed_path_prefixes: list[str] = field(default_factory=lambda: ["", "/workspace", "/home"])
    max_arg_length: int = 4096
    max_c_args: int = 10
    context_expiry_seconds: int = 300
    orphan_cleanup_age_seconds: int = 600
    git_min_version: str = "2.30.0"
    git_max_version: str = "3.0.0"
    warn_on_untested: bool = True


@dataclass
class GitVerbsConfig:
    """Git verbs configuration."""

    allowed_verbs: list[dict[str, Any]] = field(default_factory=list)
    forbidden_verbs: list[str] = field(default_factory=list)


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
    verb: str,
    args_hash: str,
    context_file: str | None,
    result: str,
    duration_ms: int,
    exit_code: int,
    git_version: str,
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
        "verb": verb,
        "args_hash": args_hash,
        "context_file": context_file or "",
        "result": result,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "git_version": git_version,
    }
    logger.log(entry)


# ============================================================================
# Configuration Loading
# ============================================================================


def find_config_dir() -> str:
    """Find configuration directory."""
    # Check environment variable first
    env_config_dir = os.environ.get("OPENACE_CONFIG_DIR")
    if env_config_dir and os.path.isdir(env_config_dir):
        return env_config_dir

    # Check default locations
    for path in [DEFAULT_CONFIG_DIR, "/app/config/openace"]:
        if os.path.isdir(path):
            return path

    return DEFAULT_CONFIG_DIR


def parse_yaml_simple(content: str) -> dict[str, Any]:
    """Simple YAML parser for basic config files (fallback when yaml not available)."""
    result: dict[str, Any] = {}
    current_section = result
    current_key = ""

    for line in content.split("\n"):
        line = line.rstrip()

        # Skip empty lines and comments
        if not line or line.startswith("#"):
            continue

        # Count indentation
        indent = len(line) - len(line.lstrip())
        line = line.strip()

        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()

            if value == "":
                # New section
                current_section[key] = {}
                current_section = result  # Reset for nested parsing
            elif value.startswith("["):
                # List value
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

    # Parse general settings
    general = raw_config.get("general", {})
    config.log_path = general.get("log_path", "")
    config.error_log_path = general.get("error_log_path", "")
    config.log_level = general.get("log_level", "INFO")
    config.command_timeout = general.get("command_timeout", 120)
    config.audit_buffer_size = general.get("audit_buffer_size", 100)
    config.audit_flush_interval = general.get("audit_flush_interval", 1.0)

    # Parse security settings
    security = raw_config.get("security", {})
    prefixes = security.get("allowed_path_prefixes", ["", "/workspace", "/home"])
    config.allowed_path_prefixes = prefixes if isinstance(prefixes, list) else ["", "/workspace", "/home"]
    config.max_arg_length = security.get("max_arg_length", 4096)
    config.max_c_args = security.get("max_c_args", 10)
    config.context_expiry_seconds = security.get("context_expiry_seconds", 300)
    config.orphan_cleanup_age_seconds = security.get("orphan_cleanup_age_seconds", 600)

    # Parse version compatibility
    version_compat = raw_config.get("version_compat", {})
    config.git_min_version = version_compat.get("git_min_version", "2.30.0")
    config.git_max_version = version_compat.get("git_max_version", "3.0.0")
    config.warn_on_untested = version_compat.get("warn_on_untested", True)

    return config


def load_git_verbs_config(config_dir: str) -> GitVerbsConfig:
    """Load git verbs configuration."""
    config_path = os.path.join(config_dir, GIT_VERBS_FILE_NAME)
    raw_config = load_config_yaml(config_path)

    verbs_config = GitVerbsConfig()

    # Parse allowed verbs
    allowed = raw_config.get("allowed_verbs", [])
    verbs_config.allowed_verbs = allowed if isinstance(allowed, list) else []

    # Parse forbidden verbs
    forbidden = raw_config.get("forbidden_verbs", [])
    verbs_config.forbidden_verbs = forbidden if isinstance(forbidden, list) else []

    return verbs_config


# ============================================================================
# Argument Parsing
# ============================================================================


def parse_git_arguments(args: list[str]) -> ParsedGitArgs:
    """
    Parse git arguments using state machine.

    Git command format: git [global-options] [subcommand] [subcommand-options] [args]
    """
    result = ParsedGitArgs()
    i = 0

    while i < len(args):
        arg = args[i]

        # Handle --name=value format
        if "=" in arg:
            name = arg.split("=", 1)[0]
            if name in KNOWN_GLOBAL_OPTS or name.startswith("--"):
                result.global_opts.append(arg)
                if name == "-c" or name.startswith("-c"):
                    result.c_args.append(arg.split("=", 1)[1] if "=" in arg else "")
                i += 1
                continue

        # Handle known global options
        if arg in KNOWN_GLOBAL_OPTS:
            result.global_opts.append(arg)
            num_args = KNOWN_GLOBAL_OPTS[arg]

            for _ in range(num_args):
                i += 1
                if i < len(args):
                    result.global_opts.append(args[i])
                    if arg == "-c":
                        result.c_args.append(args[i])
            i += 1
            continue

        # Handle -c<key>=<value> format (no space)
        if arg.startswith("-c") and len(arg) > 2:
            result.global_opts.append(arg)
            if "=" in arg:
                result.c_args.append(arg[2:])
            i += 1
            continue

        # Handle --name value format (for options that take values)
        if arg.startswith("--") and arg in KNOWN_GLOBAL_OPTS:
            result.global_opts.append(arg)
            num_args = KNOWN_GLOBAL_OPTS[arg]
            for _ in range(num_args):
                i += 1
                if i < len(args):
                    result.global_opts.append(args[i])
                    if arg == "-c":
                        result.c_args.append(args[i])
            i += 1
            continue

        # First non-global option is the subcommand
        if not result.subcommand:
            result.subcommand = arg
        else:
            result.subcommand_args.append(arg)

        i += 1

    return result


def validate_c_arguments(c_args: list[str]) -> tuple[bool, str]:
    """
    Validate -c arguments for safety.

    Returns: (is_valid, error_message)
    """
    for c_arg in c_args:
        # Extract key and value
        if "=" in c_arg:
            key, value = c_arg.split("=", 1)
        else:
            continue  # Invalid format, let git handle it

        # Check for forbidden prefixes
        for forbidden in FORBIDDEN_C_PREFIXES:
            if key.startswith(forbidden):
                # Special case: allow core.hooksPath=/dev/null
                if key == "core.hooksPath" and value in ALLOWED_HOOKS_VALUES:
                    continue
                return False, f"Forbidden config key: {key}"

    return True, ""


def validate_flags(verb: str, args: list[str], verbs_config: GitVerbsConfig) -> tuple[bool, str]:
    """
    Validate that flags in args are allowed for the given verb.

    Returns: (is_valid, error_message)
    """
    # Find the verb configuration
    verb_config = None
    for allowed_verb in verbs_config.allowed_verbs:
        if isinstance(allowed_verb, dict) and allowed_verb.get("verb") == verb:
            verb_config = allowed_verb
            break

    if not verb_config:
        # Verb not in whitelist, will be caught by is_verb_allowed
        return True, ""

    # Get allowed flags
    allowed_flags = verb_config.get("allowed_flags", [])
    if not allowed_flags:
        # No restrictions defined for this verb
        return True, ""

    # Check each argument
    for arg in args:
        # Skip non-flag arguments (paths, branch names, etc.)
        if not arg.startswith("-"):
            continue

        # Check if flag is allowed
        is_allowed = False
        for allowed in allowed_flags:
            # Exact match
            if arg == allowed:
                is_allowed = True
                break
            # Prefix match for flags with values (e.g., --format=)
            if allowed.endswith("=") and arg.startswith(allowed):
                is_allowed = True
                break

        if not is_allowed:
            return False, f"Flag '{arg}' is not allowed for verb '{verb}'"

    return True, ""


def is_verb_allowed(verb: str, verbs_config: GitVerbsConfig) -> tuple[bool, str]:
    """
    Check if a verb is allowed.

    Returns: (is_allowed, reason)
    """
    # Check forbidden list first
    for forbidden in verbs_config.forbidden_verbs:
        if forbidden.startswith("!"):
            forbidden_name = forbidden[1:]
        else:
            forbidden_name = forbidden
        if verb == forbidden_name:
            return False, f"Verb '{verb}' is explicitly forbidden"

    # Check allowed list
    for allowed_verb in verbs_config.allowed_verbs:
        if isinstance(allowed_verb, dict) and allowed_verb.get("verb") == verb:
            return True, ""

    return False, f"Verb '{verb}' is not in whitelist"


def is_force_with_lease_allowed(subcommand_args: list[str], verbs_config: GitVerbsConfig) -> tuple[bool, str]:
    """
    Check if --force-with-lease is allowed for the given branch.

    Returns: (is_allowed, reason)
    """
    # Find the verb config
    push_config = None
    for verb in verbs_config.allowed_verbs:
        if isinstance(verb, dict) and verb.get("verb") == "push":
            push_config = verb
            break

    if not push_config:
        return False, "push verb not configured"

    # Check if --force-with-lease is in args
    has_force_with_lease = "--force-with-lease" in subcommand_args
    if not has_force_with_lease:
        return True, ""  # No force-with-lease, allowed

    # Get allowed branch patterns
    constraints = push_config.get("constraints", {})
    allowed_patterns = constraints.get("force_with_lease_branches", ["auto-dev/*"])

    # Find the branch name in args
    branch_name = ""
    for i, arg in enumerate(subcommand_args):
        if arg in ("origin", "-u", "--set-upstream"):
            continue
        if arg.startswith("--"):
            continue
        # This might be a branch name
        if not branch_name and not arg.startswith("-"):
            branch_name = arg
            break

    # Check if branch matches any allowed pattern
    for pattern in allowed_patterns:
        if fnmatch.fnmatch(branch_name, pattern):
            return True, ""

    return True, "force-with-lease allowed (pattern check passed)"


# ============================================================================
# Trusted Context Validation
# ============================================================================


def load_trusted_context(context_file: str) -> TrustedContext:
    """Load and validate trusted context from file."""
    context = TrustedContext()

    if not os.path.exists(context_file):
        return context

    try:
        with open(context_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        context.git_dir = data.get("git_dir", "")
        context.work_tree = data.get("work_tree", "")
        context.common_dir = data.get("common_dir", "")
        context.git_identity = data.get("git_identity", "")
        context.created_at = data.get("created_at", 0.0)
        context.expires_at = data.get("expires_at", 0.0)
        context.pid = data.get("pid", 0)
    except (OSError, json.JSONDecodeError):
        pass

    return context


def validate_trusted_context(context: TrustedContext, max_age_seconds: int) -> tuple[bool, str]:
    """
    Validate trusted context.

    Returns: (is_valid, error_message)
    """
    # Check expiration
    if context.expires_at > 0 and time.time() > context.expires_at:
        return False, "Context has expired"

    # Check if context is too old
    if context.created_at > 0:
        age = time.time() - context.created_at
        if age > max_age_seconds:
            return False, f"Context too old ({age:.0f}s)"

    return True, ""


def get_path_identity(path: str) -> str:
    """Get device:inode identity for a path."""
    try:
        stat_result = os.stat(path)
        return f"{stat_result.st_dev}:{stat_result.st_ino}"
    except OSError:
        return ""


# ============================================================================
# Path Validation
# ============================================================================


def validate_path(path: str, allowed_prefixes: list[str]) -> tuple[bool, str]:
    """
    Validate that path is under allowed prefixes.

    Returns: (is_valid, error_message)
    """
    if not path:
        return True, ""

    # Resolve path
    try:
        resolved = os.path.realpath(path)
    except OSError:
        return False, f"Cannot resolve path: {path}"

    # Check against allowed prefixes
    for prefix in allowed_prefixes:
        if not prefix:
            continue
        prefix_resolved = os.path.realpath(prefix)
        if resolved.startswith(prefix_resolved + os.sep) or resolved == prefix_resolved:
            return True, ""

    return False, f"Path '{path}' is outside allowed directories"


def validate_paths_in_args(
    parsed_args: ParsedGitArgs, allowed_prefixes: list[str]
) -> tuple[bool, str]:
    """
    Validate paths in git arguments.

    Returns: (is_valid, error_message)
    """
    # Check -C path
    for i, opt in enumerate(parsed_args.global_opts):
        if opt == "-C" and i + 1 < len(parsed_args.global_opts):
            path = parsed_args.global_opts[i + 1]
            is_valid, error = validate_path(path, allowed_prefixes)
            if not is_valid:
                return False, error

    # Check --git-dir and --work-tree
    for opt in parsed_args.global_opts:
        if opt.startswith("--git-dir="):
            path = opt.split("=", 1)[1]
            is_valid, error = validate_path(path, allowed_prefixes)
            if not is_valid:
                return False, error
        elif opt.startswith("--work-tree="):
            path = opt.split("=", 1)[1]
            is_valid, error = validate_path(path, allowed_prefixes)
            if not is_valid:
                return False, error

    return True, ""


# ============================================================================
# Orphan File Cleanup
# ============================================================================


def cleanup_orphan_files(run_dir: str, max_age_seconds: int) -> int:
    """
    Clean up orphan context and lock files.

    Returns: number of files cleaned up
    """
    if not os.path.exists(run_dir):
        return 0

    cleaned = 0
    now = time.time()

    try:
        for filename in os.listdir(run_dir):
            if not (
                filename.startswith("trusted-context-")
                or filename.startswith("wrapper-lock-")
            ):
                continue

            filepath = os.path.join(run_dir, filename)
            try:
                mtime = os.path.getmtime(filepath)
                if now - mtime > max_age_seconds:
                    os.unlink(filepath)
                    cleaned += 1
            except OSError:
                pass
    except OSError:
        pass

    return cleaned


# ============================================================================
# Version Checking
# ============================================================================


def parse_version(version_str: str) -> tuple[int, ...]:
    """Parse version string into tuple of integers."""
    # Extract version numbers (e.g., "git version 2.43.0" -> "2.43.0")
    match = re.search(r"(\d+\.\d+\.\d+)", version_str)
    if match:
        version = match.group(1)
        return tuple(int(x) for x in version.split("."))

    # Fallback: extract any numbers
    parts = re.findall(r"\d+", version_str)
    return tuple(int(x) for x in parts) if parts else (0, 0, 0)


def check_version_compatibility(
    tool: str, min_version: str, max_version: str, warn_on_untested: bool
) -> tuple[bool, str]:
    """
    Check git version compatibility.

    Returns: (is_compatible, message)
    """
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

    # Check git version
    is_compatible, message = check_version_compatibility(
        "git", config.git_min_version, config.git_max_version, config.warn_on_untested
    )
    print(f"  git version: {message}")
    if not is_compatible:
        errors.append("git version incompatible")

    # Check configuration
    config_dir = find_config_dir()
    print(f"  config directory: {config_dir}")
    if not os.path.isdir(config_dir):
        errors.append(f"Config directory not found: {config_dir}")

    # Check wrapper config
    wrapper_config_path = os.path.join(config_dir, CONFIG_FILE_NAME)
    if os.path.exists(wrapper_config_path):
        print(f"  wrapper config: {wrapper_config_path} (OK)")
    else:
        print(f"  wrapper config: {wrapper_config_path} (NOT FOUND - using defaults)")

    # Check git verbs config
    git_verbs_path = os.path.join(config_dir, GIT_VERBS_FILE_NAME)
    if os.path.exists(git_verbs_path):
        print(f"  git verbs config: {git_verbs_path} (OK)")
    else:
        print(f"  git verbs config: {git_verbs_path} (NOT FOUND - using defaults)")

    # Check run directory
    run_dir = os.environ.get("OPENACE_RUN_DIR", DEFAULT_RUN_DIR)
    print(f"  run directory: {run_dir}")
    if not os.path.exists(run_dir):
        print(f"    (will be created on demand)")

    # Check log directory
    log_dir = os.path.dirname(config.log_path) if config.log_path else DEFAULT_LOG_DIR
    print(f"  log directory: {log_dir}")
    if config.log_path and not os.path.exists(log_dir):
        print(f"    (will be created on demand)")

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


def get_git_version() -> str:
    """Get git version string."""
    try:
        result = subprocess.run(
            ["git", "--version"],
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
        description="openace-git security wrapper",
        add_help=False,  # Don't interfere with git's own --help
    )
    parser.add_argument("--context-file", help="Path to trusted context file")
    parser.add_argument("--self-check", action="store_true", help="Run self-check")
    parser.add_argument("git_args", nargs="*", help="Git arguments")

    try:
        wrapper_args, remaining = parser.parse_known_args()
    except SystemExit:
        return EXIT_USAGE

    # Load configuration
    config_dir = find_config_dir()
    config = load_wrapper_config(config_dir)
    verbs_config = load_git_verbs_config(config_dir)

    # Self-check mode
    if wrapper_args.self_check:
        return run_self_check(config)

    # Cleanup orphan files
    run_dir = os.environ.get("OPENACE_RUN_DIR", DEFAULT_RUN_DIR)
    cleanup_orphan_files(run_dir, config.orphan_cleanup_age_seconds)

    # Determine git arguments
    git_args = wrapper_args.git_args + remaining
    if not git_args:
        print("Usage: openace-git [--context-file <path>] [git args...]", file=sys.stderr)
        return EXIT_USAGE

    # Validate argument length
    for arg in git_args:
        if len(arg) > config.max_arg_length:
            print(f"Argument too long (max {config.max_arg_length})", file=sys.stderr)
            return EXIT_USAGE

    # Parse git arguments
    parsed_args = parse_git_arguments(git_args)

    # Check for --version or --help (allow without subcommand)
    if "--version" in parsed_args.global_opts or "--help" in parsed_args.global_opts:
        # Pass through to git
        result = subprocess.run(["git"] + git_args)
        return result.returncode

    # Validate we have a subcommand
    if not parsed_args.subcommand:
        print("No git subcommand specified", file=sys.stderr)
        return EXIT_USAGE

    # Check version compatibility
    is_compatible, version_message = check_version_compatibility(
        "git", config.git_min_version, config.git_max_version, config.warn_on_untested
    )
    if not is_compatible:
        print(f"git version incompatible: {version_message}", file=sys.stderr)
        return EXIT_VERSION_INCOMPATIBLE
    if "WARNING" in version_message:
        print(f"WARNING: {version_message}", file=sys.stderr)

    # Validate -c arguments
    is_valid, error = validate_c_arguments(parsed_args.c_args)
    if not is_valid:
        print(f"Security violation: {error}", file=sys.stderr)
        return EXIT_PERMISSION_DENIED

    # Check max -c arguments
    if len(parsed_args.c_args) > config.max_c_args:
        print(f"Too many -c arguments (max {config.max_c_args})", file=sys.stderr)
        return EXIT_USAGE

    # Validate subcommand is allowed
    is_allowed, reason = is_verb_allowed(parsed_args.subcommand, verbs_config)
    if not is_allowed:
        print(f"Permission denied: {reason}", file=sys.stderr)
        return EXIT_PERMISSION_DENIED

    # Validate flags are allowed for this verb (Issue #2650)
    is_valid, error = validate_flags(parsed_args.subcommand, parsed_args.subcommand_args, verbs_config)
    if not is_valid:
        print(f"Permission denied: {error}", file=sys.stderr)
        return EXIT_PERMISSION_DENIED

    # Check --force-with-lease constraints for push
    if parsed_args.subcommand == "push":
        is_allowed, _ = is_force_with_lease_allowed(parsed_args.subcommand_args, verbs_config)
        if not is_allowed:
            print("Permission denied: --force-with-lease not allowed for this branch", file=sys.stderr)
            return EXIT_PERMISSION_DENIED

    # Load and validate trusted context if provided
    context = TrustedContext()
    if wrapper_args.context_file:
        context = load_trusted_context(wrapper_args.context_file)
        is_valid, error = validate_trusted_context(context, config.context_expiry_seconds)
        if not is_valid:
            print(f"Context validation failed: {error}", file=sys.stderr)
            return EXIT_PATH_VALIDATION

    # Validate paths in arguments
    is_valid, error = validate_paths_in_args(parsed_args, config.allowed_path_prefixes)
    if not is_valid:
        print(f"Path validation failed: {error}", file=sys.stderr)
        return EXIT_PATH_VALIDATION

    # Execute git command
    actor = get_current_user()
    target_user = None  # Not available in wrapper context
    args_hash = compute_args_hash(git_args)
    # Note: cwd intentionally not logged to avoid information leakage in multi-user environments
    git_version = get_git_version()

    start_time = time.time()

    try:
        result = subprocess.run(
            ["git"] + git_args,
            timeout=config.command_timeout,
        )
        duration_ms = int((time.time() - start_time) * 1000)

        # Log audit
        log_audit(
            event="git_exec",
            actor=actor,
            target_user=target_user,
            wrapper=WRAPPER_NAME,
            verb=parsed_args.subcommand,
            args_hash=args_hash,
            context_file=wrapper_args.context_file,
            result="success" if result.returncode == 0 else "failure",
            duration_ms=duration_ms,
            exit_code=result.returncode,
            git_version=git_version,
            config=config,
        )

        # Flush audit logger
        if _audit_logger:
            _audit_logger.flush()

        return result.returncode if result.returncode != 0 else EXIT_SUCCESS

    except subprocess.TimeoutExpired:
        duration_ms = int((time.time() - start_time) * 1000)
        log_audit(
            event="git_timeout",
            actor=actor,
            target_user=target_user,
            wrapper=WRAPPER_NAME,
            verb=parsed_args.subcommand,
            args_hash=args_hash,
            context_file=wrapper_args.context_file,
            result="timeout",
            duration_ms=duration_ms,
            exit_code=EXIT_TIMEOUT,
            git_version=git_version,
            config=config,
        )
        print(f"Command timed out after {config.command_timeout}s", file=sys.stderr)
        return EXIT_TIMEOUT

    except FileNotFoundError:
        print("git binary not found", file=sys.stderr)
        return EXIT_COMMAND_FAILED


if __name__ == "__main__":
    sys.exit(main())