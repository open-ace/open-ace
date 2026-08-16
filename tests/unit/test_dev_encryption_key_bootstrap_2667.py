"""#2667: dev-mode bootstrap for OPENACE_ENCRYPTION_KEY (python3 server.py).

docker-entrypoint.sh generates and persists OPENACE_ENCRYPTION_KEY so the
Docker path works zero-config; the direct ``python3 server.py`` path had no
equivalent, leaving APIKeyProxyService to raise RuntimeError at first use —
a failure the /api/autonomous/models route used to mask as "no models
configured".

``ensure_generated_encryption_key`` (called from server.py at module level)
mirrors the entrypoint contract:
- explicit env always wins and is never replaced;
- a previously generated key is reloaded from generated-secrets.env so
  ciphertext stays decryptable across restarts;
- a fresh key is generated AND persisted; if persistence fails the env is
  left unset so the misconfiguration stays loud instead of silently changing
  the key on every restart;
- production never auto-generates.
"""

from __future__ import annotations

import os
import re
import stat
import unittest.mock as unittest_mock
from pathlib import Path

import pytest

from app.utils.security_env import ensure_generated_encryption_key
from app.utils.security_mode import reset_security_mode_cache

pytestmark = [pytest.mark.regression, pytest.mark.issue(2667)]

SECRET_FILE_RE = re.compile(r"^OPENACE_ENCRYPTION_KEY='([0-9a-f]{32})'$")


