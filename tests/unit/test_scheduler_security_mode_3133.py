"""Regression tests for Issue #3133.

The scheduler service in the base docker-compose.yml must set
OPENACE_SECURITY_MODE explicitly: docker-entrypoint.sh treats
SCHEDULER_MODE=scheduler as a production-capable path and exits with
"OPENACE_SECURITY_MODE must be set explicitly in production-capable paths"
when the mode is unset, so `docker compose --profile scheduler up -d
scheduler` (without the multi-user overlay) crashed in a restart loop.
"""

from pathlib import Path

import pytest
import yaml

# Issue and regression markers for test discovery
pytestmark = [pytest.mark.regression, pytest.mark.issue(3133)]

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "docker-compose.yml"
OVERLAY = ROOT / "docker-compose.multi-user.yml"


def _env_of(path: Path, service: str) -> dict:
    compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = compose["services"][service]["environment"]
    return dict(entry.split("=", 1) for entry in entries if "=" in str(entry))


def test_base_compose_scheduler_sets_security_mode():
    """The scheduler must declare OPENACE_SECURITY_MODE like the app service."""
    assert "OPENACE_SECURITY_MODE" in _env_of(BASE, "scheduler")


def test_base_compose_scheduler_default_matches_app_service():
    """Both services fall back to development when .env is silent."""
    scheduler = _env_of(BASE, "scheduler")
    app = _env_of(BASE, "open-ace")
    assert scheduler["OPENACE_SECURITY_MODE"] == "${OPENACE_SECURITY_MODE:-development}"
    assert scheduler["OPENACE_SECURITY_MODE"] == app["OPENACE_SECURITY_MODE"]


def test_multi_user_overlay_keeps_stricter_production_default():
    """The overlay still overrides the base default with production."""
    assert (
        _env_of(OVERLAY, "scheduler")["OPENACE_SECURITY_MODE"]
        == "${OPENACE_SECURITY_MODE:-production}"
    )
