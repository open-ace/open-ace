"""Tests for Kubernetes manifest alignment.

Issue #2186: Health check probe paths must be correct.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


class TestKubernetesDeploymentProbes:
    """Tests for web deployment probe configuration."""

    def test_web_liveness_probe_points_to_livez(self):
        """Test that liveness probe uses /livez."""
        deployment = (ROOT / "k8s" / "deployment.yaml").read_text(encoding="utf-8")

        # Find livenessProbe section and verify path
        assert re.search(
            r"livenessProbe:.*?path:\s*/livez", deployment, re.DOTALL
        ), "livenessProbe must point to /livez"

    def test_web_readiness_probe_points_to_readyz(self):
        """Test that readiness probe uses /readyz."""
        deployment = (ROOT / "k8s" / "deployment.yaml").read_text(encoding="utf-8")

        # Find readinessProbe section and verify path
        assert re.search(
            r"readinessProbe:.*?path:\s*/readyz", deployment, re.DOTALL
        ), "readinessProbe must point to /readyz"

    def test_web_prometheus_scrape_points_to_metrics(self):
        """Test that Prometheus annotation points to /metrics."""
        deployment = (ROOT / "k8s" / "deployment.yaml").read_text(encoding="utf-8")

        assert (
            'prometheus.io/path: "/metrics"' in deployment
        ), "Prometheus annotation must point to /metrics"

    def test_web_has_startup_probe_configured(self):
        """Test that startupProbe is configured."""
        deployment = (ROOT / "k8s" / "deployment.yaml").read_text(encoding="utf-8")

        assert "startupProbe:" in deployment, "startupProbe must be configured"

    def test_web_startup_probe_allows_300_seconds(self):
        """Test that startupProbe allows at least 300 seconds."""
        deployment = (ROOT / "k8s" / "deployment.yaml").read_text(encoding="utf-8")

        # Extract startupProbe configuration
        # failureThreshold: 60, periodSeconds: 5 = 300s
        match = re.search(
            r"startupProbe:.*?failureThreshold:\s*(\d+).*?periodSeconds:\s*(\d+)",
            deployment,
            re.DOTALL,
        )
        if match:
            failure_threshold = int(match.group(1))
            period_seconds = int(match.group(2))
            total_time = failure_threshold * period_seconds
            assert total_time >= 300, f"startupProbe must allow at least 300s, got {total_time}s"

    def test_web_readiness_probe_timeout_greater_than_endpoint(self):
        """Test that K8s readiness timeout > endpoint timeout."""
        deployment = (ROOT / "k8s" / "deployment.yaml").read_text(encoding="utf-8")

        # Readiness probe timeout should be >= 5s (endpoint timeout is 5s)
        match = re.search(r"readinessProbe:.*?timeoutSeconds:\s*(\d+)", deployment, re.DOTALL)
        if match:
            timeout = int(match.group(1))
            assert timeout >= 5, f"readinessProbe timeout must be >= 5s, got {timeout}s"


class TestKubernetesSchedulerProbes:
    """Tests for scheduler deployment probe configuration."""

    def test_scheduler_liveness_probe_points_to_livez(self):
        """Test that scheduler liveness probe uses /livez."""
        deployment = (ROOT / "k8s" / "scheduler-deployment.yaml").read_text(encoding="utf-8")

        # Find livenessProbe section and verify path
        assert re.search(
            r"livenessProbe:.*?path:\s*/livez", deployment, re.DOTALL
        ), "scheduler livenessProbe must point to /livez"

    def test_scheduler_readiness_probe_points_to_health(self):
        """Test that scheduler readiness probe uses /health."""
        deployment = (ROOT / "k8s" / "scheduler-deployment.yaml").read_text(encoding="utf-8")

        # Scheduler keeps /health for readiness (includes leader status)
        assert re.search(
            r"readinessProbe:.*?path:\s*/health", deployment, re.DOTALL
        ), "scheduler readinessProbe should point to /health"


class TestNoOldHealthPath:
    """Tests to ensure old /health path is not used for probes."""

    def test_web_deployment_no_health_for_liveness(self):
        """Test that web deployment doesn't use /health for liveness."""
        deployment = (ROOT / "k8s" / "deployment.yaml").read_text(encoding="utf-8")

        # Should not have livenessProbe pointing to /health
        # (Note: /health still exists but is deprecated)
        liveness_section = re.search(
            r"livenessProbe:.*?(?=readinessProbe|startupProbe|volumeMounts|$)",
            deployment,
            re.DOTALL,
        )
        if liveness_section:
            assert "/health" not in liveness_section.group(
                0
            ), "livenessProbe should not use /health"

    def test_prometheus_not_scraping_health(self):
        """Test that Prometheus doesn't scrape /health."""
        deployment = (ROOT / "k8s" / "deployment.yaml").read_text(encoding="utf-8")

        # Prometheus should scrape /metrics, not /health
        assert (
            'prometheus.io/path: "/health"' not in deployment
        ), "Prometheus should not scrape /health"


class TestDockerfileHealthcheck:
    """Tests for Dockerfile healthcheck configuration."""

    def test_dockerfile_healthcheck_points_to_readyz(self):
        """Test that Dockerfile healthcheck uses /readyz."""
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        assert "/readyz" in dockerfile, "Dockerfile healthcheck should use /readyz"
        assert "HEALTHCHECK" in dockerfile, "Dockerfile must have HEALTHCHECK"


class TestDockerComposeHealthcheck:
    """Tests for docker-compose healthcheck configuration."""

    def test_docker_compose_healthcheck_points_to_readyz(self):
        """Test that docker-compose healthcheck uses /readyz."""
        compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

        assert "/readyz" in compose, "docker-compose healthcheck should use /readyz"
