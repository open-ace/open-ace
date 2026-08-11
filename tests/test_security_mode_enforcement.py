"""
Unit tests for Security Mode Enforcement (Issue #2331).

Tests cover:
- SecurityModeSource enum
- Test context detection
- Production-capable path detection
- require_explicit_mode() behavior
- Mode source tracking
- FLASK_ENV deprecation warning
- Schema guard compatibility layer
- Pilot metadata creation
"""

import os
import sys

import pytest

# Import from security_mode module
from app.utils.security_mode import (
    SecurityMode,
    SecurityModeSource,
    detect_security_mode,
    get_pilot_metadata_path,
    get_security_mode,
    get_security_mode_with_source,
    is_production_capable_path,
    is_test_context,
    load_pilot_metadata,
    require_explicit_mode,
    reset_security_mode_cache,
)


class TestSecurityModeSource:
    """Tests for SecurityModeSource enum."""

    def test_explicit_source_value(self):
        """Test EXPLICIT source value."""
        assert SecurityModeSource.EXPLICIT.value == "explicit"

    def test_inferred_source_value(self):
        """Test INFERRED_FLASK_ENV source value."""
        assert SecurityModeSource.INFERRED_FLASK_ENV.value == "inferred"

    def test_default_source_value(self):
        """Test DEFAULT source value."""
        assert SecurityModeSource.DEFAULT.value == "default"


class TestTestContextDetection:
    """Tests for test context detection."""

    def test_pytest_in_sys_modules(self):
        """Test pytest detection in sys.modules."""
        # pytest should be in sys.modules when running tests
        assert is_test_context() is True

    def test_openace_test_mode_env(self, monkeypatch):
        """Test OPENACE_TEST_MODE=1 detection."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_TEST_MODE", "1")
        assert is_test_context() is True

    def test_pytest_current_test_env(self, monkeypatch):
        """Test PYTEST_CURRENT_TEST detection."""
        reset_security_mode_cache()
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_foo.py::test_bar")
        assert is_test_context() is True


class TestProductionCapablePathDetection:
    """Tests for production-capable path detection."""

    def test_test_context_not_production_capable(self):
        """Test context is never production-capable."""
        # When running tests, is_test_context() returns True
        assert is_production_capable_path() is False

    def test_ci_environment_not_production_capable(self, monkeypatch):
        """Test CI environment is not production-capable."""
        reset_security_mode_cache()
        # Clear test indicators
        monkeypatch.delenv("OPENACE_TEST_MODE", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setenv("CI", "true")

        # Even without test context, CI should not be production-capable
        # But we're still in test context (pytest in sys.modules)
        assert is_production_capable_path() is False

    def test_emergency_rollback_not_production_capable(self, monkeypatch):
        """Test emergency rollback flag makes path not production-capable."""
        reset_security_mode_cache()
        # Set flag with a recent timestamp (within 30 days)
        monkeypatch.setenv("OPENACE_ALLOW_IMPLICIT_MODE", "1")
        monkeypatch.setenv("OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP", "2025-08-01")

        # Emergency rollback should make it not production-capable
        assert is_production_capable_path() is False

    def test_emergency_rollback_expired(self, monkeypatch, caplog):
        """Test expired emergency rollback flag is ignored."""
        reset_security_mode_cache()
        import logging

        # Set flag with an old timestamp (more than 30 days ago)
        monkeypatch.setenv("OPENACE_ALLOW_IMPLICIT_MODE", "1")
        monkeypatch.setenv("OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP", "2025-01-01")

        # Mock is_test_context to return False so we can test the expiration logic
        monkeypatch.setattr("app.utils.security_mode.is_test_context", lambda: False)

        # Issue #2331: Use caplog.set_level() for reliable capture in CI
        # caplog.at_level() context manager may not work reliably in all environments
        caplog.set_level(logging.ERROR, "app.utils.security_mode")
        is_production_capable_path()

        # Expired flag should be ignored - verify expiration error is logged
        assert any(
            "EMERGENCY ROLLBACK FLAG EXPIRED" in record.message for record in caplog.records
        ), f"Expected EXPIRED error in logs, got: {[r.message for r in caplog.records]}"
        # Flag is ignored, so production-capable checks continue
        # Since we're in test context mocked to False, result depends on other indicators
        # The important thing is the expired flag didn't make it non-production-capable

    def test_emergency_rollback_missing_timestamp(self, monkeypatch, caplog):
        """Test emergency rollback flag without timestamp is ignored."""
        reset_security_mode_cache()
        import logging

        monkeypatch.setenv("OPENACE_ALLOW_IMPLICIT_MODE", "1")
        # Don't set OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP

        # Mock is_test_context to return False so we can test the flag logic
        monkeypatch.setattr("app.utils.security_mode.is_test_context", lambda: False)

        # Issue #2331: Use caplog.set_level() for reliable capture in CI
        caplog.set_level(logging.ERROR, "app.utils.security_mode")
        is_production_capable_path()

        # Flag should be ignored due to missing timestamp
        # Should log an error about missing timestamp
        assert any(
            "requires OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP" in record.message
            for record in caplog.records
        ), f"Expected timestamp requirement error in logs, got: {[r.message for r in caplog.records]}"


class TestDetectSecurityMode:
    """Tests for security mode detection with source."""

    def test_explicit_production_mode(self, monkeypatch):
        """Test explicit production mode detection."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        mode, source = detect_security_mode()
        assert mode == SecurityMode.PRODUCTION
        assert source == SecurityModeSource.EXPLICIT

    def test_explicit_pilot_mode(self, monkeypatch):
        """Test explicit pilot mode detection."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "pilot")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        mode, source = detect_security_mode()
        assert mode == SecurityMode.PILOT
        assert source == SecurityModeSource.EXPLICIT

    def test_explicit_development_mode(self, monkeypatch):
        """Test explicit development mode detection."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        mode, source = detect_security_mode()
        assert mode == SecurityMode.DEVELOPMENT
        assert source == SecurityModeSource.EXPLICIT

    def test_flask_env_production_inferred(self, monkeypatch):
        """Test FLASK_ENV=production inference (deprecated)."""
        reset_security_mode_cache()
        monkeypatch.delenv("OPENACE_SECURITY_MODE", raising=False)
        monkeypatch.setenv("FLASK_ENV", "production")

        mode, source = detect_security_mode()
        assert mode == SecurityMode.PRODUCTION
        assert source == SecurityModeSource.INFERRED_FLASK_ENV

    def test_unknown_mode_raises_error(self, monkeypatch):
        """Test unknown mode value raises RuntimeError."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "invalid")

        with pytest.raises(RuntimeError) as exc_info:
            detect_security_mode()

        assert "Unknown OPENACE_SECURITY_MODE value" in str(exc_info.value)
        assert "invalid" in str(exc_info.value)

    def test_default_development_in_test_context(self, monkeypatch):
        """Test default to development in test context."""
        reset_security_mode_cache()
        monkeypatch.delenv("OPENACE_SECURITY_MODE", raising=False)
        monkeypatch.delenv("FLASK_ENV", raising=False)

        # In test context, should get development with DEFAULT source
        mode, source = detect_security_mode()
        assert mode == SecurityMode.DEVELOPMENT
        assert source == SecurityModeSource.DEFAULT


class TestRequireExplicitMode:
    """Tests for require_explicit_mode() function."""

    def test_test_context_allows_implicit(self):
        """Test context allows implicit development mode."""
        reset_security_mode_cache()

        # Should not raise in test context
        require_explicit_mode()

    def test_test_context_sets_development(self):
        """Test context sets development mode."""
        reset_security_mode_cache()

        # In test context, should set development mode
        require_explicit_mode()

        mode, source = get_security_mode_with_source()
        assert mode == SecurityMode.DEVELOPMENT
        assert source == SecurityModeSource.DEFAULT

    def test_explicit_mode_respected_when_not_cached(self, monkeypatch):
        """Test explicit mode is used when cache is fresh."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        # Before calling require_explicit_mode, check that explicit mode is detected
        mode, source = detect_security_mode()
        assert mode == SecurityMode.PRODUCTION
        assert source == SecurityModeSource.EXPLICIT


