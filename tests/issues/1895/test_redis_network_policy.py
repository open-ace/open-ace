"""Tests for Issue #1895: Redis NetworkPolicy configuration.

This module verifies:
- Redis ingress NetworkPolicy exists
- NetworkPolicy restricts access to open-ace pods only
- NetworkPolicy targets the correct cache component
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


class TestRedisNetworkPolicyIngress:
    """Test that Redis has proper ingress NetworkPolicy."""

    def test_redis_ingress_network_policy_exists(self):
        """A NetworkPolicy named 'redis-ingress' should exist in policies.yaml."""
        policies_path = ROOT / "k8s" / "policies.yaml"
        policies_content = policies_path.read_text(encoding="utf-8")

        documents = list(yaml.safe_load_all(policies_content))

        # Find redis-ingress NetworkPolicy
        redis_ingress_np = None
        for doc in documents:
            if (
                doc
                and doc.get("kind") == "NetworkPolicy"
                and doc.get("metadata", {}).get("name") == "redis-ingress"
            ):
                redis_ingress_np = doc
                break

        assert (
            redis_ingress_np is not None
        ), "NetworkPolicy 'redis-ingress' not found in policies.yaml"

    def test_redis_network_policy_restricts_to_open_ace_pods(self):
        """Redis NetworkPolicy should only allow pods with app.kubernetes.io/name: open-ace."""
        policies_path = ROOT / "k8s" / "policies.yaml"
        policies_content = policies_path.read_text(encoding="utf-8")

        documents = list(yaml.safe_load_all(policies_content))

        redis_ingress_np = None
        for doc in documents:
            if (
                doc
                and doc.get("kind") == "NetworkPolicy"
                and doc.get("metadata", {}).get("name") == "redis-ingress"
            ):
                redis_ingress_np = doc
                break

        assert redis_ingress_np is not None, "NetworkPolicy 'redis-ingress' not found"

        # Check ingress rules
        ingress_rules = redis_ingress_np.get("spec", {}).get("ingress", [])
        assert len(ingress_rules) > 0, "No ingress rules found in redis-ingress NetworkPolicy"

        # Check that there's a rule allowing open-ace pods
        found_open_ace_selector = False
        for rule in ingress_rules:
            from_rules = rule.get("from", [])
            for from_rule in from_rules:
                pod_selector = from_rule.get("podSelector", {})
                match_labels = pod_selector.get("matchLabels", {})
                if match_labels.get("app.kubernetes.io/name") == "open-ace":
                    found_open_ace_selector = True
                    break

        assert found_open_ace_selector, (
            "NetworkPolicy should have a rule allowing pods with "
            "app.kubernetes.io/name: open-ace"
        )

    def test_redis_network_policy_targets_cache_component(self):
        """Redis NetworkPolicy should target pods with app.kubernetes.io/component: cache."""
        policies_path = ROOT / "k8s" / "policies.yaml"
        policies_content = policies_path.read_text(encoding="utf-8")

        documents = list(yaml.safe_load_all(policies_content))

        redis_ingress_np = None
        for doc in documents:
            if (
                doc
                and doc.get("kind") == "NetworkPolicy"
                and doc.get("metadata", {}).get("name") == "redis-ingress"
            ):
                redis_ingress_np = doc
                break

        assert redis_ingress_np is not None, "NetworkPolicy 'redis-ingress' not found"

        # Check podSelector targets cache component
        pod_selector = redis_ingress_np.get("spec", {}).get("podSelector", {})
        match_labels = pod_selector.get("matchLabels", {})

        assert (
            match_labels.get("app.kubernetes.io/component") == "cache"
        ), "NetworkPolicy should target pods with app.kubernetes.io/component: cache"

    def test_redis_network_policy_allows_port_6379(self):
        """Redis NetworkPolicy should allow port 6379."""
        policies_path = ROOT / "k8s" / "policies.yaml"
        policies_content = policies_path.read_text(encoding="utf-8")

        documents = list(yaml.safe_load_all(policies_content))

        redis_ingress_np = None
        for doc in documents:
            if (
                doc
                and doc.get("kind") == "NetworkPolicy"
                and doc.get("metadata", {}).get("name") == "redis-ingress"
            ):
                redis_ingress_np = doc
                break

        assert redis_ingress_np is not None, "NetworkPolicy 'redis-ingress' not found"

        # Check that port 6379 is allowed
        ingress_rules = redis_ingress_np.get("spec", {}).get("ingress", [])

        found_port_6379 = False
        for rule in ingress_rules:
            ports = rule.get("ports", [])
            for port in ports:
                if port.get("port") == 6379:
                    found_port_6379 = True
                    break

        assert found_port_6379, "NetworkPolicy should allow port 6379 for Redis"

    def test_redis_network_policy_has_ingress_policy_type(self):
        """Redis NetworkPolicy should have Ingress in policyTypes."""
        policies_path = ROOT / "k8s" / "policies.yaml"
        policies_content = policies_path.read_text(encoding="utf-8")

        documents = list(yaml.safe_load_all(policies_content))

        redis_ingress_np = None
        for doc in documents:
            if (
                doc
                and doc.get("kind") == "NetworkPolicy"
                and doc.get("metadata", {}).get("name") == "redis-ingress"
            ):
                redis_ingress_np = doc
                break

        assert redis_ingress_np is not None, "NetworkPolicy 'redis-ingress' not found"

        policy_types = redis_ingress_np.get("spec", {}).get("policyTypes", [])
        assert (
            "Ingress" in policy_types
        ), f"NetworkPolicy should have 'Ingress' in policyTypes. Found: {policy_types}"