@pytest.fixture(autouse=True)
def isolated_secrets_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point the secrets file at a tmp dir, dev mode, no key in env."""
    monkeypatch.setenv("OPENACE_CONFIG_DIR", str(tmp_path / "open-ace"))
    monkeypatch.delenv("OPENACE_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
    reset_security_mode_cache()
    yield
    reset_security_mode_cache()


class TestDevelopmentMode:
    def test_generates_and_persists_key(self, tmp_path: Path):
        key = ensure_generated_encryption_key()

        assert key is not None
        assert SECRET_FILE_RE.match(f"OPENACE_ENCRYPTION_KEY='{key}'")
        assert os.environ["OPENACE_ENCRYPTION_KEY"] == key

        secrets_file = tmp_path / "open-ace" / "generated-secrets.env"
        content = secrets_file.read_text(encoding="utf-8")
        assert f"OPENACE_ENCRYPTION_KEY='{key}'" in content
        # Owner-only, like the entrypoint's chmod 600.
        mode = stat.S_IMODE(secrets_file.stat().st_mode) & 0o777
        assert mode == 0o600

    def test_persisted_key_is_reloaded_not_regenerated(self, tmp_path: Path):
        first = ensure_generated_encryption_key()
        content_before = (tmp_path / "open-ace" / "generated-secrets.env").read_text(
            encoding="utf-8"
        )

        # Simulate a restart: env gone, file still there.
        del os.environ["OPENACE_ENCRYPTION_KEY"]
        second = ensure_generated_encryption_key()

        assert second == first
        assert os.environ["OPENACE_ENCRYPTION_KEY"] == first
        # File untouched by the reload.
        assert (tmp_path / "open-ace" / "generated-secrets.env").read_text(
            encoding="utf-8"
        ) == content_before

    def test_rejected_formats_in_file_trigger_regeneration(self, tmp_path: Path):
        secrets_dir = tmp_path / "open-ace"
        secrets_dir.mkdir()
        (secrets_dir / "generated-secrets.env").write_text(
            "OPENACE_ENCRYPTION_KEY=not-our-format\n",
            encoding="utf-8",
        )

        key = ensure_generated_encryption_key()

        # The non-hex line is ignored (never evaluated), so a fresh key is
        # generated and the stale assignment line is replaced idempotently.
        assert key is not None
        content = (secrets_dir / "generated-secrets.env").read_text(encoding="utf-8")
        assert content == f"OPENACE_ENCRYPTION_KEY='{key}'\n"

    def test_other_secrets_in_file_are_preserved(self, tmp_path: Path):
        secrets_dir = tmp_path / "open-ace"
        secrets_dir.mkdir()
        (secrets_dir / "generated-secrets.env").write_text(
            "UPLOAD_AUTH_KEY='aabbccdd'\n",
            encoding="utf-8",
        )

        key = ensure_generated_encryption_key()

        assert key is not None
        content = (secrets_dir / "generated-secrets.env").read_text(encoding="utf-8")
        assert "UPLOAD_AUTH_KEY='aabbccdd'" in content
        assert f"OPENACE_ENCRYPTION_KEY='{key}'" in content

    def test_explicit_env_value_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # setenv (not a bare os.environ write) so the value is rolled back
        # after the test — a leaked key would poison every later test in the
        # same process that expects the key unset.
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "e" * 64)

        key = ensure_generated_encryption_key()

        assert key == "e" * 64
        # Never touched on disk when the operator provided the value.
        assert not (tmp_path / "open-ace" / "generated-secrets.env").exists()


class TestFailureAndProduction:
    def test_production_mode_never_auto_generates(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPENACE_SECURITY_MODE", "production")
        reset_security_mode_cache()

        key = ensure_generated_encryption_key()

        assert key is None
        assert "OPENACE_ENCRYPTION_KEY" not in os.environ

    def test_mode_detection_failure_skips_bootstrap(self, monkeypatch: pytest.MonkeyPatch):
        """Issue #2667 M1: get_security_mode() raising must not crash startup.

        detect_security_mode() raises RuntimeError in production-capable
        environments with no explicit OPENACE_SECURITY_MODE. server.py calls
        this helper at module level BEFORE create_app — letting the error
        propagate would move the crash point (and its message) ahead of
        create_app's own validation. Fail-safe: skip generation; create_app
        still fails with its pre-existing error.
        """
        monkeypatch.delenv("OPENACE_SECURITY_MODE", raising=False)
        with unittest_mock.patch(
            "app.utils.security_env.get_security_mode",
            side_effect=RuntimeError("OPENACE_SECURITY_MODE must be set explicitly in production"),
        ):
            # Must not raise.
            key = ensure_generated_encryption_key()

        assert key is None
        assert "OPENACE_ENCRYPTION_KEY" not in os.environ

    def test_persistence_failure_leaves_env_unset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # OPENACE_CONFIG_DIR pointing at a regular file: makedirs fails.
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("occupied", encoding="utf-8")
        monkeypatch.setenv("OPENACE_CONFIG_DIR", str(blocker))
        reset_security_mode_cache()

        # Must not raise, and must NOT run with an unpersisted key — a fresh
        # random key on every restart would silently break all ciphertext.
        key = ensure_generated_encryption_key()

        assert key is None
        assert "OPENACE_ENCRYPTION_KEY" not in os.environ


class TestServerWiring:
    def test_server_py_calls_bootstrap(self):
        """Guard the wiring: server.py must invoke the helper at startup.

        Importing server.py in a test would gevent-monkey-patch the whole
        pytest process, so assert on source instead.
        """
        server_py = Path(__file__).resolve().parents[2] / "server.py"
        source = server_py.read_text(encoding="utf-8")
        assert "ensure_generated_encryption_key" in source
        assert "ensure_generated_encryption_key()" in source

    def test_server_py_bootstrap_ordering_invariant(self):
        """Issue #2667 R1: the bootstrap call must run AFTER the
        OPENACE_SECURITY_MODE development fallback and BEFORE create_app().

        get_security_mode() caches its first result process-wide; calling the
        bootstrap earlier (e.g. next to the config.json SECRET_KEY load)
        would pin a mode computed before the fallback env var is set,
        diverging from what create_app later resolves in prod-capable
        environments.
        """
        server_py = Path(__file__).resolve().parents[2] / "server.py"
        source = server_py.read_text(encoding="utf-8")

        fallback_pos = source.index('os.environ["OPENACE_SECURITY_MODE"] = "development"')
        bootstrap_pos = source.index("ensure_generated_encryption_key()")
        create_app_pos = source.index("from app import create_app")

        assert fallback_pos < bootstrap_pos < create_app_pos, (
            "server.py ordering invariant violated: the generated-key "
            "bootstrap must sit between the security-mode fallback and "
            "create_app (Issue #2667 R1)."
        )