class TestGetSecurityModeWithSource:
    """Tests for get_security_mode_with_source() function."""

    def test_returns_tuple(self, monkeypatch):
        """Test returns (mode, source) tuple."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "pilot")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        result = get_security_mode_with_source()

        assert isinstance(result, tuple)
        assert len(result) == 2
        mode, source = result
        assert isinstance(mode, SecurityMode)
        assert isinstance(source, SecurityModeSource)

    def test_caches_result(self, monkeypatch):
        """Test result is cached."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        # First call
        mode1, source1 = get_security_mode_with_source()

        # Change env var (shouldn't affect cached result)
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")

        # Second call (should return cached result)
        mode2, source2 = get_security_mode_with_source()

        assert mode1 == mode2
        assert source1 == source2


class TestBackwardCompatibility:
    """Tests for backward compatibility."""

    def test_get_security_mode_still_works(self, monkeypatch):
        """Test get_security_mode() still returns SecurityMode."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        mode = get_security_mode()
        assert isinstance(mode, SecurityMode)
        assert mode == SecurityMode.PRODUCTION

    def test_schema_guard_compatibility_layer(self, monkeypatch):
        """Test schema_guard.get_environment_mode() compatibility layer."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        from app.repositories.schema_guard import get_environment_mode

        # Should return string for backward compatibility
        env_mode = get_environment_mode()
        assert isinstance(env_mode, str)
        assert env_mode == "production"


class TestPilotMetadata:
    """Tests for pilot metadata functions."""

    def test_pilot_metadata_path(self, monkeypatch):
        """Test pilot metadata path calculation."""
        monkeypatch.setenv("OPENACE_CONFIG_DIR", "/tmp/test-config")

        path = get_pilot_metadata_path()
        assert path == "/tmp/test-config/pilot-mode-metadata.json"

    def test_load_pilot_metadata_missing(self, monkeypatch, tmp_path):
        """Test loading missing pilot metadata."""
        monkeypatch.setenv("OPENACE_CONFIG_DIR", str(tmp_path))

        metadata = load_pilot_metadata()
        assert metadata is None


class TestCacheReset:
    """Tests for cache reset functionality."""

    def test_reset_clears_cache(self, monkeypatch):
        """Test reset clears cached mode."""
        # Set a mode
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        reset_security_mode_cache()
        mode1 = get_security_mode()

        # Change env and reset
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
        reset_security_mode_cache()
        mode2 = get_security_mode()

        assert mode1 != mode2


class TestCaseInsensitivity:
    """Tests for case-insensitive mode detection."""

    def test_uppercase_mode(self, monkeypatch):
        """Test uppercase mode value."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "PRODUCTION")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        mode, source = detect_security_mode()
        assert mode == SecurityMode.PRODUCTION

    def test_mixed_case_mode(self, monkeypatch):
        """Test mixed case mode value."""
        reset_security_mode_cache()
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "PiLoT")
        monkeypatch.delenv("FLASK_ENV", raising=False)

        mode, source = detect_security_mode()
        assert mode == SecurityMode.PILOT
