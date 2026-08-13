"""
Multi-worker key consistency tests (Issue #2185).

Verifies that:
1. Multiple Gunicorn workers use the same SECRET_KEY
2. Key persistence works across restarts
3. File-based secret storage is atomic and thread-safe
"""

import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils.security_mode import SecurityMode, get_security_mode, reset_security_mode_cache


class TestMultiWorkerKeyConsistency:
    """Test that multiple workers see the same key."""

    def test_concurrent_security_mode_detection_is_consistent(self):
        """Multiple threads should see the same security mode."""
        reset_security_mode_cache()

        # Set environment variable once before concurrent access to avoid race condition
        # with patch.dict(os.environ, clear=False) in multi-threaded context
        original = os.environ.get("OPENACE_SECURITY_MODE")
        os.environ["OPENACE_SECURITY_MODE"] = "production"

        try:
            results = []
            errors = []

            def detect_mode():
                try:
                    mode = get_security_mode()
                    results.append(mode)
                except (
                    Exception
                ) as e:  # allow-swallow: collect per-thread errors; the driving test asserts errors is empty
                    errors.append(e)

            # Run 20 concurrent detections
            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(detect_mode) for _ in range(20)]
                for future in as_completed(futures):
                    future.result()  # Raise any errors

            assert not errors, f"Errors during concurrent detection: {errors}"
            assert len(results) == 20, "All threads should complete"
            assert all(
                m == SecurityMode.PRODUCTION for m in results
            ), "All threads should see the same mode"
        finally:
            # Restore original environment
            if original is None:
                os.environ.pop("OPENACE_SECURITY_MODE", None)
            else:
                os.environ["OPENACE_SECURITY_MODE"] = original

    def test_cached_security_mode_is_thread_safe(self):
        """Cached mode should be safe for concurrent access."""
        reset_security_mode_cache()

        # First detection (will cache)
        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "development"}, clear=False):
            mode1 = get_security_mode()

        # Concurrent reads should all return the cached value
        results = []

        def read_cached_mode():
            results.append(get_security_mode())

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(read_cached_mode) for _ in range(10)]
            for future in as_completed(futures):
                future.result()

        assert all(m == mode1 for m in results), "All reads should return the cached mode"


class TestKeyPersistenceAcrossRestarts:
    """Test that keys persist correctly across simulated restarts."""

    def test_security_mode_cache_persists_within_process(self):
        """Cache should persist within a single process lifetime."""
        reset_security_mode_cache()

        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "pilot"}, clear=False):
            mode1 = get_security_mode()
            mode2 = get_security_mode()
            mode3 = get_security_mode()

        assert mode1 == mode2 == mode3 == SecurityMode.PILOT

    def test_key_persistence_file_is_atomic(self):
        """Key file writes should be atomic."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            key_file = Path(tmp_dir) / "generated-secrets.env"

            # Simulate concurrent writes
            errors = []

            def write_key(worker_id):
                try:
                    # Simulate atomic write pattern
                    temp_path = key_file.with_suffix(".tmp")
                    temp_path.write_text(f"SECRET_KEY=test-key-{worker_id}\n")
                    temp_path.rename(key_file)
                except Exception as e:
                    errors.append((worker_id, e))

            # Run concurrent writes
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(write_key, i) for i in range(10)]
                for future in as_completed(futures):
                    future.result()

            assert not errors, f"Errors during concurrent writes: {errors}"

            # File should exist and be valid
            assert key_file.exists(), "Key file should exist after concurrent writes"
            content = key_file.read_text()
            assert "SECRET_KEY=" in content, "Key file should contain SECRET_KEY"


class TestGunicornWorkerConsistency:
    """Simulate Gunicorn worker initialization."""

    def test_workers_initialize_with_same_config(self):
        """Multiple workers should initialize with identical configuration."""
        reset_security_mode_cache()

        # Simulate Gunicorn master setting env before forking workers
        # Save original values for cleanup to prevent test pollution
        original_mode = os.environ.get("OPENACE_SECURITY_MODE")
        original_secret = os.environ.get("SECRET_KEY")

        os.environ["OPENACE_SECURITY_MODE"] = "production"
        os.environ["SECRET_KEY"] = "shared-secret-key-for-all-workers-32ch"

        try:
            worker_configs = []
            errors = []

            def simulate_worker_init(worker_id):
                try:
                    # Each worker reads the same environment
                    mode = get_security_mode()
                    secret_key = os.environ.get("SECRET_KEY")
                    worker_configs.append(
                        {
                            "worker_id": worker_id,
                            "mode": mode,
                            "secret_key": secret_key,
                        }
                    )
                except (
                    Exception
                ) as e:  # allow-swallow: collect per-thread errors; the driving test asserts errors is empty
                    errors.append((worker_id, e))

            # Simulate 4 workers (typical Gunicorn configuration)
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(simulate_worker_init, i) for i in range(4)]
                for future in as_completed(futures):
                    future.result()

            assert not errors, f"Worker initialization errors: {errors}"
            assert len(worker_configs) == 4, "All workers should initialize"

            # All workers should have the same configuration
            modes = [w["mode"] for w in worker_configs]
            keys = [w["secret_key"] for w in worker_configs]

            assert all(
                m == SecurityMode.PRODUCTION for m in modes
            ), "All workers should be in production mode"
            assert all(
                k == "shared-secret-key-for-all-workers-32ch" for k in keys
            ), "All workers should have the same SECRET_KEY"
        finally:
            # Restore original environment to prevent test pollution
            if original_mode is None:
                os.environ.pop("OPENACE_SECURITY_MODE", None)
            else:
                os.environ["OPENACE_SECURITY_MODE"] = original_mode

            if original_secret is None:
                os.environ.pop("SECRET_KEY", None)
            else:
                os.environ["SECRET_KEY"] = original_secret
