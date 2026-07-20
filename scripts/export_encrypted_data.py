#!/usr/bin/env python3
"""
Export all encrypted data to plaintext for key rotation.

This script exports encrypted data from the database using the current
encryption key, outputting plaintext values to a JSON file. Use this
during key rotation to prepare data for re-encryption with a new key.

Usage:
    python scripts/export_encrypted_data.py --output encrypted_data_backup.json

Security Note:
    The output file contains PLAINTEXT secrets. Handle with care:
    - Store in a secure location
    - Delete after successful re-encryption
    - Never commit to version control

Tables exported:
    - api_key_store.encrypted_key -> plaintext API keys
    - smtp_settings.encrypted_password -> plaintext SMTP passwords
    - model_gateway_config.encrypted_api_key -> plaintext gateway keys
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime
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


def export_api_keys(conn) -> list[dict[str, Any]]:
    """Export all API keys from api_key_store table."""
    from app.modules.workspace.api_key_proxy import APIKeyProxyService
    from app.repositories.database import adapt_boolean_condition

    service = APIKeyProxyService()
    cursor = conn.cursor()

    # adapt_boolean_condition emits "(is_active)::int != 0" on PostgreSQL and
    # "is_active = 1" on SQLite, covering both boolean and INTEGER columns.
    cursor.execute(
        f"""
        SELECT id, tenant_id, provider, key_name, encrypted_key, base_url,
               cli_tools, cli_settings, scope, priority, weight
        FROM api_key_store
        WHERE {adapt_boolean_condition("is_active", True)}
        """
    )

    rows = cursor.fetchall()
    exported = []

    for row in rows:
        row_dict = dict(row)
        try:
            # Decrypt the API key
            plaintext_key = service._decrypt_key(row_dict["encrypted_key"])
            exported.append(
                {
                    "id": row_dict["id"],
                    "tenant_id": row_dict["tenant_id"],
                    "provider": row_dict["provider"],
                    "key_name": row_dict["key_name"],
                    "plaintext_api_key": plaintext_key,
                    "base_url": row_dict["base_url"],
                    "cli_tools": row_dict["cli_tools"],
                    "cli_settings": row_dict["cli_settings"],
                    "scope": row_dict["scope"],
                    "priority": row_dict["priority"],
                    "weight": row_dict["weight"],
                }
            )
        except Exception as e:
            logger.warning(f"Failed to decrypt API key id={row_dict['id']}: {e}")

    logger.info(f"Exported {len(exported)} API keys")
    return exported


def export_smtp_settings(conn) -> dict[str, Any] | None:
    """Export SMTP settings from smtp_settings table."""
    from app.utils.smtp_crypto import get_password_manager

    password_manager = get_password_manager()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, smtp_host, smtp_port, smtp_user, encrypted_password,
               from_address, use_tls, is_verified
        FROM smtp_settings
        ORDER BY id DESC LIMIT 1
        """
    )

    row = cursor.fetchone()
    if not row:
        logger.info("No SMTP settings found")
        return None

    row_dict = dict(row)
    try:
        plaintext_password = password_manager.decrypt(row_dict["encrypted_password"])
        return {
            "id": row_dict["id"],
            "smtp_host": row_dict["smtp_host"],
            "smtp_port": row_dict["smtp_port"],
            "smtp_user": row_dict["smtp_user"],
            "plaintext_password": plaintext_password,
            "from_address": row_dict["from_address"],
            "use_tls": row_dict["use_tls"],
            "is_verified": row_dict["is_verified"],
        }
    except Exception as e:
        logger.warning(f"Failed to decrypt SMTP password: {e}")
        return None


def export_gateway_config(conn) -> dict[str, Any] | None:
    """Export Model Gateway config from model_gateway_config table."""
    from app.utils.smtp_crypto import get_password_manager

    password_manager = get_password_manager()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT id, mode, base_url, encrypted_api_key, model_prefix_mode, model_prefix
        FROM model_gateway_config
        ORDER BY id DESC LIMIT 1
        """
    )

    row = cursor.fetchone()
    if not row:
        logger.info("No Model Gateway config found")
        return None

    row_dict = dict(row)
    try:
        plaintext_key = password_manager.decrypt(row_dict["encrypted_api_key"])
        return {
            "id": row_dict["id"],
            "mode": row_dict["mode"],
            "base_url": row_dict["base_url"],
            "plaintext_api_key": plaintext_key,
            "model_prefix_mode": row_dict["model_prefix_mode"],
            "model_prefix": row_dict["model_prefix"],
        }
    except Exception as e:
        logger.warning(f"Failed to decrypt Gateway API key: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Export encrypted data to plaintext for key rotation"
    )
    parser.add_argument(
        "--output",
        "-o",
        required=True,
        help="Output JSON file path for plaintext data",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress info messages",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Decrypt and report counts without writing the plaintext file",
    )
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    # Validate OPENACE_ENCRYPTION_KEY is set
    if not os.environ.get("OPENACE_ENCRYPTION_KEY"):
        logger.error("OPENACE_ENCRYPTION_KEY environment variable must be set")
        sys.exit(1)

    output_path = Path(args.output)

    # Safety check: don't overwrite existing files without warning
    if output_path.exists():
        logger.warning(f"Output file {output_path} already exists, will overwrite")

    logger.info("Starting encrypted data export...")

    conn = get_database_connection()
    try:
        api_keys = export_api_keys(conn)
        smtp_settings = export_smtp_settings(conn)
        gateway_config = export_gateway_config(conn)

        if args.dry_run:
            logger.info(
                "Dry-run: would export %d API keys, smtp=%s, gateway=%s",
                len(api_keys),
                bool(smtp_settings),
                bool(gateway_config),
            )
            logger.info("Dry-run complete — no file written.")
            return

        export_data = {
            "export_timestamp": datetime.now().isoformat(),
            "api_keys": api_keys,
            "smtp_settings": smtp_settings,
            "gateway_config": gateway_config,
        }

        # Write with restrictive permissions (0600). The file contains
        # plaintext secrets; default umask may leave it group/world readable.
        # On POSIX we pre-create the file 0600 so the secrets never touch a
        # more-permissive inode. Windows ignores the mode bits.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(output_path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        # Belt-and-suspenders: chmod in case the file pre-existed.
        os.chmod(output_path, 0o600)

        logger.info(f"Export complete. Output written to: {output_path}")
        logger.warning(
            "SECURITY: This file contains plaintext secrets (mode 0600). "
            "Delete after re-encryption is complete."
        )

    finally:
        conn.close()


if __name__ == "__main__":
    main()
