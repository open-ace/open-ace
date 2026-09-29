"""Regression tests for Issue #3455.

The multi-user overlay intends (Issue #3128) to make the scheduler start by
default in multi-user mode by overriding the base compose's
``profiles: [scheduler]``. Docker Compose MERGES ``profiles`` across merged
files, so a bare ``profiles: []`` in the overlay cannot remove the inherited
profile — the scheduler stayed opt-in and ``docker compose -f
docker-compose.yml -f docker-compose.multi-user.yml up -d`` silently deployed
without it. The fix uses Compose's ``!override`` tag (replacement merge
semantics, requires Compose >= v2.24).

These tests pin both directions:
* the merged (base + overlay) stack includes the scheduler, and
* the base-only compose keeps the scheduler opt-in, so the fix is not
  over-generalised into changing single-service deployments.
"""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

# Issue and regression markers for test discovery
pytestmark = [pytest.mark.regression, pytest.mark.issue(3455)]

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "docker-compose.yml"
OVERLAY = ROOT / "docker-compose.multi-user.yml"

# The overlay interpolates required variables with `:?` — provide dummies so
# `docker compose config` can resolve the model without a bootstrap.
REQUIRED_ENV = {
    "DB_PASSWORD": "test-only-strong-password",
    "SECRET_KEY": "s" * 64,
    "OPENACE_ENCRYPTION_KEY": "e" * 64,
}


def _compose_services(*compose_files: Path, with_required_env: bool = False) -> list[str]:
    """Run `docker compose config --services`, skipping when docker is unusable.

    Skip pattern copied from test_multi_user_config_2235.py: a compose
    invocation that fails for environment reasons (docker unavailable, base
    file context missing) is not this suite's subject.
    """
    cmd = ["docker", "compose"]
    for path in compose_files:
        cmd += ["-f", str(path)]
    cmd += ["config", "--services"]

    env = os.environ.copy()
    if with_required_env:
        env.update(REQUIRED_ENV)

    try:
        result = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except subprocess.TimeoutExpired:
        pytest.skip("Docker compose command timed out")
    except OSError as exc:
        pytest.skip(f"Docker CLI unavailable: {exc}")

    if result.returncode != 0:
        stderr = result.stderr
        if (
            "docker-compose.yml" in stderr
            or "has neither an image nor a build context" in stderr
            or "looking up compose provider failed" in stderr
            or "Cannot connect to the Docker daemon" in stderr
        ):
            pytest.skip(f"Docker compose environment unusable: {stderr.strip()}")
        pytest.fail(f"`{' '.join(cmd)}` failed: {stderr}")

    return result.stdout.split()


def test_merged_multi_user_stack_includes_scheduler():
    """Base + overlay `up -d` must bring the scheduler without --profile."""
    services = _compose_services(BASE, OVERLAY, with_required_env=True)
    assert "scheduler" in services, (
        f"scheduler missing from the merged multi-user stack: {services} — "
        "the overlay's profiles override no longer takes effect (#3455)"
    )


def test_base_only_compose_keeps_scheduler_opt_in():
    """Without the overlay the scheduler stays behind --profile scheduler."""
    services = _compose_services(BASE)
    assert "scheduler" not in services, (
        f"scheduler started from the base compose without a profile: {services} — "
        "single-service deployments must keep the scheduler opt-in"
    )


class _Override(list):
    """Marker for values built from Compose's `!override` tag."""


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader plus Compose's `!override` tag (see Issue #3455).

    Registered on a local subclass so the global SafeLoader stays untouched;
    the marker class lets us assert the tag itself, not just the value.
    """


_ComposeLoader.add_constructor(
    "!override", lambda loader, node: _Override(loader.construct_sequence(node))
)


def test_overlay_scheduler_profiles_use_override_tag():
    """A bare `profiles: []` is the regression — only `!override` replaces."""
    compose = yaml.load(OVERLAY.read_text(encoding="utf-8"), Loader=_ComposeLoader)
    profiles = compose["services"]["scheduler"]["profiles"]
    assert isinstance(profiles, _Override), (
        "scheduler profiles in the overlay must be `!override []`: a bare `[]` "
        "merges with the inherited [scheduler] profile and silently re-hides "
        "the service in multi-user mode (#3455)"
    )
    assert list(profiles) == [], "the override must replace profiles with an empty list"
