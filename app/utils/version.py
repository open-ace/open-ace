"""
Open ACE - Version Utilities

Version and git information utilities.
"""

import os
import subprocess


def get_git_commit() -> str:
    """
    Get the current git commit hash and date for version display.

    Returns:
        str: Format "commit_hash (MM-DD HH:MM:SS)" or "unknown" if version info unavailable.
    """
    version_file = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "VERSION"
    )

    # Try to get version from git (for development environments)
    try:
        # Get commit hash
        hash_result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
        # Get commit date in MM-DD HH:MM:SS format
        date_result = subprocess.run(
            ["git", "log", "-1", "--format=%cd", "--date=format:%m-%d %H:%M:%S"],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
        if hash_result.returncode == 0 and date_result.returncode == 0:
            commit_hash = hash_result.stdout.strip()
            commit_date = date_result.stdout.strip()
            version = f"{commit_hash} ({commit_date})"

            # Auto-update VERSION file in development environment
            try:
                with open(version_file, "w") as f:
                    f.write(version)
            except Exception:
                pass

            return version
    except Exception:
        pass

    # Fallback: read from VERSION file (for deployed environments without .git)
    if os.path.exists(version_file):
        try:
            with open(version_file) as f:
                version = f.read().strip()
                if version:
                    return version
        except Exception:
            pass

    return "unknown"
