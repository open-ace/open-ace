"""Regression tests for issue #1748 deployment alignment."""

from __future__ import annotations

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
    def test_deployment_manifest_matches_single_instance_runtime_contract(self):
        deployment = (ROOT / "k8s" / "deployment.yaml").read_text(encoding="utf-8")

        assert "replicas: 1" in deployment
        assert "containerPort: 19888" in deployment
        assert "runAsNonRoot: false" in deployment
        assert "runAsUser: 0" in deployment
        assert "mountPath: /workspace" in deployment
        assert "mountPath: /root/.open-ace" in deployment
        assert "kind: HorizontalPodAutoscaler" not in deployment
        assert 'prometheus.io/port: "5001"' not in deployment

    def test_service_and_policy_ports_match_19888_runtime_port(self):
        service = (ROOT / "k8s" / "service.yaml").read_text(encoding="utf-8")
        policies = (ROOT / "k8s" / "policies.yaml").read_text(encoding="utf-8")

        assert "port: 19888" in service
        assert "port: 19888" in policies
        assert "port: 5001" not in service
        assert "port: 5001" not in policies

    def test_k8s_secret_includes_dedicated_encryption_key(self):
        configmap = (ROOT / "k8s" / "configmap.yaml").read_text(encoding="utf-8")

        assert "OPENACE_ENCRYPTION_KEY" in configmap
        assert "WORKSPACE_BASE_DIR" in configmap


class TestComposeSunset:
    def test_main_compose_no_longer_advertises_broken_nginx_profile(self):
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        assert "--profile production" not in compose
        assert "./nginx.conf:/etc/nginx/nginx.conf:ro" not in compose
        assert "./ssl:/etc/nginx/ssl:ro" not in compose
