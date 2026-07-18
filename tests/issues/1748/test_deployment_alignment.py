"""Regression tests for issue #1748 deployment alignment."""

from __future__ import annotations

import re
from pathlib import Path

import scripts.shared.config as shared_config

ROOT = Path(__file__).resolve().parents[3]


class TestDatabaseUrlEnvFallback:
    def test_split_db_env_builds_postgres_url(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("DB_HOST", "postgres")
        monkeypatch.setenv("DB_PORT", "5432")
        monkeypatch.setenv("DB_NAME", "openace")
        monkeypatch.setenv("DB_USER", "openace")
        monkeypatch.setenv("DB_PASSWORD", "p@ss word")

        url = shared_config.get_database_url()

        assert url == "postgresql://openace:p%40ss%20word@postgres:5432/openace"


class TestKubernetesManifestAlignment:
    def test_deployment_manifest_preserves_ha_and_non_root_runtime_contract(self):
        deployment = (ROOT / "k8s" / "deployment.yaml").read_text(encoding="utf-8")

        assert "replicas: 3" in deployment
        assert "containerPort: 19888" in deployment
        assert "runAsNonRoot: true" in deployment
        assert "runAsUser: 1000" in deployment
        assert "runAsGroup: 1000" in deployment
        assert "allowPrivilegeEscalation: false" in deployment
        assert "mountPath: /workspace" in deployment
        assert "mountPath: /home/open-ace/.open-ace" in deployment
        assert "kind: HorizontalPodAutoscaler" in deployment
        assert "minReplicas: 3" in deployment
        assert 'prometheus.io/port: "19888"' in deployment

    def test_service_and_policy_ports_match_19888_runtime_port(self):
        service = (ROOT / "k8s" / "service.yaml").read_text(encoding="utf-8")
        policies = (ROOT / "k8s" / "policies.yaml").read_text(encoding="utf-8")

        assert "port: 19888" in service
        assert "port: 19888" in policies
        assert "port: 5001" not in service
        assert "port: 5001" not in policies
        assert "sessionAffinity: ClientIP" in service
        assert 'nginx.ingress.kubernetes.io/affinity: "cookie"' in service
        assert "minAvailable: 2" in policies

    def test_k8s_secret_includes_dedicated_encryption_key(self):
        configmap = (ROOT / "k8s" / "configmap.yaml").read_text(encoding="utf-8")

        assert "OPENACE_ENCRYPTION_KEY" in configmap
        assert "WORKSPACE_BASE_DIR" in configmap
        assert "change-me-in-production" not in configmap

    def test_shared_app_pvc_supports_multiple_replicas(self):
        storage = (ROOT / "k8s" / "storage.yaml").read_text(encoding="utf-8")

        assert "ReadWriteMany" in storage

    def test_k8s_docs_describe_sticky_multi_replica_boundary(self):
        docs = (ROOT / "docs" / "en" / "KUBERNETES.md").read_text(encoding="utf-8")

        assert "multi-replica reference deployment with sticky routing" in docs
        assert "Remote session commands, command responses, session output replay" in docs
        assert "Tenant-aware schema and query boundaries cover users" in docs
        assert "single-instance reference deployment" not in docs


class TestComposeSunset:
    def test_main_compose_no_longer_advertises_broken_nginx_profile(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        assert "--profile production" not in compose
        assert "./nginx.conf:/etc/nginx/nginx.conf:ro" not in compose
        assert "./ssl:/etc/nginx/ssl:ro" not in compose


class TestDockerImageDefaultsToNonRoot:
    def test_dockerfile_sets_non_root_user_directive(self):
        """The image must default to non-root everywhere, not only under K8s.

        Regression for PR #1780 review: image had no USER directive so
        `docker run` executed as root even though deployment.yaml set 1000.
        """
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        # Strip comment lines so the line-172 suggestion comment doesn't false-pass.
        lines = [
            ln for ln in dockerfile.splitlines() if ln.strip() and not ln.lstrip().startswith("#")
        ]
        body = "\n".join(lines)
        # Require an active USER directive naming the non-root user or its uid.
        assert re.search(r"^USER\s+(open-ace|1000)\s*$", body, re.MULTILINE), (
            "Dockerfile must declare `USER open-ace` (or `USER 1000`) in the "
            "production stage so the image is non-root by default, not just "
            "under the K8s manifest."
        )

    def test_dockerfile_non_root_user_matches_manifest_uid(self):
        """If the image sets USER <uid>, it must match deployment.yaml runAsUser."""
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        deployment = (ROOT / "k8s" / "deployment.yaml").read_text(encoding="utf-8")
        m = re.search(r"runAsUser:\s*(\d+)", deployment)
        assert m, "deployment.yaml must set runAsUser"
        manifest_uid = m.group(1)
        # The image must not contradict the manifest (either name or same uid).
        assert re.search(
            rf"^USER\s+(open-ace|{manifest_uid})\s*$", dockerfile, re.MULTILINE
        ), f"Dockerfile USER directive must resolve to uid {manifest_uid}"


class TestDockerEntrypointDefaults:
    def test_generated_config_enables_autonomous_by_default(self):
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")

        assert re.search(r'"autonomous":\s*\{\s*"enabled": true', entrypoint)

    def test_generated_config_does_not_force_root_only_multi_user_mode(self):
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")

        assert '"multi_user_mode": ${DEFAULT_WORKSPACE_MULTI_USER_MODE}' in entrypoint
        assert (
            'DEFAULT_WORKSPACE_MULTI_USER_MODE="${WORKSPACE_MULTI_USER_MODE:-false}"' in entrypoint
        )


# ============================================================================
# Regression tests for PR #1797: non-root (USER 1000) startup path.
# ============================================================================
# The image defaults to the non-root open-ace user (uid 1000). Before the fix,
# docker-entrypoint.sh hardcoded `OPENACE_CONFIG_DIR="${OPENACE_CONFIG_DIR:-/root/.open-ace}"`.
# Under uid 1000, /root is root:root 0700, so `generate_default_config`'s
# `mkdir -p /root/.open-ace` hit Permission denied and, with `set -e`, the
# container exited on startup. These tests guard that path so CI catches the
# regression instead of only static-Dockerfile checks (which stayed green).
import os as _os
import re as _re
import subprocess as _subprocess

_CONFIG_DIR_BLOCK_RE = _re.compile(
    r'if \[ -z "\$\{OPENACE_CONFIG_DIR:-\}" \]; then.*?\nfi',
    _re.DOTALL,
)


def _extract_config_dir_default_block(entrypoint_src):
    """Pull the OPENACE_CONFIG_DIR default-resolution `if ... fi` block out of
    the entrypoint so tests can eval just that logic without sourcing the whole
    script (which needs node/gh/postgres and runs mkdir)."""
    m = _CONFIG_DIR_BLOCK_RE.search(entrypoint_src)
    assert m, "Could not find OPENACE_CONFIG_DIR default block in entrypoint"
    return m.group(0)


def _run_entrypoint_block(block, env, id_u):
    """Eval the extracted default-resolution block under a stubbed `id -u` and
    return the resolved OPENACE_CONFIG_DIR.

    The block calls `$(id -u)`; we redefine `id` as a shell function so the test
    is deterministic regardless of the host runner's real uid. Only the env
    keys the caller passed are exported (so an unset HOME stays unset)."""
    full_env = {"PATH": _os.environ.get("PATH", "/usr/bin:/bin")}
    for key, value in env.items():
        if value is not None:
            full_env[key] = value

    # id() returns the stubbed uid for `-u`, delegates to the real binary otherwise.
    id_shim = 'id() { if [ "$1" = "-u" ]; then echo "%s"; else command id "$@"; fi; }' % id_u
    print_shim = 'printf %s "$OPENACE_CONFIG_DIR"'
    script = "\n".join([id_shim, block, print_shim])

    proc = _subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=full_env,
    )
    assert proc.returncode == 0, "entrypoint block exited %d: stderr=%r" % (
        proc.returncode,
        proc.stderr,
    )
    return proc.stdout


class TestEntrypointNonRootConfigDirDefault:
    """The config dir default must be writable by the runtime uid."""

    def test_uid_aware_branch_exists(self):
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
        assert (
            '"$(id -u)" = "0"' in entrypoint
        ), "entrypoint must branch the config dir default on the runtime uid"

    def test_no_unguarded_root_default(self):
        """Every non-comment /root/.open-ace literal must sit inside the
        root-only branch. (Occurrences inside `#` comments are explanatory and
        are ignored.)"""
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
        for lineno, line in enumerate(entrypoint.splitlines(), start=1):
            if "/root/.open-ace" not in line:
                continue
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue  # explanatory comment, not a code default
            # Code occurrences must be guarded by the root-only uid branch.
            preceding = "\n".join(entrypoint.splitlines()[:lineno])
            assert '"$(id -u)" = "0"' in preceding, (
                "/root/.open-ace in code at line %d is not behind the root-only "
                "branch; non-root startup would default to an unwritable path." % lineno
            )

    def test_non_root_uid_resolves_home_based_default(self):
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
        block = _extract_config_dir_default_block(entrypoint)
        result = _run_entrypoint_block(block, env={"HOME": "/home/open-ace"}, id_u="1000")
        assert result == "/home/open-ace/.open-ace", (
            "non-root default config dir should be /home/open-ace/.open-ace, " "got %r" % result
        )

    def test_root_uid_keeps_legacy_root_default(self):
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
        block = _extract_config_dir_default_block(entrypoint)
        result = _run_entrypoint_block(block, env={"HOME": "/root"}, id_u="0")
        assert result == "/root/.open-ace", (
            "root default config dir should stay /root/.open-ace, got %r" % result
        )

    def test_explicit_override_is_respected(self):
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
        block = _extract_config_dir_default_block(entrypoint)
        result = _run_entrypoint_block(
            block,
            env={"HOME": "/home/open-ace", "OPENACE_CONFIG_DIR": "/custom/cfg"},
            id_u="1000",
        )
        assert result == "/custom/cfg", (
            "explicit OPENACE_CONFIG_DIR override ignored, got %r" % result
        )

    def test_home_fallback_when_unset(self):
        entrypoint = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
        block = _extract_config_dir_default_block(entrypoint)
        result = _run_entrypoint_block(block, env={}, id_u="1000")
        assert result == "/home/open-ace/.open-ace", (
            "missing HOME should fall back to /home/open-ace/.open-ace, " "got %r" % result
        )


class TestComposeConfigVolumeWritableByNonRoot:
    """docker-compose must mount the config volume where uid 1000 can write."""

    def test_config_volume_mounted_under_non_root_home(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
        assert "config-data:/root/.open-ace" not in compose, (
            "docker-compose still mounts config-data to /root/.open-ace, which "
            "uid 1000 (image default) cannot write."
        )
        assert "config-data:/home/open-ace/.open-ace" in compose, (
            "docker-compose must mount config-data under /home/open-ace/.open-ace "
            "so the non-root user can persist generated config.json."
        )

    def test_dockerfile_precreates_non_root_config_dir(self):
        """The image must pre-create + chown the non-root config dir so the
        named-volume mount is writable on first run."""
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        assert _re.search(
            r"mkdir -p /home/open-ace/\.open-ace", dockerfile
        ), "Dockerfile must pre-create /home/open-ace/.open-ace"
        assert _re.search(
            r"chown -R open-ace:open-ace /home/open-ace\b", dockerfile
        ), "Dockerfile must chown /home/open-ace to open-ace"
