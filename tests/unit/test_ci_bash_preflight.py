"""In-process error boundaries for the shared Bash prerequisite check."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(3366)]
ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ci_bash_unit", ROOT / "scripts/ci.py")
assert SPEC and SPEC.loader
ci = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci)


@pytest.mark.parametrize("error", [OSError("unusable"), subprocess.TimeoutExpired("bash", 5)])
def test_probe_failure_is_actionable(monkeypatch, error):
    monkeypatch.setattr(ci.shutil, "which", lambda *args, **kwargs: "/chosen/bash")

    def fail(*args, **kwargs):
        assert kwargs["timeout"] == 5
        raise error

    monkeypatch.setattr(ci.subprocess, "run", fail)
    with pytest.raises(ci.CIError, match=type(error).__name__) as caught:
        ci.check_bash({"toolchain": {"bash_min_major": 4}})
    assert "/chosen/bash" in str(caught.value)
    assert "doctor --strict" in str(caught.value)


def test_startup_normalization_does_not_mutate_parent(monkeypatch):
    monkeypatch.setenv("BASH_ENV", "startup.sh")
    monkeypatch.setenv("ENV", "startup.sh")
    monkeypatch.setenv("PATH", "relative::/absolute")
    child = ci.shell_environment()
    assert "BASH_ENV" not in child and "ENV" not in child
    assert ci.os.environ["BASH_ENV"] == ci.os.environ["ENV"] == "startup.sh"
    assert ci.os.environ["PATH"] == "relative::/absolute"
    assert child["PATH"].split(ci.os.pathsep) == [
        str(Path.cwd() / "relative"),
        str(Path.cwd()),
        "/absolute",
    ]


def test_preflight_failure_survives_metrics_write_failure(monkeypatch):
    monkeypatch.setattr(ci.shutil, "which", lambda *args, **kwargs: None)
    recorder = ci.MetricsRecorder(None)
    recorder.error = OSError("disk full")
    config = {"toolchain": {"bash_min_major": 4}, "suites": {"core": {"requires_bash": True}}}
    with pytest.raises(ci.CIError) as caught:
        ci.execute_suites(["core"], config, action="run", metrics=recorder)
    message = str(caught.value)
    assert message.index("Bash: missing") < message.index("metrics_write_error: disk full")
