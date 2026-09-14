"""Regression tests for OPENACE_CONFIG_DIR handling (issue #3110)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_READ_WORKSPACE_CONFIG = """
import json
from app.services.webui_manager import read_workspace_config

config = read_workspace_config()
print(json.dumps({
    "enabled": config.enabled,
    "multi_user_mode": config.multi_user_mode,
}))
"""


def _read_workspace_config(home: Path, openace_config_dir: Path | str | None) -> dict[str, bool]:
    env = os.environ.copy()
    env.update({"HOME": str(home), "DATABASE_URL": "sqlite:///:memory:"})
    if openace_config_dir is None:
        env.pop("OPENACE_CONFIG_DIR", None)
    else:
        env["OPENACE_CONFIG_DIR"] = str(openace_config_dir)

    result = subprocess.run(
        [sys.executable, "-c", _READ_WORKSPACE_CONFIG],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_workspace_config_is_loaded_from_openace_config_dir(tmp_path: Path) -> None:
    """Workspace config should be read from the explicit config directory."""
    config_dir = tmp_path / "configured"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"workspace": {"enabled": True, "multi_user_mode": True}}),
        encoding="utf-8",
    )

    isolated_home = tmp_path / "home"
    isolated_home.mkdir()

    assert _read_workspace_config(isolated_home, config_dir) == {
        "enabled": True,
        "multi_user_mode": True,
    }


@pytest.mark.parametrize("openace_config_dir", [None, ""])
def test_workspace_config_falls_back_to_home_when_override_is_unusable(
    tmp_path: Path, openace_config_dir: str | None
) -> None:
    """An absent or empty override should preserve the default config path."""
    isolated_home = tmp_path / "home"
    config_dir = isolated_home / ".open-ace"
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps({"workspace": {"enabled": True, "multi_user_mode": True}}),
        encoding="utf-8",
    )

    assert _read_workspace_config(isolated_home, openace_config_dir) == {
        "enabled": True,
        "multi_user_mode": True,
    }
