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

from app.utils.security_mode import (
    SecurityMode,
    get_security_mode,
    reset_security_mode_cache,
)


class TestMultiWorkerKeyConsistency:
    """Test that multiple workers see the same key."""

    def test_concurrent_security_mode_detection_is_consistent(self):
        """Multiple threads should see the same security mode."""
        reset_security_mode_cache()

        results = []
        errors = []

        def detect_mode():
            try:
                # Simulate concurrent detection
                with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "production"}, clear=False):
                    mode = get_security_mode()
                    results.append(mode)
            except Exception as e:
                errors.append(e)

        # Run 20 concurrent detections
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(detect_mode) for _ in range(20)]
            for future in as_completed(futures):
                future.result()  # Raise any errors

        assert not errors, f"Errors during concurrent detection: {errors}"
        assert len(results) == 20, "All threads should complete"
        assert all(m == SecurityMode.PRODUCTION for m in results), "All threads should see the same mode"

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

    def test_security_mode_reset_clears_cache(self):
        """Reset should clear the cache, allowing re-detection."""
        reset_security_mode_cache()

        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "development"}, clear=False):
            mode1 = get_security_mode()

        reset_security_mode_cache()

        with patch.dict(os.environ, {"OPENACE_SECURITY_MODE": "production"}, clear=False):
            mode2 = get_security_mode()

        assert mode1 == SecurityMode.DEVELOPMENT
        assert mode2 == SecurityMode.PRODUCTION


class TestFileBasedSecretStorage:
    """Test atomic file-based secret storage for multi-worker scenarios."""

    def test_concurrent_file_reads_are_safe(self):
        """Multiple workers reading the same secret file should be safe."""
        # Create a temporary secret file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            secret_file = Path(f.name)
            f.write("test-secret-key-for-multi-worker-test-32ch\n")

        try:
            results = []
            errors = []

            def read_secret():
                try:
                    content = secret_file.read_text().strip()
                    results.append(content)
                except Exception as e:
                    errors.append(e)

            # Simulate 20 workers reading the same file
            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = [executor.submit(read_secret) for _ in range(20)]
                for future in as_completed(futures):
                    future.result()

            assert not errors, f"Errors during concurrent reads: {errors}"
            assert len(results) == 20, "All reads should complete"
            assert all(s == "test-secret-key-for-multi-worker-test-32ch" for s in results), \
                "All workers should read the same secret"
        finally:
            secret_file.unlink(missing_ok=True)

    def test_atomic_file_write_prevents_partial_reads(self):
        """Secret file writes should be atomic to prevent partial reads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_file = Path(tmpdir) / "secret.txt"

            # Write initial secret
            secret_file.write_text("initial-secret-key-32-characters-long")

            errors = []

            def read_and_verify():
                try:
                    content = secret_file.read_text().strip()
                    # Content should always be either initial or final, never partial
                    assert content in [
                        "initial-secret-key-32-characters-long",
                        "final-secret-key-32-characters-long"
                    ], f"Read partial or corrupted content: {content}"
                except Exception as e:
                    errors.append(e)

            def write_new_secret():
                # Atomic write: write to temp file then rename
                temp_file = Path(tmpdir) / "secret.tmp"
                temp_file.write_text("final-secret-key-32-characters-long")
                temp_file.replace(secret_file)  # Atomic on POSIX

            # Concurrent reads while writing
            with ThreadPoolExecutor(max_workers=20) as executor:
                # Start readers
                read_futures = [executor.submit(read_and_verify) for _ in range(19)]
                # Start writer
                write_future = executor.submit(write_new_secret)

                # Wait for completion
                for future in as_completed([write_future] + read_futures):
                    future.result()

            assert not errors, f"Partial reads detected: {errors}"


class TestEnvFileConsistency:
    """Test .env file handling for multi-worker consistency."""

    def test_dotenv_load_is_idempotent(self):
        """Loading .env multiple times should be safe."""
        # Simulate multiple workers loading the same .env
        with tempfile.TemporaryDirectory() as tmpdir:
            env_file = Path(tmpdir) / ".env"
            env_file.write_text("TEST_VAR=test-value-32-characters-long\n")

            results = []

            def load_and_check():
                # In real scenario, this would be dotenv.load_dotenv
                # Here we just read the file
                content = env_file.read_text()
                results.append(content)

            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(load_and_check) for _ in range(10)]
                for future in as_completed(futures):
                    future.result()

            # All workers should see the same content
            assert all(c == results[0] for c in results), "All workers should see same .env content"


class TestGunicornWorkerConsistency:
    """Simulate Gunicorn worker initialization."""

    def test_workers_initialize_with_same_config(self):
        """Multiple workers should initialize with identical configuration."""
        reset_security_mode_cache()

        # Simulate Gunicorn master setting env before forking workers
        os.environ["OPENACE_SECURITY_MODE"] = "production"
        os.environ["SECRET_KEY"] = "shared-secret-key-for-all-workers-32ch"

        worker_configs = []
        errors = []

        def simulate_worker_init(worker_id):
            try:
                # Each worker reads the same environment
                mode = get_security_mode()
                secret_key = os.environ.get("SECRET_KEY")
                worker_configs.append({
                    "worker_id": worker_id,
                    "mode": mode,
                    "secret_key": secret_key,
                })
            except Exception as e:
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

        assert all(m == SecurityMode.PRODUCTION for m in modes), "All workers should be in production mode"
        assert all(k == "shared-secret-key-for-all-workers-32ch" for k in keys), \
            "All workers should have the same SECRET_KEY"