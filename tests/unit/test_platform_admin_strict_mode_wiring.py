"""OPENACE_PLATFORM_ADMIN_STRICT_MODE must actually reach the role checks.

The flag shipped unwired. ``is_platform_admin_strict_mode()`` -- the only
function that read the environment variable and populated the module cache --
had zero production callers; every consumer went through
``get_cached_strict_mode()``, which returned ``_PLATFORM_ADMIN_STRICT_MODE or
False`` against a global that nothing ever set. The result was a switch that
could not be switched: setting the variable to ``true`` changed nothing, and
legacy ``admin`` stayed equivalent to ``platform_admin`` forever, while the
docstring carried a four-step deployment procedure for enabling it.

These tests pin the wiring itself, not just the helper's return type.

Lineage: Issue #2332 (centralized platform admin checking with strict mode).
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.regression,
    pytest.mark.issue(2332),
]


@pytest.fixture(autouse=True)
def _clean_strict_mode_cache():
    """Clear the process-global flag around every test.

    The cache is deliberately process-lifetime in production; leaving a value
    behind here would silently change how later tests classify the legacy
    ``admin`` role.
    """
    from app.auth.permissions import reset_strict_mode_cache

    reset_strict_mode_cache()
    yield
    reset_strict_mode_cache()


class TestFlagIsActuallyRead:
    def test_enabled_env_reaches_get_cached_strict_mode(self, monkeypatch):
        """The regression: this returned False no matter what the env said."""
        from app.auth.permissions import get_cached_strict_mode

        monkeypatch.setenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", "true")

        assert get_cached_strict_mode() is True

    def test_disabled_env_is_the_default(self, monkeypatch):
        from app.auth.permissions import get_cached_strict_mode

        monkeypatch.delenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", raising=False)

        assert get_cached_strict_mode() is False

    @pytest.mark.parametrize("value", ["TRUE", "True", "tRuE"])
    def test_case_insensitive(self, monkeypatch, value):
        from app.auth.permissions import get_cached_strict_mode

        monkeypatch.setenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", value)

        assert get_cached_strict_mode() is True

    @pytest.mark.parametrize("value", ["false", "1", "yes", "on", ""])
    def test_only_the_literal_true_enables_it(self, monkeypatch, value):
        """Anything other than 'true' leaves the permissive default in place."""
        from app.auth.permissions import get_cached_strict_mode

        monkeypatch.setenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", value)

        assert get_cached_strict_mode() is False

    def test_value_is_frozen_after_first_read(self, monkeypatch):
        """A mid-flight env change must not make two requests disagree."""
        from app.auth.permissions import get_cached_strict_mode

        monkeypatch.setenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", "true")
        assert get_cached_strict_mode() is True

        monkeypatch.setenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", "false")
        assert get_cached_strict_mode() is True


class TestRoleChecksHonourTheFlag:
    """The flag is only meaningful if it changes an authorization outcome."""

    def test_legacy_admin_is_platform_admin_when_disabled(self, monkeypatch):
        from app.auth.permissions import is_platform_admin_role

        monkeypatch.setenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", "false")

        assert is_platform_admin_role("admin") is True
        assert is_platform_admin_role("platform_admin") is True

    def test_legacy_admin_is_rejected_when_enabled(self, monkeypatch):
        """This is the whole point of the flag, and it never worked."""
        from app.auth.permissions import is_platform_admin_role

        monkeypatch.setenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", "true")

        assert is_platform_admin_role("admin") is False
        assert is_platform_admin_role("platform_admin") is True

    def test_user_model_honours_the_flag(self, monkeypatch):
        from app.models.user import User

        monkeypatch.setenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", "true")
        legacy = User(id=1, username="legacy", email="l@example.com", role="admin")

        assert legacy.is_platform_admin() is False
        assert legacy.is_platform_admin(strict=False) is True

    def test_non_admin_roles_are_never_platform_admin(self, monkeypatch):
        from app.auth.permissions import is_platform_admin_role

        monkeypatch.setenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", "true")

        for role in ("user", "tenant_admin", "", None):
            assert is_platform_admin_role(role) is False


class TestStartupInitialization:
    def test_init_resolves_and_freezes_the_flag(self, monkeypatch):
        from app.auth.permissions import get_cached_strict_mode, init_platform_admin_strict_mode

        monkeypatch.setenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", "true")

        assert init_platform_admin_strict_mode() is True

        monkeypatch.setenv("OPENACE_PLATFORM_ADMIN_STRICT_MODE", "false")
        assert init_platform_admin_strict_mode() is True
        assert get_cached_strict_mode() is True

    def test_init_is_called_during_app_creation(self):
        """Guards against the wiring being dropped from create_app again.

        Checked statically: actually building the app opens a database and
        would make this test depend on the local schema being current, which
        says nothing about the flag.
        """
        import ast
        import inspect

        import app as app_pkg

        tree = ast.parse(inspect.getsource(app_pkg.create_app))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert "init_platform_admin_strict_mode" in called, (
            "create_app must call init_platform_admin_strict_mode() so the flag "
            "is frozen and logged before the first request"
        )
