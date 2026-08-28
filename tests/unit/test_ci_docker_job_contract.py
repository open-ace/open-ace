"""Contract for the main-push docker job's local image availability.

The docker job builds with buildx and then runs `docker run`/`docker images`
against the built tag inside the same job. Without `load: true` the build
never exports the image into the local Docker engine, so every consumer step
fails — exactly the main-push-only red of #3169 that PR CI cannot catch
(the job is gated to `push` + `refs/heads/main`). This pins the behavior:
whenever the job consumes the built tag locally, the producing build-push
step must load it into the engine.
"""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = [pytest.mark.regression, pytest.mark.issue(3169)]

TAG_PATTERN = re.compile(r"open-ace:\$\{\{ github\.sha \}\}")


def _docker_job():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["docker"]


def test_docker_job_stays_main_push_only():
    job = _docker_job()
    assert job["needs"] == ["lint", "test", "build"]
    assert job["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"


def test_local_tag_consumers_require_the_build_step_to_load_the_image():
    job = _docker_job()
    steps = job["steps"]

    build_steps = [
        step for step in steps if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert len(build_steps) == 1, "expected exactly one docker/build-push-action step"
    build = build_steps[0]

    consumers = [
        step
        for step in steps
        if step.get("run") and TAG_PATTERN.search(step["run"]) and "docker " in step["run"]
    ]
    assert consumers, "docker job must still exercise the built image locally"

    # The producer must tag with the same literal and export into the engine.
    tags = build.get("with", {}).get("tags", [])
    tags_text = " ".join(tags) if isinstance(tags, list) else str(tags)
    assert TAG_PATTERN.search(tags_text)
    assert build["with"].get("load") is True, (
        "build-push step must set load: true whenever later steps run the "
        "built tag via docker run/docker images (image must exist locally)"
    )
    assert build["with"].get("push") is False
    # Without an explicit target Docker builds the LAST stage (migration),
    # whose entrypoint ignores the command and execs server.py — the image
    # verified here must be the web image.
    assert build["with"].get("target") == "production"

    verify = next(step for step in steps if step.get("name") == "Verify code-server installation")
    assert TAG_PATTERN.search(verify["run"]) and "docker run" in verify["run"]
    # The production image is production-capable (FLASK_ENV=production), so
    # its entrypoint's fail-closed security-mode validation rejects one-shot
    # commands without an explicit OPENACE_SECURITY_MODE.
    assert "-e OPENACE_SECURITY_MODE=" in verify["run"]
    size = next(step for step in steps if step.get("name") == "Check image size")
    assert TAG_PATTERN.search(size["run"]) and "docker images" in size["run"]
    assert "GITHUB_STEP_SUMMARY" in size["run"]
