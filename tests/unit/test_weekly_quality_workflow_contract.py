"""Contract for the weekly cross-browser lane's server/browser environment.

The cross-browser job boots the real `server.py` through the playwright
webServer (frontend/playwright.config.ts) and swaps HOME to an isolated
directory for the E2E step. Two job-level env keys keep that combination
alive (#3170): SECRET_KEY (the fail-closed development-mode boot contract)
and PLAYWRIGHT_BROWSERS_PATH (browsers are installed under the real HOME;
without an absolute cache path the HOME swap makes every browser launch
fail with a missing executable).

Also pins the performance lane's env wiring (#3290): the qwen wall-clock
tests skip unless RUN_PERFORMANCE_TESTS is truthy, and nothing ever set
it — the advisory lane reported green while never executing them.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WEEKLY = REPO_ROOT / ".github" / "workflows" / "weekly-quality.yml"
PLAYWRIGHT_CONFIG = REPO_ROOT / "frontend" / "playwright.config.ts"
QWEN_PERF_TESTS = REPO_ROOT / "tests" / "performance" / "test_qwen_performance_2735.py"

pytestmark = [pytest.mark.regression, pytest.mark.issue(3170)]


def _cross_browser_job():
    workflow = yaml.load(WEEKLY.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return workflow["jobs"]["cross-browser-e2e"]


def _performance_job():
    workflow = yaml.load(WEEKLY.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return workflow["jobs"]["performance"]


def test_playwright_web_server_boots_the_real_server_py():
    config = PLAYWRIGHT_CONFIG.read_text(encoding="utf-8")
    web_server = re.search(r"webServer:\s*\{.*?command:\s*'([^']+)'", config, re.DOTALL)
    assert web_server, "playwright webServer command not found"
    assert "server.py" in web_server.group(1)


def test_server_booting_job_pins_secret_key_and_browser_cache_path():
    job = _cross_browser_job()
    env = job.get("env", {})

    # The development-mode boot contract fails closed without a key; the pin
    # keeps >= 32 chars of headroom over validate_secret_strength's floor.
    secret = env.get("SECRET_KEY")
    assert isinstance(secret, str) and secret and not secret.startswith("${{")
    assert len(secret) >= 32

    # Absolute cache path so the E2E step's HOME swap cannot hide the
    # browsers installed under the runner's real HOME.
    browsers = env.get("PLAYWRIGHT_BROWSERS_PATH")
    assert isinstance(browsers, str) and browsers
    assert browsers.startswith("${{") or browsers.startswith("/")

    run_step = next(
        step for step in job["steps"] if step.get("run", "").startswith("npm run test:e2e")
    )
    assert run_step["env"]["HOME"] == "${{ runner.temp }}/openace-home"


def test_cross_browser_job_timeout_covers_the_full_matrix():
    # The lane runs the full suite once per browser project on a single
    # worker; 90 minutes was never measured (both historical runs died at
    # server boot) and canceled the first real attempt (#3170).
    job = _cross_browser_job()
    assert int(job["timeout-minutes"]) >= 240


# ── Performance lane env wiring (#3290) ─────────────────────────────


@pytest.mark.issue(3290)
def test_performance_job_wires_the_gate_the_perf_tests_read():
    """Workflow side of the two-sided pin (#3287 pattern).

    The performance job must set a truthy literal RUN_PERFORMANCE_TESTS at
    job level (stricter than step level: covers every step's subprocesses,
    including ci.py's environment isolation). A `${{ }}` template or empty
    value would silently re-create the #3290 never-ran state.
    """
    env = _performance_job().get("env", {})
    value = env.get("RUN_PERFORMANCE_TESTS")
    assert isinstance(value, str) and value, (
        f"performance job must set job-level RUN_PERFORMANCE_TESTS (what "
        f"tests/performance/test_qwen_performance_2735.py gates on); got {value!r}"
    )
    assert not value.startswith("${{") and value != "0" and value.lower() != "false"


@pytest.mark.issue(3290)
def test_qwen_perf_tests_gate_lives_on_the_wired_env_name():
    """Test side: without the env the three tests skip, citing its name.

    Runs the real pytest collection/selection in a scrubbed subprocess —
    the gate must be live (evaluating RUN_PERFORMANCE_TESTS at runtime),
    not a copied constant. With the variable inherited from a developer
    shell this would flip to passing, so it is stripped explicitly. The
    skip reason is asserted as a substring; the full -rs line embeds
    file:line positions that drift on edits.
    """
    scrubbed = {k: v for k, v in os.environ.items() if k != "RUN_PERFORMANCE_TESTS"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(QWEN_PERF_TESTS), "-q", "-rs", "--no-header"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=scrubbed,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 skipped" in result.stdout, result.stdout
    assert (
        "RUN_PERFORMANCE_TESTS" in result.stdout
    ), "skip reason must name the env var so an unwired lane is diagnosable"


@pytest.mark.issue(3290)
def test_workflow_and_perf_file_pin_the_same_env_name():
    """Textual name lock: the workflow writes exactly the name the file reads."""
    workflow = WEEKLY.read_text(encoding="utf-8")
    perf_file = QWEN_PERF_TESTS.read_text(encoding="utf-8")
    assert re.search(
        r"RUN_PERFORMANCE_TESTS:\s*'[^']*'", workflow
    ), "weekly-quality.yml no longer writes RUN_PERFORMANCE_TESTS"
    assert 'os.environ.get("RUN_PERFORMANCE_TESTS"' in perf_file, (
        "the perf gate no longer reads RUN_PERFORMANCE_TESTS — update the "
        "workflow wiring in the same change"
    )
