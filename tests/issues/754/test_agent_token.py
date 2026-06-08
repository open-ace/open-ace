"""Unit tests for agent token utilities (Issue #754).

Tests the core token generation, hashing, and validation functions
in app.modules.workspace.agent_token.
"""

import hashlib
import hmac
import unittest


class TestAgentTokenUtilities(unittest.TestCase):
    """Tests for agent_token module functions."""

    def test_generate_agent_token_returns_64_char_hex(self):
        """Generated token should be a 64-char hex string (256 bits)."""
        from app.modules.workspace.agent_token import generate_agent_token

        token = generate_agent_token()
        self.assertEqual(len(token), 64)
        # Verify it's valid hex
        int(token, 16)

    def test_generate_agent_token_unique(self):
        """Two calls should produce different tokens."""
        from app.modules.workspace.agent_token import generate_agent_token

        t1 = generate_agent_token()
        t2 = generate_agent_token()
        self.assertNotEqual(t1, t2)

    def test_hash_agent_token_deterministic(self):
        """Same input should always produce the same hash."""
        from app.modules.workspace.agent_token import hash_agent_token

        token = "abcdef1234567890" * 4
        h1 = hash_agent_token(token)
        h2 = hash_agent_token(token)
        self.assertEqual(h1, h2)

    def test_hash_agent_token_matches_sha256(self):
        """Hash should match manual SHA-256 computation."""
        from app.modules.workspace.agent_token import hash_agent_token

        token = "test_token_value"
        expected = hashlib.sha256(token.encode()).hexdigest()
        self.assertEqual(hash_agent_token(token), expected)

    def test_hash_agent_token_different_inputs(self):
        """Different inputs should produce different hashes."""
        from app.modules.workspace.agent_token import hash_agent_token

        self.assertNotEqual(hash_agent_token("token_a"), hash_agent_token("token_b"))

    def test_validate_agent_token_hash_correct(self):
        """Validation should return True for correct plaintext."""
        from app.modules.workspace.agent_token import (
            generate_agent_token,
            hash_agent_token,
            validate_agent_token_hash,
        )

        token = generate_agent_token()
        token_hash = hash_agent_token(token)
        self.assertTrue(validate_agent_token_hash(token_hash, token))

    def test_validate_agent_token_hash_wrong(self):
        """Validation should return False for wrong plaintext."""
        from app.modules.workspace.agent_token import (
            generate_agent_token,
            hash_agent_token,
            validate_agent_token_hash,
        )

        token = generate_agent_token()
        wrong_token = generate_agent_token()
        token_hash = hash_agent_token(token)
        self.assertFalse(validate_agent_token_hash(token_hash, wrong_token))


if __name__ == "__main__":
    unittest.main()
