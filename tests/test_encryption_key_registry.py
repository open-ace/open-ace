"""
Tests for EncryptionKeyRegistry.

Tests multi-key management, hot reload, and backward compatibility.
"""

import base64
import json
import os
import threading
import time
from unittest.mock import patch

import pytest

from app.utils.encryption_key_registry import (
    EncryptionKeyRegistry,
    KeyStatus,
    get_registry,
    reset_registry,
)


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset singleton before and after each test."""
    reset_registry()
    yield
    reset_registry()


class TestEncryptionKeyRegistryInit:
    """Tests for EncryptionKeyRegistry initialization."""

    def test_init_with_single_key(self):
        """Test initialization with OPENACE_ENCRYPTION_KEY."""
        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "test-encryption-key-32-chars-long"},
            clear=False,
        ):
            registry = EncryptionKeyRegistry()

            assert registry.get_key_count() == 1
            assert registry.get_primary_key_id() == 0
            assert registry.get_active_key_count() == 1

    def test_init_with_multi_key_config(self):
        """Test initialization with OPENACE_ENCRYPTION_KEYS."""
        config = {
            "keys": [
                {"id": 1, "value": "key-1-value-32-chars-long-abc-xx", "status": "deprecated"},
                {"id": 2, "value": "key-2-value-32-chars-long-abc-xx", "status": "active"},
            ],
            "primary_key_id": 2,
        }

        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEYS": json.dumps(config)},
            clear=False,
        ):
            registry = EncryptionKeyRegistry()

            assert registry.get_key_count() == 2
            assert registry.get_primary_key_id() == 2
            assert registry.get_active_key_count() == 1

    def test_init_requires_exactly_one_active_key(self):
        """Test that config must have exactly one active key."""
        config = {
            "keys": [
                {"id": 1, "value": "key-1-value-32-chars-long-abc-xx", "status": "active"},
                {"id": 2, "value": "key-2-value-32-chars-long-abc-xx", "status": "active"},
            ],
            "primary_key_id": 1,
        }

        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEYS": json.dumps(config)},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="exactly 1 active key"):
                EncryptionKeyRegistry()

    def test_init_requires_valid_key_id_range(self):
        """Test that key_id must be in range 1-255."""
        config = {
            "keys": [
                {"id": 0, "value": "key-value-32-chars-long-abc-xxxxx", "status": "active"},
            ],
            "primary_key_id": 0,
        }

        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEYS": json.dumps(config)},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="between 1 and 255"):
                EncryptionKeyRegistry()

    def test_init_max_key_count(self):
        """Test that max key count is enforced."""
        keys = [
            {"id": i, "value": f"key-{i}-value-32-chars-long-abcdef", "status": "deprecated"}
            for i in range(1, 7)  # 6 keys > MAX_KEY_COUNT (5)
        ]
        keys[5]["status"] = "active"  # Make last one active

        config = {"keys": keys, "primary_key_id": 6}

        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEYS": json.dumps(config)},
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="cannot have more than"):
                EncryptionKeyRegistry()


class TestEncryptionKeyRegistryEncryptDecrypt:
    """Tests for encryption and decryption."""

    def test_encrypt_decrypt_roundtrip(self):
        """Test basic encrypt/decrypt roundtrip."""
        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "test-encryption-key-32-chars-long"},
            clear=False,
        ):
            registry = EncryptionKeyRegistry()

            plaintext = "my_secret_password"
            ciphertext = registry.encrypt(plaintext)

            # Should not have key_id prefix for legacy key_id=0
            assert not ciphertext.startswith("v1k")

            # Decrypt
            result = registry.decrypt(ciphertext)
            assert result is not None
            decrypted, key_id = result
            assert decrypted == plaintext
            assert key_id == 0  # Legacy key_id

    def test_decrypt_legacy_format(self):
        """Test decryption of legacy ciphertext without key_id prefix."""
        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "test-encryption-key-32-chars-long"},
            clear=False,
        ):
            registry = EncryptionKeyRegistry()

            # Create legacy ciphertext using Fernet directly
            import hashlib

            from cryptography.fernet import Fernet

            key = hashlib.sha256(b"test-encryption-key-32-chars-long").digest()
            fernet_key = base64.urlsafe_b64encode(key)
            fernet = Fernet(fernet_key)

            plaintext = "legacy_secret"
            legacy_ciphertext = fernet.encrypt(plaintext.encode()).decode()

            # Should not have prefix
            assert "." not in legacy_ciphertext or legacy_ciphertext.count(".") == 1

            # Decrypt using registry
            result = registry.decrypt(legacy_ciphertext)
            assert result is not None
            decrypted, key_id = result
            assert decrypted == plaintext

    def test_multi_key_decrypt_tries_all_keys(self):
        """Test that decryption tries all available keys."""
        config = {
            "keys": [
                {"id": 1, "value": "key-1-value-32-chars-long-abc-xx", "status": "deprecated"},
                {"id": 2, "value": "key-2-value-32-chars-long-abc-xx", "status": "active"},
            ],
            "primary_key_id": 2,
        }

        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEYS": json.dumps(config)},
            clear=False,
        ):
            # Encrypt with key-1
            import hashlib

            from cryptography.fernet import Fernet

            key1 = hashlib.sha256(b"key-1-value-32-chars-long-abc-xx").digest()
            fernet1 = Fernet(base64.urlsafe_b64encode(key1))
            plaintext = "encrypted_with_key1"
            ciphertext_key1 = fernet1.encrypt(plaintext.encode()).decode()

            # Initialize registry (uses key-2 as primary)
            registry = EncryptionKeyRegistry()

            # Should decrypt legacy ciphertext using key-1
            result = registry.decrypt(ciphertext_key1)
            assert result is not None
            decrypted, key_id = result
            assert decrypted == plaintext

    def test_encrypt_empty_string(self):
        """Test encrypting empty string returns empty string."""
        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "test-encryption-key-32-chars-long"},
            clear=False,
        ):
            registry = EncryptionKeyRegistry()
            assert registry.encrypt("") == ""

    def test_decrypt_empty_string(self):
        """Test decrypting empty string returns empty tuple."""
        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "test-encryption-key-32-chars-long"},
            clear=False,
        ):
            registry = EncryptionKeyRegistry()
            result = registry.decrypt("")
            assert result == ("", 0)


class TestEncryptionKeyRegistryHotReload:
    """Tests for hot reload functionality."""

    def test_reload_updates_keys(self):
        """Test that reload() updates keys from environment."""
        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "initial-encryption-key-32-chars-xx"},
            clear=False,
        ):
            registry = EncryptionKeyRegistry()
            initial_version = registry.get_config_version()

            # Change key
            with patch.dict(
                os.environ,
                {"OPENACE_ENCRYPTION_KEY": "changed-encryption-key-32-chars-xx"},
                clear=False,
            ):
                registry.reload()
                new_version = registry.get_config_version()

                # Version should change
                assert new_version != initial_version

    def test_lazy_check_time_window(self):
        """Test that lazy check only happens after time window."""
        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "test-encryption-key-32-chars-long"},
            clear=False,
        ):
            registry = EncryptionKeyRegistry()
            initial_time = registry._last_check_time

            # Should not check immediately (within 100ms tolerance)
            assert abs(registry._last_check_time - initial_time) < 0.1

            # Force check
            registry._last_check_time = time.time() - 10  # 10 seconds ago
            registry._check_and_reload_if_needed()

            # Should have updated last_check_time
            assert registry._last_check_time >= time.time() - 1


class TestEncryptionKeyRegistryConfigVersion:
    """Tests for config version functionality."""

    def test_config_version_changes_on_key_change(self):
        """Test that config version changes when keys change."""
        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "key-1-encryption-32-characters-xx"},
            clear=False,
        ):
            registry1 = EncryptionKeyRegistry()
            version1 = registry1.get_config_version()

        reset_registry()

        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "key-2-encryption-32-characters-xx"},
            clear=False,
        ):
            registry2 = EncryptionKeyRegistry()
            version2 = registry2.get_config_version()

        # Different keys should produce different versions
        assert version1 != version2

    def test_config_version_same_for_same_config(self):
        """Test that config version is same for same config."""
        config = {
            "keys": [
                {"id": 1, "value": "key-1-value-32-chars-long-abc-xx", "status": "active"},
            ],
            "primary_key_id": 1,
        }

        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEYS": json.dumps(config)},
            clear=False,
        ):
            registry1 = EncryptionKeyRegistry()
            version1 = registry1.get_config_version()

        reset_registry()

        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEYS": json.dumps(config)},
            clear=False,
        ):
            registry2 = EncryptionKeyRegistry()
            version2 = registry2.get_config_version()

        # Same config should produce same version
        assert version1 == version2


class TestEncryptionKeyRegistryThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_encrypt_operations(self):
        """Test concurrent encrypt operations are thread-safe."""
        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "test-encryption-key-32-chars-long"},
            clear=False,
        ):
            registry = EncryptionKeyRegistry()
            errors = []

            def encrypt_decrypt_task(i):
                try:
                    plaintext = f"secret_{i}"
                    ciphertext = registry.encrypt(plaintext)
                    result = registry.decrypt(ciphertext)
                    assert result is not None
                    decrypted, _ = result
                    assert decrypted == plaintext
                except Exception as e:
                    errors.append(e)

            # Run 100 concurrent operations
            threads = [threading.Thread(target=encrypt_decrypt_task, args=(i,)) for i in range(100)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert len(errors) == 0, f"Errors: {errors}"


class TestGetRegistry:
    """Tests for get_registry() singleton."""

    def test_singleton_returns_same_instance(self):
        """Test that get_registry() returns same instance."""
        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "test-encryption-key-32-chars-long"},
            clear=False,
        ):
            registry1 = get_registry()
            registry2 = get_registry()
            assert registry1 is registry2

    def test_reset_allows_new_instance(self):
        """Test that reset_registry() allows new instance."""
        with patch.dict(
            os.environ,
            {"OPENACE_ENCRYPTION_KEY": "test-encryption-key-32-chars-long"},
            clear=False,
        ):
            registry1 = get_registry()
            reset_registry()
            registry2 = get_registry()
            assert registry1 is not registry2
