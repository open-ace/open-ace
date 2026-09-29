"""CI must reject an unsuitable shell before starting any selected command."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.regression, pytest.mark.issue(3366)]
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def checkout(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "ci").mkdir()
    (tmp_path / "bin").mkdir()
    (tmp_path / "home").mkdir()
    shutil.copyfile(ROOT / "scripts/ci.py", tmp_path / "scripts/ci.py")
    config = json.loads((ROOT / "ci/suites.json").read_text())
    # Keep the real manifest's prerequisites and selection semantics, but
    # substitute cheap observable commands for expensive test/dependency work.
    for name, suite in config["suites"].items():
        suite.pop("collection_target", None)
        suite.pop("working_directory", None)
        suite["commands"] = [
            [
                sys.executable,
                "-c",
                "from pathlib import Path; "
                f"Path({str(tmp_path / (name + '.ran'))!r}).write_text('executed')",
            ]
        ]
    (tmp_path / "ci/suites.json").write_text(json.dumps(config))
    return tmp_path


def _env(checkout):
    return {
        "PATH": str(checkout / "bin"),
        "HOME": str(checkout / "home"),
        "OPENACE_CI_METRICS_FILE": str(checkout / "metrics.jsonl"),
    }


def _invoke(checkout, *args, extra_env=None):
    return subprocess.run(
        [sys.executable, str(checkout / "scripts/ci.py"), *args],
        cwd=checkout,
        env={**_env(checkout), **(extra_env or {})},
        text=True,
        capture_output=True,
        timeout=20,
    )


def _shell(checkout, version, *, code=0, capability="assoc-ok"):
    # External interpreter protocol double: run as a real process. A separate
    # test below exercises actual Bash; this permits old/missing/broken cases
    # to run on Linux without installing additional shell versions.
    path = checkout / "bin/bash"
    path.write_text(
        f"#!{sys.executable}\nimport sys\n"
        f"print({version!r})\nprint({capability!r})\nsys.exit({code})\n"
    )
    path.chmod(0o755)
    return path


@pytest.mark.parametrize("shell", ["missing", "old", "broken", "malformed", "no-capability"])
def test_unsuitable_bash_blocks_entire_plan_before_first_command(checkout, shell):
    if shell != "missing":
        _shell(
            checkout,
            "3.2.57" if shell == "old" else "garbage" if shell == "malformed" else "5.2.0",
            code=7 if shell == "broken" else 0,
            capability="" if shell == "no-capability" else "assoc-ok",
        )
    result = _invoke(checkout, "run", "false-positive-scan", "python-core")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Bash" in result.stdout + result.stderr
    assert not list(checkout.glob("*.ran"))
    records = [json.loads(line) for line in (checkout / "metrics.jsonl").read_text().splitlines()]
    assert not any(record["record_type"] == "command_start" for record in records)
    assert records[-1]["record_type"] == "invocation_terminal"
    assert records[-1]["outcome"] == "failure"
    assert {record["suite"] for record in records if record["record_type"] == "suite_terminal"} == {
        "false-positive-scan",
        "python-core",
    }


@pytest.mark.parametrize("suite", ["python-core", "python-min"])
def test_valid_bash_allows_selected_commands(checkout, suite):
    path = _shell(checkout, "4.0.0")
    result = _invoke(checkout, "run", suite)
    assert result.returncode == 0, result.stdout + result.stderr
    assert str(path) in result.stdout
    assert (checkout / (suite + ".ran")).read_text() == "executed"


def test_bash_free_suite_does_not_require_shell(checkout):
    result = _invoke(checkout, "run", "false-positive-scan")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "false-positive-scan.ran").exists()


def test_doctor_reports_old_shell_and_strict_rejects_it(checkout):
    config_path = checkout / "ci/suites.json"
    config = json.loads(config_path.read_text())
    config["toolchain"]["production_python"] = f"{sys.version_info.major}.{sys.version_info.minor}"
    config_path.write_text(json.dumps(config))
    node = checkout / "bin/node"
    node.write_text(f"#!{sys.executable}\nprint('v{config['toolchain']['node']}.0.0')\n")
    node.chmod(0o755)
    path = _shell(checkout, "3.2.57")
    result = _invoke(checkout, "doctor", "--strict")
    assert result.returncode == 1
    assert str(path) in result.stdout + result.stderr
    assert "3.2.57" in result.stdout + result.stderr
    assert "Bash" in result.stdout + result.stderr
    advisory = _invoke(checkout, "doctor")
    assert advisory.returncode == 0
    assert "WARNING" in advisory.stderr
    _shell(checkout, "5.2.0")
    strict = _invoke(checkout, "doctor", "--strict")
    assert strict.returncode == 0, strict.stdout + strict.stderr


def test_pr_preflights_selected_requirements(checkout):
    git = shutil.which("git")
    assert git, "git is required by the CI PR entrypoint"
    (checkout / "bin/git").symlink_to(git)
    for args in [
        ["init"],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "fixture",
        ],
    ]:
        subprocess.run(
            [git, *args],
            cwd=checkout,
            check=True,
            capture_output=True,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(checkout / "home")},
        )
    result = _invoke(checkout, "pr", "--base", "HEAD")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Bash" in result.stdout + result.stderr
    assert not list(checkout.glob("*.ran"))


@pytest.mark.parametrize("startup_path", ["absolute", "relative", "home-sensitive"])
def test_real_bash_probe_and_commands_ignore_startup_injection(checkout, startup_path):
    bash = shutil.which("bash")
    assert bash, "Bash is required for this subprocess contract"
    (checkout / "bin/bash").symlink_to(bash)
    startup = checkout / "startup.sh"
    startup.write_text('echo poisoned > "$HOME/startup-ran"\nexit 7\n')
    if startup_path == "relative":
        injection = "startup.sh"
    elif startup_path == "home-sensitive":
        shutil.copyfile(startup, checkout / "home/startup.sh")
        injection = "$HOME/startup.sh"
    else:
        injection = str(startup)
    config_path = checkout / "ci/suites.json"
    config = json.loads(config_path.read_text())
    # This test checks normalization with the actual installed interpreter,
    # even on macOS Bash 3.2; it does not claim that interpreter meets >=4.
    config["suites"]["false-positive-scan"]["commands"] = [
        ["bash", "-c", 'test -z "${BASH_ENV-}${ENV-}" && echo clean-shell']
    ]
    config_path.write_text(json.dumps(config))
    result = _invoke(
        checkout, "run", "false-positive-scan", extra_env={"BASH_ENV": injection, "ENV": injection}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "clean-shell" in result.stdout
    assert not (checkout / "home/startup-ran").exists()
    doctor = _invoke(checkout, "doctor", extra_env={"BASH_ENV": injection, "ENV": injection})
    assert "Bash" in doctor.stdout
    # Compare with an uninjected probe: Bash 3.2 itself legitimately fails
    # capability checks, but the injected startup must not change the result.
    clean = _invoke(checkout, "doctor")
    assert (doctor.returncode, doctor.stdout, doctor.stderr) == (
        clean.returncode,
        clean.stdout,
        clean.stderr,
    )
    assert not (checkout / "home/startup-ran").exists()


def test_relative_path_keeps_same_bash_when_suite_changes_directory(checkout):
    _shell(checkout, "5.2.0")
    config_path = checkout / "ci/suites.json"
    config = json.loads(config_path.read_text())
    config["suites"]["python-core"]["working_directory"] = "scripts"
    config["suites"]["python-core"]["commands"] = [["bash", "-c", "ignored by protocol double"]]
    config_path.write_text(json.dumps(config))
    result = _invoke(checkout, "run", "python-core", extra_env={"PATH": "bin"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "assoc-ok" in result.stdout


@pytest.mark.parametrize("absolute", [False, True])
def test_path_symlink_parent_semantics_are_preserved(checkout, absolute):
    _shell(checkout, "5.2.0")
    (checkout / "nested/inner").mkdir(parents=True)
    (checkout / "link").symlink_to(checkout / "nested/inner", target_is_directory=True)
    # link/../bin denotes nested/bin (missing), not checkout/bin (suitable).
    path = "link/../bin"
    if absolute:
        path = str(checkout / path)
    result = _invoke(checkout, "run", "python-core", extra_env={"PATH": path})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "not found on PATH" in result.stderr
    assert not list(checkout.glob("*.ran"))
