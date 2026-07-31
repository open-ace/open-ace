#!/usr/bin/env python3
"""
Encryption Key Validation CLI Tool

Validates encryption key configuration and strength.

Usage:
    python -m app.tools.validate_encryption_keys [--json]

Issue: #1820
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

logger = logging.getLogger(__name__)


def validate_encryption_keys() -> dict:
    """
    Validate encryption key configuration.

    Returns:
        Dict with validation results.
    """
    results = {
        "valid": True,
        "errors": [],
        "warnings": [],
        "info": {},
    }

    # Check EncryptionKeyRegistry
    try:
        from app.utils.encryption_key_registry import get_registry, reset_registry

        # Reset to ensure fresh load
        reset_registry()
        registry = get_registry()

        results["info"]["registry"] = {
            "key_count": registry.get_key_count(),
            "primary_key_id": registry.get_primary_key_id(),
            "active_key_count": registry.get_active_key_count(),
            "config_version": registry.get_config_version(),
        }

        # Test encryption/decryption roundtrip
        test_plaintext = "validation_test_secret"
        ciphertext = registry.encrypt(test_plaintext)
        result = registry.decrypt(ciphertext)

        if result is None:
            results["valid"] = False
            results["errors"].append("Encryption/decryption roundtrip failed")
        else:
            decrypted, key_id = result
            if decrypted != test_plaintext:
                results["valid"] = False
                results["errors"].append(
                    f"Encryption/decryption mismatch: expected '{test_plaintext}', got '{decrypted}'"
                )
            else:
                results["info"]["roundtrip"] = {
                    "success": True,
                    "key_id": key_id,
                }

    except Exception as e:
        results["valid"] = False
        results["errors"].append(f"EncryptionKeyRegistry initialization failed: {e}")

    # Check secret strength
    try:
        import os

        from app.utils.security_env import is_strict_mode, is_weak_secret_value

        results["info"]["mode"] = "strict" if is_strict_mode() else "development"

        # Check SECRET_KEY
        secret_key = os.environ.get("SECRET_KEY")
        if secret_key:
            if is_weak_secret_value(secret_key):
                results["warnings"].append("SECRET_KEY uses a weak value")
            elif len(secret_key) < 32:
                results["warnings"].append(
                    f"SECRET_KEY is shorter than recommended 32 chars (got {len(secret_key)})"
                )
            else:
                results["info"]["secret_key"] = {
                    "strength": "strong",
                    "length": len(secret_key),
                }
        else:
            results["warnings"].append("SECRET_KEY not set (using development default)")

        # Check OPENACE_ENCRYPTION_KEY
        encryption_key = os.environ.get("OPENACE_ENCRYPTION_KEY")
        if encryption_key:
            if is_weak_secret_value(encryption_key):
                results["warnings"].append("OPENACE_ENCRYPTION_KEY uses a weak value")
            elif len(encryption_key) < 32:
                results["warnings"].append(
                    f"OPENACE_ENCRYPTION_KEY is shorter than recommended 32 chars (got {len(encryption_key)})"
                )
            else:
                results["info"]["encryption_key"] = {
                    "strength": "strong",
                    "length": len(encryption_key),
                }
        else:
            results["warnings"].append("OPENACE_ENCRYPTION_KEY not set (using development default)")

    except Exception as e:
        results["warnings"].append(f"Secret strength validation error: {e}")

    return results


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Validate encryption key configuration")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    # Validate
    results = validate_encryption_keys()

    # Output
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print("\n=== Encryption Key Validation ===\n")

        if results["valid"]:
            print("✓ Status: VALID\n")
        else:
            print("✗ Status: INVALID\n")

        if results.get("info"):
            print("Information:")
            for key, value in results["info"].items():
                print(f"  {key}: {value}")
            print()

        if results.get("warnings"):
            print("Warnings:")
            for warning in results["warnings"]:
                print(f"  ⚠ {warning}")
            print()

        if results.get("errors"):
            print("Errors:")
            for error in results["errors"]:
                print(f"  ✗ {error}")
            print()

    # Exit code
    sys.exit(0 if results["valid"] else 1)


if __name__ == "__main__":
    main()
