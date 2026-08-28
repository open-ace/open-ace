"""Contracts for the wheel entry-module verifier (issue #3171)."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_file_location(
    "openace_verify_wheel_entry", ROOT / "scripts" / "ci" / "verify_wheel_entry.py"
)
assert spec and spec.loader
verify_wheel_entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_wheel_entry)

pytestmark = [pytest.mark.regression, pytest.mark.issue(3171)]


def _write_wheel(path: Path, *, with_module: bool, entry_line: str | None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        if with_module:
            archive.writestr("cli.py", "def main():\n    return 0\n")
        archive.writestr("app/__init__.py", "")
        archive.writestr("open_ace-1.2.0.dist-info/METADATA", "Name: open-ace\n")
        if entry_line is not None:
            archive.writestr(
                "open_ace-1.2.0.dist-info/entry_points.txt",
                f"[console_scripts]\n{entry_line}\n",
            )


def test_complete_wheel_passes(tmp_path, monkeypatch, capsys):
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_wheel(
        dist / "open_ace-1.2.0-py3-none-any.whl",
        with_module=True,
        entry_line="openace = cli:main",
    )
    monkeypatch.chdir(tmp_path)

    assert verify_wheel_entry.main() == 0
    assert "ship cli.py" in capsys.readouterr().out


def test_wheel_missing_cli_module_fails(tmp_path, monkeypatch, capsys):
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_wheel(
        dist / "open_ace-1.2.0-py3-none-any.whl",
        with_module=False,
        entry_line="openace = cli:main",
    )
    monkeypatch.chdir(tmp_path)

    assert verify_wheel_entry.main() == 1
    assert "missing top-level cli.py" in capsys.readouterr().err


def test_wheel_with_wrong_entry_point_fails(tmp_path, monkeypatch, capsys):
    dist = tmp_path / "dist"
    dist.mkdir()
    _write_wheel(
        dist / "open_ace-1.2.0-py3-none-any.whl",
        with_module=True,
        entry_line="openace = server:main",
    )
    monkeypatch.chdir(tmp_path)

    assert verify_wheel_entry.main() == 1
    assert "lacks 'openace = cli:main'" in capsys.readouterr().err


def test_every_wheel_is_checked_and_missing_dist_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert verify_wheel_entry.main() == 1
    assert "no wheels found" in capsys.readouterr().err

    dist = tmp_path / "dist"
    dist.mkdir()
    _write_wheel(
        dist / "open_ace-1.0.0-py3-none-any.whl",
        with_module=True,
        entry_line="openace = cli:main",
    )
    _write_wheel(
        dist / "open_ace-2.0.0-py3-none-any.whl",
        with_module=False,
        entry_line="openace = cli:main",
    )

    assert verify_wheel_entry.main() == 1
    assert "open_ace-2.0.0" in capsys.readouterr().err
