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
        assert "#1782" in docs
        assert "#1781" in docs
        assert "single-instance reference deployment" not in docs


class TestComposeSunset:
    def test_main_compose_no_longer_advertises_broken_nginx_profile(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        assert "--profile production" not in compose
        assert "./nginx.conf:/etc/nginx/nginx.conf:ro" not in compose
        assert "./ssl:/etc/nginx/ssl:ro" not in compose


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
