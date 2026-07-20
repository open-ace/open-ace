#!/usr/bin/env python3
"""Test RBAC permission minimization (Issue #1895)."""

from __future__ import annotations

import pytest
import yaml


class TestRBACMinimization:
    """Test that RBAC permissions are minimized."""

    def test_main_role_no_secrets_permission(self):
        """Main application Role should not have secrets permission."""
        with open("k8s/storage.yaml") as f:
            docs = list(yaml.safe_load_all(f))

        # Find the Role document
        role = None
        for doc in docs:
            if doc and doc.get("kind") == "Role":
                role = doc
                break

        assert role is not None, "Role not found in storage.yaml"

        # Check that secrets is NOT in resources
        for rule in role["rules"]:
            resources = rule.get("resources", [])
            assert "secrets" not in resources, (
                f"Role should not have 'secrets' permission, found in: {resources}"
            )

    def test_backup_role_no_secrets_permission(self):
        """Backup Role should not have secrets permission."""
        with open("k8s/extras/backup/rbac.yaml") as f:
            docs = list(yaml.safe_load_all(f))

        # Find the Role document
        role = None
        for doc in docs:
            if doc and doc.get("kind") == "Role":
                role = doc
                break

        assert role is not None, "Role not found in backup rbac.yaml"

        # Check that secrets and configmaps are NOT in resources
        for rule in role["rules"]:
            resources = rule.get("resources", [])
            assert "secrets" not in resources, (
                f"Backup Role should not have 'secrets' permission, found in: {resources}"
            )
            assert "configmaps" not in resources, (
                f"Backup Role should not have 'configmaps' permission, found in: {resources}"
            )

    def test_main_role_has_configmaps_permission(self):
        """Main application Role should have configmaps permission."""
        with open("k8s/storage.yaml") as f:
            docs = list(yaml.safe_load_all(f))

        # Find the Role document
        role = None
        for doc in docs:
            if doc and doc.get("kind") == "Role":
                role = doc
                break

        assert role is not None, "Role not found in storage.yaml"

        # Check that configmaps IS in resources
        has_configmaps = False
        for rule in role["rules"]:
            resources = rule.get("resources", [])
            if "configmaps" in resources:
                has_configmaps = True
                # Should have get, list, watch verbs
                verbs = rule.get("verbs", [])
                assert "get" in verbs, f"configmaps should have 'get' verb, found: {verbs}"
                assert "list" in verbs, f"configmaps should have 'list' verb, found: {verbs}"
                assert "watch" in verbs, f"configmaps should have 'watch' verb, found: {verbs}"
                break

        assert has_configmaps, "Role should have 'configmaps' permission"

    def test_backup_role_has_jobs_permission(self):
        """Backup Role should have jobs permission for cleanup."""
        with open("k8s/extras/backup/rbac.yaml") as f:
            docs = list(yaml.safe_load_all(f))

        # Find the Role document
        role = None
        for doc in docs:
            if doc and doc.get("kind") == "Role":
                role = doc
                break

        assert role is not None, "Role not found in backup rbac.yaml"

        # Check that jobs IS in resources
        has_jobs = False
        for rule in role["rules"]:
            resources = rule.get("resources", [])
            if "jobs" in resources:
                has_jobs = True
                # Should have list, delete verbs
                verbs = rule.get("verbs", [])
                assert "list" in verbs, f"jobs should have 'list' verb, found: {verbs}"
                assert "delete" in verbs, f"jobs should have 'delete' verb, found: {verbs}"
                break

        assert has_jobs, "Backup Role should have 'jobs' permission"


class TestRedisStatefulSetConfig:
    """Test Redis StatefulSet authentication configuration."""

    def test_redis_statefulset_has_requirepass(self):
        """Redis StatefulSet should have requirepass in command."""
        with open("k8s/database.yaml") as f:
            docs = list(yaml.safe_load_all(f))

        # Find the Redis StatefulSet
        redis_sts = None
        for doc in docs:
            if doc and doc.get("kind") == "StatefulSet" and doc["metadata"]["name"] == "redis":
                redis_sts = doc
                break

        assert redis_sts is not None, "Redis StatefulSet not found"

        # Check command
        containers = redis_sts["spec"]["template"]["spec"]["containers"]
        redis_container = None
        for container in containers:
            if container["name"] == "redis":
                redis_container = container
                break

        assert redis_container is not None, "Redis container not found"

        # Check that command uses shell wrapper
        command = redis_container.get("command", [])
        assert "/bin/sh" in command, "Redis should use shell wrapper for command"
        assert "-c" in command, "Redis should use shell wrapper for command"

        # Check that requirepass is in the command
        command_str = " ".join(command)
        assert "requirepass" in command_str, "Redis should have requirepass in command"

    def test_redis_statefulset_has_rediscli_auth_env(self):
        """Redis StatefulSet should have REDISCLI_AUTH environment variable."""
        with open("k8s/database.yaml") as f:
            docs = list(yaml.safe_load_all(f))

        # Find the Redis StatefulSet
        redis_sts = None
        for doc in docs:
            if doc and doc.get("kind") == "StatefulSet" and doc["metadata"]["name"] == "redis":
                redis_sts = doc
                break

        assert redis_sts is not None, "Redis StatefulSet not found"

        # Check environment variables
        containers = redis_sts["spec"]["template"]["spec"]["containers"]
        redis_container = None
        for container in containers:
            if container["name"] == "redis":
                redis_container = container
                break

        assert redis_container is not None, "Redis container not found"

        # Check for REDISCLI_AUTH env var
        env_vars = redis_container.get("env", [])
        rediscli_auth_found = False
        for env in env_vars:
            if env.get("name") == "REDISCLI_AUTH":
                rediscli_auth_found = True
                assert env["valueFrom"]["secretKeyRef"]["key"] == "REDIS_PASSWORD"
                assert env["valueFrom"]["secretKeyRef"]["name"] == "open-ace-secrets"
                assert env.get("optional", False) is True
                break

        assert rediscli_auth_found, "REDISCLI_AUTH environment variable not found in Redis StatefulSet"


class TestSecretDefinition:
    """Test Secret definition has Redis password."""

    def test_secret_has_redis_password(self):
        """Secret should have REDIS_PASSWORD key with placeholder."""
        with open("k8s/configmap.yaml") as f:
            docs = list(yaml.safe_load_all(f))

        # Find the Secret document
        secret = None
        for doc in docs:
            if doc and doc.get("kind") == "Secret":
                secret = doc
                break

        assert secret is not None, "Secret not found in configmap.yaml"

        # Check stringData has REDIS_PASSWORD
        string_data = secret.get("stringData", {})
        assert "REDIS_PASSWORD" in string_data, "REDIS_PASSWORD not found in Secret"

        # Should have placeholder, not empty string
        redis_password = string_data["REDIS_PASSWORD"]
        assert redis_password != "", "REDIS_PASSWORD should not be empty"
        assert "replace-with-random" in redis_password, (
            f"REDIS_PASSWORD should have placeholder, got: {redis_password}"
        )