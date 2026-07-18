#!/usr/bin/env python3
"""
Open ACE - SSO Encryption Key Rotation Script

Issue #1815 Finding 1: CLI tool for rotating SSO provider encryption keys.

This script re-encrypts all SSO provider client_secrets with a new encryption key.

Usage:
    # Verify mode (dry-run, check if new key works)
    python scripts/rotate_sso_encryption.py --verify --new-key <NEW_KEY>

    # Execute mode (re-encrypt all providers)
    python scripts/rotate_sso_encryption.py --new-key <NEW_KEY>

Environment variables:
    OPENACE_ENCRYPTION_KEY: Current encryption key (required)
    OPENACE_SSO_ENCRYPTION_KEY: Override key for this script
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Add project root to path
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def get_current_encryption_key() -> str | None:
    """Get the current encryption key from environment."""
    return os.environ.get("OPENACE_ENCRYPTION_KEY")


def verify_key_works(db_url: str, new_key: str) -> tuple[bool, list[str]]:
    """Verify that the new key can decrypt all provider secrets.

    Args:
        db_url: Database URL.
        new_key: New encryption key to test.

    Returns:
        Tuple of (success, list of provider names that failed).
    """
    import tempfile

    from app.repositories.database import Database
    from app.repositories.schema_init import load_schema_from_file
    from app.utils.smtp_crypto import PasswordManager

    db = Database(db_url=db_url)

    # Create a temporary password manager with the new key
    pm = PasswordManager(encryption_key=new_key)

    # Get all providers
    rows = db.fetch_all("SELECT name, config FROM sso_providers")

    failed_providers = []
    success_count = 0

    for row in rows:
        name = row["name"]
        config_str = row["config"]

        try:
            config = json.loads(config_str)
            encrypted_secret = config.get("client_secret_encrypted", "")

            if encrypted_secret:
                # Try to decrypt with new key
                decrypted = pm.decrypt(encrypted_secret)
                if decrypted:
                    success_count += 1
                    logger.debug(f"Provider '{name}' can be decrypted with new key")
                else:
                    failed_providers.append(name)
                    logger.warning(f"Provider '{name}' returned empty decrypted value")
            else:
                # No encrypted secret, skip
                logger.debug(f"Provider '{name}' has no encrypted secret")

        except Exception as e:
            failed_providers.append(name)
            logger.warning(f"Provider '{name}' failed: {e}")

    success = len(failed_providers) == 0
    return success, failed_providers


def rotate_keys(db_url: str, new_key: str) -> tuple[bool, int, list[str]]:
    """Re-encrypt all provider secrets with the new key.

    Args:
        db_url: Database URL.
        new_key: New encryption key.

    Returns:
        Tuple of (success, count of re-encrypted providers, list of failed providers).
    """
    from app.repositories.database import Database
    from app.utils.smtp_crypto import PasswordManager

    db = Database(db_url=db_url)

    # Create password manager with new key
    pm_new = PasswordManager(encryption_key=new_key)

    # Also need old key to decrypt first
    old_key = get_current_encryption_key()
    if not old_key:
        logger.error("OPENACE_ENCRYPTION_KEY not set")
        return False, 0, []

    pm_old = PasswordManager(encryption_key=old_key)

    # Get all providers
    rows = db.fetch_all("SELECT name, config FROM sso_providers")

    re_encrypted_count = 0
    failed_providers = []

    for row in rows:
        name = row["name"]
        config_str = row["config"]

        try:
            config = json.loads(config_str)
            encrypted_secret = config.get("client_secret_encrypted", "")

            if encrypted_secret:
                # Decrypt with old key
                client_secret = pm_old.decrypt(encrypted_secret)

                if client_secret:
                    # Re-encrypt with new key
                    new_encrypted = pm_new.encrypt(client_secret)
                    config["client_secret_encrypted"] = new_encrypted

                    # Update database
                    new_config_str = json.dumps(config)
                    db.execute(
                        "UPDATE sso_providers SET config = ? WHERE name = ?",
                        (new_config_str, name),
                    )
                    re_encrypted_count += 1
                    logger.info(f"Re-encrypted provider '{name}'")
                else:
                    failed_providers.append(name)
                    logger.error(f"Failed to decrypt provider '{name}'")
            else:
                logger.debug(f"Provider '{name}' has no encrypted secret, skipping")

        except Exception as e:
            failed_providers.append(name)
            logger.error(f"Failed to re-encrypt provider '{name}': {e}")

    success = len(failed_providers) == 0
    return success, re_encrypted_count, failed_providers


def main():
    parser = argparse.ArgumentParser(
        description="Rotate SSO provider encryption keys (Issue #1815)"
    )
    parser.add_argument(
        "--new-key",
        required=True,
        help="New encryption key to use",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify mode: check if new key works without modifying data",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="Database URL (defaults to environment variable)",
    )

    args = parser.parse_args()

    # Check current key is set
    current_key = get_current_encryption_key()
    if not current_key:
        logger.error("OPENACE_ENCRYPTION_KEY environment variable not set")
        sys.exit(1)

    # Get database URL
    db_url = args.db_url or os.environ.get("DATABASE_URL") or "sqlite:///openace.db"

    logger.info(f"Using database: {db_url.split('@')[-1] if '@' in db_url else db_url}")

    if args.verify:
        logger.info("Running in VERIFY mode (dry-run)")
        success, failed = verify_key_works(db_url, args.new_key)

        if success:
            logger.info("✓ All providers can be decrypted with new key")
            logger.info("You can now run in execute mode to re-encrypt")
        else:
            logger.error(f"✗ {len(failed)} provider(s) failed verification: {failed}")
            sys.exit(1)
    else:
        logger.info("Running in EXECUTE mode")
        logger.warning("This will modify the database!")

        # First verify
        logger.info("Verifying new key...")
        verify_success, verify_failed = verify_key_works(db_url, args.new_key)

        if not verify_success:
            logger.error(f"Verification failed for providers: {verify_failed}")
            logger.error("Aborting. Run with --verify first to see details.")
            sys.exit(1)

        # Execute rotation
        success, count, failed = rotate_keys(db_url, args.new_key)

        if success:
            logger.info(f"✓ Successfully re-encrypted {count} provider(s)")
            logger.info("Update OPENACE_ENCRYPTION_KEY environment variable and restart the service")
        else:
            logger.error(f"✗ {len(failed)} provider(s) failed: {failed}")
            sys.exit(1)


if __name__ == "__main__":
    main()
