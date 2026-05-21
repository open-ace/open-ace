"""
Open ACE Remote Agent - Session Isolation Helpers

Provides utilities for creating isolated workspace directories for sessions
to prevent multi-session context mixing (Issue #491).

When multiple sessions run concurrently with the same project_path, CLI tools
like qwen-code and claude-code store their session data in the same directory:
- ~/.qwen/projects/<encoded-project-path>/chats/<session-id>.jsonl
- ~/.claude/projects/<encoded-project-path>/chats/<session-id>.jsonl

This causes session confusion when resuming, as CLI tools may load incorrect
session data.

Solution: Create a session-specific workspace directory for each session,
with symlinks to the original project's key files/directories.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Key project items to symlink into isolated workspace
# These ensure the session can work on the actual project code
ISOLATION_KEY_ITEMS = [
    # Version control
    ".git",
    ".gitignore",
    ".gitattributes",
    ".gitmodules",
    # Python project configuration
    ".editorconfig",
    ".pre-commit-config.yaml",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "pyvenv.cfg",
    ".python-version",
    # Node.js project configuration
    "package.json",
    "package-lock.json",
    "yarn.lock",
    ".npmrc",
    ".nvmrc",
    ".yarnrc",
    "tsconfig.json",
    "tsconfig.build.json",
    "jest.config.js",
    "vitest.config.ts",
    # Rust project configuration
    "Cargo.toml",
    "Cargo.lock",
    ".cargo",
    # Go project configuration
    "go.mod",
    "go.sum",
    # Java/Kotlin project configuration
    "build.gradle",
    "build.gradle.kts",
    "pom.xml",
    "settings.gradle",
    "gradle.properties",
    # Build tools
    "Makefile",
    "CMakeLists.txt",
    "meson.build",
    # Build/deployment
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env.example",
    ".envrc",
    "direnv.toml",
    # Configuration files
    ".config",
    "config",
    "configs",
    # Documentation
    "README.md",
    "README",
    "README.txt",
    "CHANGELOG.md",
    "CHANGELOG",
    "LICENSE",
    "LICENSE.md",
    "CONTRIBUTING.md",
    "docs",
    # IDE/Editor settings (shared across sessions)
    ".vscode",
    ".idea",
    # Source code directories (common patterns)
    "src",
    "lib",
    "libs",
    "app",
    "apps",
    "server",
    "client",
    "backend",
    "frontend",
    "web",
    "www",
    "api",
    "services",
    "modules",
    "components",
    "pages",
    "views",
    "models",
    "controllers",
    "routes",
    "scripts",
    "bin",
    "cmd",
    "internal",
    "pkg",
    # Test directories
    "tests",
    "test",
    "__tests__",
    "spec",
    "specs",
    "testing",
    # Assets
    "assets",
    "static",
    "public",
    "resources",
    # Data files
    "data",
    "examples",
    "samples",
]


def create_isolated_workspace(
    session_id: str,
    original_project_path: str,
) -> str:
    """
    Create an isolated workspace directory for a session.

    Args:
        session_id: Unique session identifier.
        original_project_path: The original project path requested by user.

    Returns:
        Path to the isolated workspace directory, or original path if creation fails.
    """
    original_path = Path(original_project_path).resolve()

    # Check write permission on original project directory
    try:
        test_file = original_path / ".openace-sessions-permission-test"
        test_file.touch()
        test_file.unlink()
    except (OSError, PermissionError) as e:
        logger.warning(
            "No write permission on project path for session %s: %s, "
            "falling back to original project path",
            session_id[:8],
            e,
        )
        return original_project_path

    # Create isolated workspace directory structure:
    # <original_project>/.openace-sessions/<session_id>/workspace/
    sessions_dir = original_path / ".openace-sessions"
    session_dir = sessions_dir / session_id
    workspace_dir = session_dir / "workspace"

    try:
        workspace_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Created isolated workspace for session %s: %s",
            session_id[:8],
            workspace_dir,
        )
    except OSError as e:
        logger.warning(
            "Failed to create isolated workspace for session %s: %s, "
            "falling back to original project path",
            session_id[:8],
            e,
        )
        return original_project_path

    # Create symlinks for key project items
    symlinked_count = 0
    for item_name in ISOLATION_KEY_ITEMS:
        src_item = original_path / item_name
        dst_item = workspace_dir / item_name

        if src_item.exists() and not dst_item.exists():
            try:
                # Use symlink on Unix; on Windows, may need fallback
                if os.name != "nt":
                    dst_item.symlink_to(src_item)
                else:
                    # Windows: try symlink (requires admin on older versions),
                    # fall back to copy if needed
                    try:
                        dst_item.symlink_to(src_item)
                    except OSError:
                        # Fallback: copy file/directory for Windows compatibility
                        if src_item.is_dir():
                            shutil.copytree(src_item, dst_item, symlinks=True)
                        else:
                            shutil.copy2(src_item, dst_item)
                symlinked_count += 1
                logger.debug(
                    "Linked %s -> %s for session %s",
                    dst_item,
                    src_item,
                    session_id[:8],
                )
            except OSError as e:
                logger.warning(
                    "Failed to link %s for session %s: %s",
                    item_name,
                    session_id[:8],
                    e,
                )

    # Create marker file to identify this as an isolated workspace
    marker_file = session_dir / ".openace-session-info"
    try:
        marker_info = {
            "session_id": session_id,
            "original_project_path": str(original_path),
            "workspace_type": "isolated",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "symlinked_items": symlinked_count,
        }
        marker_file.write_text(json.dumps(marker_info, indent=2), encoding="utf-8")
    except OSError:
        pass  # Non-critical

    return str(workspace_dir)


def cleanup_isolated_workspace(
    session_id: str,
    workspace_path: str,
) -> None:
    """
    Clean up the isolated workspace directory when session ends.

    Args:
        session_id: Session identifier.
        workspace_path: Path to the isolated workspace (or original path if not isolated).
    """
    # Only cleanup if this is an isolated workspace
    if ".openace-sessions" not in workspace_path:
        return

    try:
        workspace = Path(workspace_path)
        session_dir = workspace.parent  # workspace -> <session_id> dir
        marker_file = session_dir / ".openace-session-info"

        # Verify this is the correct session before cleanup
        if marker_file.exists():
            info = json.loads(marker_file.read_text(encoding="utf-8"))
            if info.get("session_id") != session_id:
                logger.warning(
                    "Session ID mismatch in workspace cleanup: expected %s, found %s",
                    session_id[:8],
                    info.get("session_id", "N/A")[:8],
                )
                return

        # Remove workspace contents (symlinks and files)
        if workspace.exists():
            for item in workspace.iterdir():
                try:
                    if item.is_symlink():
                        # Remove symlink only, not the target
                        item.unlink()
                    elif item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)
                except OSError as e:
                    logger.warning(
                        "Failed to remove %s in session %s cleanup: %s",
                        item,
                        session_id[:8],
                        e,
                    )
            try:
                workspace.rmdir()
            except OSError:
                pass

        # Remove session directory if empty
        try:
            if session_dir.exists() and not any(session_dir.iterdir()):
                session_dir.rmdir()
        except OSError:
            pass

        logger.info(
            "Cleaned up isolated workspace for session %s",
            session_id[:8],
        )
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(
            "Failed to cleanup isolated workspace for session %s: %s",
            session_id[:8],
            e,
        )


def get_original_project_path(workspace_path: str) -> str | None:
    """
    Get the original project path from an isolated workspace.

    Args:
        workspace_path: Path to the workspace (may be isolated or original).

    Returns:
        Original project path if isolated workspace, None otherwise.
    """
    if ".openace-sessions" not in workspace_path:
        return None

    try:
        workspace = Path(workspace_path)
        session_dir = workspace.parent
        marker_file = session_dir / ".openace-session-info"

        if marker_file.exists():
            info = json.loads(marker_file.read_text(encoding="utf-8"))
            return info.get("original_project_path")
    except (OSError, json.JSONDecodeError):
        pass

    return None
