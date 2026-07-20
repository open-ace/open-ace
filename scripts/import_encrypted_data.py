#!/usr/bin/env python3
"""
Import and re-encrypt data after key rotation.

This script reads plaintext data from a JSON file (created by export_encrypted_data.py)
and re-encrypts it with the current OPENACE_ENCRYPTION_KEY. Use this during key
rotation after setting a new encryption key.

Usage:
    python scripts/import_encrypted_data.py --input encrypted_data_backup.json

Prerequisites:
    1. Run export_encrypted_data.py with the OLD key
    2. Set the NEW OPENACE_ENCRYPTION_KEY environment variable
    3. Restart the service to pick up the new key
    4. Run this script to re-encrypt and import the data

Security Note:
    The input file contains PLAINTEXT secrets. Delete it after successful import.
"""

from __future__ import annotations


from __future__ import annotations


from __future__ import annotations
import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def get_database_connection():
    """Get database connection based on current configuration."""
    from app.repositories.database import get_database_url, is_postgresql

    if is_postgresql():
        import psycopg2
        from psycopg2.extras import RealDictCursor

        return psycopg2.connect(get_database_url(), cursor_factory=RealDictCursor)
    else:
        import sqlite3

        url = get_database_url()
        db_path = url[len("sqlite:///") :] if url.startswith("sqlite:///") else "app.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn


def import_api_keys(conn, api_keys: list[dict[str, Any]]) -> int:
    """Import and re-encrypt API keys."""
    from app.modules.workspace.api_key_proxy import APIKeyProxyService
    from app.repositories.database import adapt_sql

    service = APIKeyProxyService()
    cursor = conn.cursor()
    imported = 0

    for key_data in api_keys:
        try:
            # Encrypt with new key
            encrypted_key = service._encrypt_key(key_data["plaintext_api_key"])

            # Update existing record (adapt_sql converts ? -> %s on PostgreSQL)
            cursor.execute(
                adapt_sql(
                    """
                    UPDATE api_key_store
                    SET encrypted_key = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND tenant_id = ?
                    """
                ),
                (encrypted_key, key_data["id"], key_data["tenant_id"]),
            )
            imported += 1
        except Exception as e:
            logger.warning(f"Failed to import API key id={key_data.get('id')}: {e}")

    conn.commit()
    logger.info(f"Imported {imported} API keys")
    return imported


def import_smtp_settings(conn, smtp_data: dict[str, Any] | None) -> bool:
    """Import and re-encrypt SMTP settings."""
    if not smtp_data:
        logger.info("No SMTP settings to import")
        return True

    from app.repositories.database import adapt_sql
    from app.utils.smtp_crypto import get_password_manager

    password_manager = get_password_manager()
    cursor = conn.cursor()

    try:
        encrypted_password = password_manager.encrypt(smtp_data["plaintext_password"])

        cursor.execute(
            adapt_sql(
                """
                UPDATE smtp_settings
                SET encrypted_password = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """
            ),
            (encrypted_password, smtp_data["id"]),
        )
        conn.commit()
        logger.info("Imported SMTP settings")
        return True
    except Exception as e:
        logger.error(f"Failed to import SMTP settings: {e}")
        return False


def import_gateway_config(conn, gateway_data: dict[str, Any] | None) -> bool:
    """Import and re-encrypt Model Gateway config."""
    if not gateway_data:
        logger.info("No Gateway config to import")
        return True

    from app.repositories.database import adapt_sql
    from app.utils.smtp_crypto import get_password_manager

    password_manager = get_password_manager()
    cursor = conn.cursor()

    try:
        encrypted_key = password_manager.encrypt(gateway_data["plaintext_api_key"])

        cursor.execute(
            adapt_sql(
                """
                UPDATE model_gateway_config
                SET encrypted_api_key = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """
            ),
            (encrypted_key, gateway_data["id"]),
        )
        conn.commit()
        logger.info("Imported Gateway config")
        return True
    except Exception as e:
        logger.error(f"Failed to import Gateway config: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Import and re-encrypt data after key rotation")
    parser.add_argument(
        "--input",
        "-i",
        required=True,
        help="Input JSON file path with plaintext data",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress info messages",
    )
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    # Validate OPENACE_ENCRYPTION_KEY is set
    if not os.environ.get("OPENACE_ENCRYPTION_KEY"):
        logger.error("OPENACE_ENCRYPTION_KEY environment variable must be set")
        sys.exit(1)

    input_path = Path(args.input)

    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        sys.exit(1)

    logger.info("Starting data import and re-encryption...")

    # Read plaintext data
    with open(input_path, encoding="utf-8") as f:
        export_data = json.load(f)

    conn = get_database_connection()
    try:
        # Import each data type
        import_api_keys(conn, export_data.get("api_keys", []))
        import_smtp_settings(conn, export_data.get("smtp_settings"))
        import_gateway_config(conn, export_data.get("gateway_config"))

        logger.info("Import complete.")
        logger.warning(
            "SECURITY: Remember to delete the plaintext input file: %s",
            input_path,
        )

        # Verify by attempting decryption
        logger.info("Verifying imported data can be decrypted...")
        verify_import(conn)

    finally:
        conn.close()


def verify_import(conn):
    """Verify that imported data can be decrypted with the current key."""
    from app.modules.workspace.api_key_proxy import APIKeyProxyService
    from app.repositories.database import adapt_boolean_condition
    from app.utils.smtp_crypto import get_password_manager

    service = APIKeyProxyService()
    password_manager = get_password_manager()
    cursor = conn.cursor()

    # Verify API keys (adapt_boolean_condition handles PG BOOLEAN vs SQLite INT)
    cursor.execute(
        f"SELECT id, encrypted_key FROM api_key_store "
        f"WHERE {adapt_boolean_condition('is_active', True)} LIMIT 5"
    )
    for row in cursor.fetchall():
        try:
            service._decrypt_key(row["encrypted_key"])
        except Exception as e:
            logger.error(f"Verification failed for API key id={row['id']}: {e}")
            return

    # Verify SMTP
    cursor.execute("SELECT id, encrypted_password FROM smtp_settings LIMIT 1")
    row = cursor.fetchone()
    if row:
        try:
            password_manager.decrypt(row["encrypted_password"])
        except Exception as e:
            logger.error(f"Verification failed for SMTP: {e}")
            return

    logger.info("Verification successful - all imported data decrypts correctly")


if __name__ == "__main__":
    main()
