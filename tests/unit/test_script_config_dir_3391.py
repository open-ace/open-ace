"""Regression tests for script-side OPENACE_CONFIG_DIR handling (#3391)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_MODULES = [
    pytest.param(PROJECT_ROOT / "scripts/shared/config.py", id="shared"),
    pytest.param(
        PROJECT_ROOT / "scripts/upload-to-central/shared/config.py",
        id="upload-to-central",
    ),
]

_LOAD_CONFIG = """
import importlib.util
import json
import sys

module_path = sys.argv[1]
spec = importlib.util.spec_from_file_location("_script_config_under_test", module_path)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not load {module_path}")
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)
print(json.dumps({
    "config_dir": config.CONFIG_DIR,
    "config_path": config.CONFIG_PATH,
    "db_dir": config.DB_DIR,
    "db_path": config.DB_PATH,
    "web_port": config.WEB_PORT,
    "database_url": config.get_database_url(),
}))
"""


def _load_config(module_path: Path, home: Path, override: Path | str | None) -> dict:
    env = os.environ.copy()
    for name in ("DATABASE_URL", "DB_HOST", "DB_NAME", "DB_USER", "AI_TOKEN_WEB_PORT"):
        env.pop(name, None)
    env["HOME"] = str(home)
    if override is None:
        env.pop("OPENACE_CONFIG_DIR", None)
    else:
        env["OPENACE_CONFIG_DIR"] = str(override)

    result = subprocess.run(
        [sys.executable, "-c", _LOAD_CONFIG, str(module_path)],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout.splitlines()[-1])


def _write_config(config_dir: Path) -> None:
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        json.dumps(
            {
                "server": {"web_port": 23456},
                "database": {"type": "sqlite"},
            }
        ),
        encoding="utf-8",
    )


def _expected_config(config_dir: Path) -> dict:
    db_path = config_dir / "ace.db"
    return {
        "config_dir": str(config_dir),
        "config_path": str(config_dir / "config.json"),
        "db_dir": str(config_dir),
        "db_path": str(db_path),
        "web_port": 23456,
        "database_url": f"sqlite:///{db_path}",
    }


@pytest.mark.parametrize("module_path", CONFIG_MODULES)
def test_script_config_uses_openace_config_dir(module_path: Path, tmp_path: Path) -> None:
    """Script config and its consumers should use the explicit directory."""
    home = tmp_path / "home"
    home.mkdir()
    config_dir = tmp_path / "configured"
    _write_config(config_dir)

    assert _load_config(module_path, home, config_dir) == _expected_config(config_dir)


@pytest.mark.parametrize("module_path", CONFIG_MODULES)
@pytest.mark.parametrize("override", [None, ""], ids=["unset", "empty"])
def test_script_config_falls_back_to_home(
    module_path: Path, override: str | None, tmp_path: Path
) -> None:
    """Absent and empty overrides should preserve the default directory."""
    home = tmp_path / "home"
    config_dir = home / ".open-ace"
    _write_config(config_dir)

    assert _load_config(module_path, home, override) == _expected_config(config_dir)
