"""Contract for the weekly cross-browser lane's server/browser environment.

The cross-browser job boots the real `server.py` through the playwright
webServer (frontend/playwright.config.ts) and swaps HOME to an isolated
directory for the E2E step. Two job-level env keys keep that combination
alive (#3170): SECRET_KEY (the fail-closed development-mode boot contract)
and PLAYWRIGHT_BROWSERS_PATH (browsers are installed under the real HOME;
without an absolute cache path the HOME swap makes every browser launch
fail with a missing executable).
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WEEKLY = REPO_ROOT / ".github" / "workflows" / "weekly-quality.yml"
PLAYWRIGHT_CONFIG = REPO_ROOT / "frontend" / "playwright.config.ts"

pytestmark = [pytest.mark.regression, pytest.mark.issue(3170)]


def _cross_browser_job():
    workflow = yaml.load(WEEKLY.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return workflow["jobs"]["cross-browser-e2e"]


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
