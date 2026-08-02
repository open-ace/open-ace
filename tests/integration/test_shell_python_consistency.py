"""
Shell/Python consistency tests (Issue #2185).

Verifies that docker-entrypoint.sh security mode detection
matches the Python implementation in security_mode.py.
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils.security_mode import (
    SecurityMode,
    detect_security_mode,
    reset_security_mode_cache,
)


class TestShellPythonConsistency:
    """Test Shell and Python security mode detection are consistent."""

    def get_shell_security_mode(self, env_vars: dict[str, str]) -> str:
        """
        Run docker-entrypoint.sh detect_security_mode function and return result.

        Args:
            env_vars: Environment variables to set

        Returns:
            Security mode detected by shell script
        """
        # Path to docker-entrypoint.sh (in repo root)
        entrypoint = Path(__file__).resolve().parents[2] / "docker-entrypoint.sh"

        if not entrypoint.exists():
            pytest.skip("docker-entrypoint.sh not found")

        # Build the shell command to source the script and call the function
        # We extract just the detect_security_mode function and call it
        script = f"""
set -e
# Source the detect_security_mode function
source <(sed -n '/^detect_security_mode()/,/^}}/p' {entrypoint})

# Set environment variables
{os.linesep.join(f'export {k}="{v}"' for k, v in env_vars.items())}

# Call the function and print result
detect_security_mode
"""

        try:
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            pytest.fail("Shell script timed out")
        except Exception as e:
            pytest.fail(f"Shell script failed: {e}")

    def test_production_mode_consistency(self):
        """OPENACE_SECURITY_MODE=production should be consistent."""
        reset_security_mode_cache()

        env = {"OPENACE_SECURITY_MODE": "production"}

        # Python detection
        with patch.dict(os.environ, env, clear=False):
            python_mode = detect_security_mode()

        # Shell detection
        shell_mode = self.get_shell_security_mode(env)

        assert python_mode == SecurityMode.PRODUCTION
        assert shell_mode == "production"

    def test_pilot_mode_consistency(self):
        """OPENACE_SECURITY_MODE=pilot should be consistent."""
        reset_security_mode_cache()

        env = {"OPENACE_SECURITY_MODE": "pilot"}

        # Python detection
        with patch.dict(os.environ, env, clear=False):
            python_mode = detect_security_mode()

        # Shell detection
        shell_mode = self.get_shell_security_mode(env)

        assert python_mode == SecurityMode.PILOT
        assert shell_mode == "pilot"

    def test_development_mode_consistency(self):
        """OPENACE_SECURITY_MODE=development should be consistent."""
        reset_security_mode_cache()

        env = {"OPENACE_SECURITY_MODE": "development"}

        # Python detection
        with patch.dict(os.environ, env, clear=False):
            python_mode = detect_security_mode()

        # Shell detection
        shell_mode = self.get_shell_security_mode(env)

        assert python_mode == SecurityMode.DEVELOPMENT
        assert shell_mode == "development"

    def test_flask_env_backward_compat_consistency(self):
        """FLASK_ENV=production should be consistent (backward compat)."""
        reset_security_mode_cache()

        env = {
            "FLASK_ENV": "production",
            "OPENACE_SECURITY_MODE": "",  # Not set
        }

        # Python detection
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("OPENACE_SECURITY_MODE", None)
            python_mode = detect_security_mode()

        # Shell detection
        shell_mode = self.get_shell_security_mode(env)

        assert python_mode == SecurityMode.PRODUCTION
        assert shell_mode == "production"

    def test_priority_order_consistency(self):
        """OPENACE_SECURITY_MODE should take precedence over FLASK_ENV."""
        reset_security_mode_cache()

        env = {
            "OPENACE_SECURITY_MODE": "pilot",
            "FLASK_ENV": "production",  # Should be ignored
        }

        # Python detection
        with patch.dict(os.environ, env, clear=False):
            python_mode = detect_security_mode()

        # Shell detection
        shell_mode = self.get_shell_security_mode(env)

        assert python_mode == SecurityMode.PILOT
        assert shell_mode == "pilot"


class TestEntrypointSecurityBaseline:
    """Test docker-entrypoint.sh security baseline checks."""

    def test_entrypoint_exists(self):
        """docker-entrypoint.sh should exist."""
        entrypoint = Path(__file__).resolve().parents[2] / "docker-entrypoint.sh"
        assert entrypoint.exists(), "docker-entrypoint.sh should exist"
        assert entrypoint.is_file(), "docker-entrypoint.sh should be a file"

    def test_entrypoint_has_security_mode_detection(self):
        """docker-entrypoint.sh should have detect_security_mode function."""
        entrypoint = Path(__file__).resolve().parents[2] / "docker-entrypoint.sh"
        content = entrypoint.read_text()

        assert "detect_security_mode()" in content, "Should have detect_security_mode function"
        assert "OPENACE_SECURITY_MODE" in content, "Should check OPENACE_SECURITY_MODE"
        assert "FLASK_ENV" in content, "Should check FLASK_ENV for backward compat"

    def test_entrypoint_has_security_baseline_check(self):
        """docker-entrypoint.sh should have security baseline check."""
        entrypoint = Path(__file__).resolve().parents[2] / "docker-entrypoint.sh"
        content = entrypoint.read_text()

        assert "check_security_baseline" in content, "Should have check_security_baseline function"
        assert "SECRET_KEY" in content, "Should check SECRET_KEY"
        assert "OPENACE_ENCRYPTION_KEY" in content, "Should check OPENACE_ENCRYPTION_KEY"
        assert "DB_PASSWORD" in content, "Should check DB_PASSWORD"

    def test_entrypoint_enforces_production_requirements(self):
        """docker-entrypoint.sh should enforce production requirements."""
        entrypoint = Path(__file__).resolve().parents[2] / "docker-entrypoint.sh"
        content = entrypoint.read_text()

        # Should have production-specific checks
        assert "production)" in content, "Should have production case"
        assert "required in production" in content or "required in production mode" in content, \
            "Should enforce production requirements"


class TestSecretPersistenceMechanism:
    """Test that secret persistence works consistently."""

    def test_entrypoint_has_secret_persistence(self):
        """docker-entrypoint.sh should persist generated secrets."""
        entrypoint = Path(__file__).resolve().parents[2] / "docker-entrypoint.sh"
        content = entrypoint.read_text()

        assert "ensure_secret_env" in content, "Should have ensure_secret_env function"
        assert "generated-secrets.env" in content, "Should persist to generated-secrets.env"
        assert "secrets.token_hex" in content, "Should generate strong random secrets"

    def test_entrypoint_does_not_overwrite_explicit_secrets(self):
        """docker-entrypoint.sh should not overwrite explicit secrets."""
        entrypoint = Path(__file__).resolve().parents[2] / "docker-entrypoint.sh"
        content = entrypoint.read_text()

        # Check for logic that preserves existing secrets
        assert "not os.environ.get" in content or "not set" in content, \
            "Should check if secret is already set"
        assert "operator set explicitly" in content or "env to override" in content, \
            "Should mention that explicit env vars take precedence"