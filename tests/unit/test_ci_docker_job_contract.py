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


def test_production_image_precreates_logs_dir_for_non_root_entrypoint():
    # docker-entrypoint.sh runs `mkdir -p /app/logs` (Issue #1205) as uid 1000
    # before dispatching one-shot commands, /app itself is root-owned, and the
    # gitignored logs/ directory never ships in CI build contexts — the image
    # must therefore pre-create it with open-ace ownership.
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    production = dockerfile.split("FROM production AS development")[0]
    assert "mkdir -p /app/logs && chown open-ace:open-ace /app/logs" in production


# ── Multi-user deployment smoke wiring (#3289/#3293) ────────────────


@pytest.mark.issue(3289)
@pytest.mark.issue(3293)
def test_docker_job_runs_the_multiuser_deployment_smoke():
    """The root+multi-user positive paths (real useradd, permission-700
    collection, cross-user read regression, 100-user scale, wrapper audit
    pseudonymization) have no PR-lane coverage by construction — CI has no
    root. The main-push docker job's smoke step is their only automated
    verification; removing it (or losing --user 0 / any of the three
    multi-user envs) silently reintroduces the #3289/#3293 gap."""
    steps = _docker_job()["steps"]
    smoke = next((step for step in steps if "multiuser_smoke.py" in str(step.get("run", ""))), None)
    assert smoke is not None, (
        "docker job must run scripts/multiuser_smoke.py (the #3289/#3293 "
        "deployment-level verification)"
    )
    run = smoke["run"]
    assert "docker run" in run and TAG_PATTERN.search(
        run
    ), "smoke must docker run the freshly built main-push tag"
    assert "--user 0" in run, "production target ends at USER 1000 — the smoke needs root"
    for env in (
        "OPENACE_SECURITY_MODE=development",
        "WORKSPACE_MULTI_USER_MODE=true",
        "OPENACE_ALLOW_ROOT_MULTI_USER=1",
        "WORKSPACE_BASE_DIR=/workspace",
    ):
        assert f"-e {env}" in run, f"smoke step lost -e {env}"


def test_docker_sandbox_blocks_the_pr_gate():
    """PR #3386 review: docker-sandbox must be a REQUIRED PR-gate input.

    The repo ruleset's only required status check is "PR Gate"; if the
    sandbox-image job were not wired into its needs/validation, a sandbox
    build regression (e.g. the uid/gid-1000 clash) could not block a merge.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    gate = workflow["jobs"]["pr-gate"]

    assert "docker-sandbox" in gate["needs"]

    gate_step = gate["steps"][0]
    assert (
        gate_step.get("env", {}).get("SANDBOX") == "${{ needs.docker-sandbox.result }}"
    ), "gate must read the sandbox job result"
    assert '"SANDBOX"' in gate_step.get(
        "run", ""
    ), "gate must validate the sandbox result as required"


def test_qwen_stack_pins_are_consistent_across_all_sites():
    """Upgrade guard (PR #3386): the webui/CLI pair is pinned at FIVE sites
    (two Dockerfiles, package install.sh, remote-agent install.sh/.ps1).
    An upgrade that misses any site ships mixed versions. This contract
    fails with the per-site inventory the moment the pins disagree, so an
    upgrade is one commit that touches every site CI points at.
    """
    repo = REPO_ROOT
    webui_sites = {
        "Dockerfile": (repo / "Dockerfile").read_text(encoding="utf-8"),
        "scripts/docker/webui-sandbox.Dockerfile": (
            repo / "scripts" / "docker" / "webui-sandbox.Dockerfile"
        ).read_text(encoding="utf-8"),
    }
    cli_sites = dict(webui_sites)
    cli_sites.update(
        {
            "scripts/install-central/package-method/install.sh": (
                repo / "scripts" / "install-central" / "package-method" / "install.sh"
            ).read_text(encoding="utf-8"),
            "remote-agent/install.sh": (repo / "remote-agent" / "install.sh").read_text(
                encoding="utf-8"
            ),
            "remote-agent/install.ps1": (repo / "remote-agent" / "install.ps1").read_text(
                encoding="utf-8"
            ),
        }
    )

    def pins(sites, pattern):
        found = {}
        for name, text in sites.items():
            match = re.search(pattern, text, re.MULTILINE)
            assert match is not None, f"{name}: pin not found via {pattern!r}"
            found[name] = match.group(1)
        return found

    webui = pins(
        webui_sites,
        r"npm install -g(?: --prefix /usr)? qwen-code-webui@([0-9.]+)",
    )
    webui["scripts/install-central/package-method/install.sh (QWEBUI_VERSION)"] = pins(
        {
            "scripts/install-central/package-method/install.sh": cli_sites[
                "scripts/install-central/package-method/install.sh"
            ]
        },
        r'^QWEBUI_VERSION="([0-9.]+)"',
    )["scripts/install-central/package-method/install.sh"]

    cli = {}
    for name in ("Dockerfile", "scripts/docker/webui-sandbox.Dockerfile"):
        cli[name] = pins(
            {name: cli_sites[name]},
            r"@qwen-code/qwen-code@([0-9.]+)",
        )[name]
    cli["scripts/install-central/package-method/install.sh (QWEN_CLI_VERSION)"] = pins(
        {
            "scripts/install-central/package-method/install.sh": cli_sites[
                "scripts/install-central/package-method/install.sh"
            ]
        },
        r'^QWEN_CLI_VERSION="([0-9.]+)"',
    )["scripts/install-central/package-method/install.sh"]
    cli["remote-agent/install.sh (QWEN_CLI_VERSION)"] = pins(
        {"remote-agent/install.sh": cli_sites["remote-agent/install.sh"]},
        r'^QWEN_CLI_VERSION="([0-9.]+)"',
    )["remote-agent/install.sh"]
    cli["remote-agent/install.ps1 ($QwenCliVersion)"] = pins(
        {"remote-agent/install.ps1": cli_sites["remote-agent/install.ps1"]},
        r"^\$QwenCliVersion = \"([0-9.]+)\"",
    )["remote-agent/install.ps1"]

    assert len(set(webui.values())) == 1, f"webui pins disagree: {webui}"
    assert len(set(cli.values())) == 1, f"CLI pins disagree: {cli}"
