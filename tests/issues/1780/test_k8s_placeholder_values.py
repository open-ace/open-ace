#!/usr/bin/env python3
"""RED test: K8s Secret placeholder values must fail-closed in production (PR #1780 R1).

``k8s/configmap.yaml`` ships stringData placeholders like
``replace-with-random-flask-secret``. The startup guard
``app/utils/security_env._WEAK_SECRET_VALUES`` previously only listed the OLD
sentinels, so these committed strings passed ``is_weak_secret_value()`` and
were used as real secrets in production.
"""

from __future__ import annotations

import pytest

from app.utils.security_env import is_weak_secret_value


class TestK8sPlaceholderSecretsFailClosed:
    @pytest.mark.parametrize(
        "placeholder",
        [
            "replace-with-random-flask-secret",
            "replace-with-random-dedicated-encryption-key",
            "replace-with-random-upload-auth-key",
            "replace-with-random-database-password",
        ],
    )
    def test_replace_with_random_placeholder_is_weak(self, placeholder):
        assert is_weak_secret_value(placeholder) is True

    def test_placeholder_case_insensitive(self):
        assert is_weak_secret_value("Replace-With-Random-Anything") is True

    def test_strong_secret_still_accepted(self):
        assert is_weak_secret_value("a-real-randomly-generated-64-char-secret-1234567890") is False

    def test_prefix_variant_detected(self):
        """Any replace-with-random-* variant must be detected, not just k8s placeholders."""
        assert is_weak_secret_value("replace-with-random-new-variant-not-in-k8s") is True

    def test_placeholder_with_whitespace(self):
        """Whitespace around placeholder must not bypass detection."""
        assert is_weak_secret_value("  replace-with-random-flask-secret  ") is True
