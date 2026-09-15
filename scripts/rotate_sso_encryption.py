#!/usr/bin/env python3
"""
Open ACE - Encryption Key Rotation Script

Issue #1815 Finding 1: CLI tool for rotating the OPENACE_ENCRYPTION_KEY-bound
ciphertext across every store that shares the key.

OPENACE_ENCRYPTION_KEY protects more than SSO providers. This script rotates
ALL of the following stores in ONE transaction:

  - sso_providers.config["client_secret_encrypted"]          (JSON-embedded)
  - api_key_store.encrypted_key
  - smtp_settings.encrypted_password
  - model_gateway_config.encrypted_api_key
  - dingtalk_settings.app_secret_enc / fallback_webhook_secret_enc
  - feishu_settings.app_secret_enc
  - webhook_settings.webhook_secret_enc
  - notification_preferences.dingtalk_webhook_secret

Values in the key-registry format ("v1k<key_id>:..." prefix) are encrypted
with the data keys from OPENACE_ENCRYPTION_KEYS (plural) and are NOT bound to
this key — they are detected and skipped. Unprefixed values are identical
whether written via SMTPPasswordManager or registry legacy key 0 (same
SHA-256 derivation), so both rotate here.

Usage:
    # Pre-flight (dry-run): the new key can encrypt/decrypt, and every stored
    # secret is decryptable with the CURRENT key (rotation would not lose data)
    python scripts/rotate_sso_encryption.py --verify --new-key <NEW_KEY>

    # Execute mode (re-encrypt all stores, then re-check with the new key)
    python scripts/rotate_sso_encryption.py --new-key <NEW_KEY>

After rotation, update OPENACE_ENCRYPTION_KEY to the new key and restart the
service. Run BOTH modes while the environment still holds the OLD key: the
script decrypts with OPENACE_ENCRYPTION_KEY and re-encrypts with --new-key —
switching the environment variable first makes the stored ciphertext
undecryptable.

All row updates across all stores are written in a single transaction; a
mid-batch failure rolls the whole rotation back so a retry (or a single key
switch) always recovers.

Environment variables:
    OPENACE_ENCRYPTION_KEY: Current (old) encryption key (required)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

# Add project root to path
project_root = str(Path(__file__).resolve().parent.parent)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Every (table, column) whose ciphertext is derived from OPENACE_ENCRYPTION_KEY
# (SHA-256 -> Fernet, see SMTPPasswordManager). Keep in sync with schema and
# the repositories that write these columns.
DIRECT_SECRET_COLUMNS: list[tuple[str, str]] = [
    ("api_key_store", "encrypted_key"),
    ("smtp_settings", "encrypted_password"),
    ("model_gateway_config", "encrypted_api_key"),
    ("dingtalk_settings", "app_secret_enc"),
    ("dingtalk_settings", "fallback_webhook_secret_enc"),
    ("feishu_settings", "app_secret_enc"),
    ("webhook_settings", "webhook_secret_enc"),
    ("notification_preferences", "dingtalk_webhook_secret"),
]

# Ciphertexts prefixed like "v1k<key_id>:..." belong to the key registry and
# are encrypted with the OPENACE_ENCRYPTION_KEYS (plural) data keys; rotating
# the single-key env does not affect them.
_REGISTRY_PREFIX_RE = re.compile(r"^v1k\d+:")


def _is_registry_format(ciphertext: str) -> bool:
    return bool(_REGISTRY_PREFIX_RE.match(ciphertext))


def get_current_encryption_key() -> str | None:
    """Get the current encryption key from environment."""
    return os.environ.get("OPENACE_ENCRYPTION_KEY")


def _iter_secret_values(db) -> list[tuple[str, str]]:
    """Collect every key-bound ciphertext as (store_label, ciphertext).

    Registry-format values are skipped (not bound to this key); empty/NULL
    values are skipped (nothing to rotate).
    """
    values: list[tuple[str, str]] = []

    for table, column in DIRECT_SECRET_COLUMNS:
        rows = db.fetch_all(
            f"SELECT {column} AS v FROM {table} " f"WHERE {column} IS NOT NULL AND {column} != ''"
        )
        for row in rows:
            ciphertext = row["v"]
            if _is_registry_format(ciphertext):
                logger.debug(f"{table}.{column}: registry-format value, skipped")
                continue
            values.append((f"{table}.{column}", ciphertext))

    for row in db.fetch_all("SELECT name, config FROM sso_providers WHERE config IS NOT NULL"):
        try:
            encrypted = (json.loads(row["config"]) or {}).get("client_secret_encrypted", "")
        except (json.JSONDecodeError, TypeError):
            values.append((f"sso_providers/{row['name']}", "<invalid-json>"))
            continue
        if not encrypted:
            continue
        if _is_registry_format(encrypted):
            logger.debug(f"sso_providers/{row['name']}: registry-format value, skipped")
            continue
        values.append((f"sso_providers/{row['name']}", encrypted))

    return values


def verify_key_works(db_url: str, new_key: str) -> tuple[bool, list[str]]:
    """Pre-flight rotation checks (no data is modified).

    1. The new key round-trips: a probe value encrypts and decrypts back
       (catches unusable key material before touching any row).
    2. Every stored secret in EVERY store decrypts with the CURRENT key —
       otherwise re-encryption would silently corrupt those records.

    Args:
        db_url: Database URL.
        new_key: New encryption key to test.

    Returns:
        Tuple of (success, list of store labels that failed).
    """
    from app.repositories.database import Database
    from app.utils.smtp_crypto import SMTPPasswordManager

    if new_key == get_current_encryption_key():
        logger.error("New key is identical to the current key; rotation would be a no-op")
        return False, ["<same-key-noop>"]

    db = Database(db_url=db_url)
    pm_new = SMTPPasswordManager(encryption_key=new_key)
    probe = pm_new.encrypt("openace-rotation-probe")
    if pm_new.decrypt(probe) != "openace-rotation-probe":
        logger.error("New key failed an encrypt/decrypt round-trip probe")
        return False, ["<new-key-roundtrip>"]

    old_key = get_current_encryption_key()
    if not old_key:
        logger.error("OPENACE_ENCRYPTION_KEY not set")
        return False, ["<current-key-missing>"]
    pm_old = SMTPPasswordManager.for_legacy_rotation_key(old_key)

    failed_stores: list[str] = []
    checked = 0
    for label, ciphertext in _iter_secret_values(db):
        if ciphertext == "<invalid-json>":
            failed_stores.append(label)
            logger.warning(f"{label}: config is not valid JSON")
            continue
        try:
            if pm_old.decrypt(ciphertext):
                checked += 1
            else:
                failed_stores.append(label)
                logger.warning(f"{label}: empty decrypted value")
        except Exception as e:
            failed_stores.append(label)
            logger.warning(f"{label}: {e}")

    if checked:
        logger.info(f"Pre-flight: {checked} value(s) decrypt with the current key")
    return len(failed_stores) == 0, failed_stores


def rotate_keys(db_url: str, new_key: str) -> tuple[bool, int, list[str]]:
    """Re-encrypt every key-bound secret across all stores with the new key.

    Args:
        db_url: Database URL.
        new_key: New encryption key.

    Returns:
        Tuple of (success, count of re-encrypted values, list of failed stores).
    """
    from app.repositories.database import Database
    from app.utils.smtp_crypto import SMTPPasswordManager

    db = Database(db_url=db_url)

    # Constructor rejects empty/weak/short candidates before anything below
    # can touch the database.
    pm_new = SMTPPasswordManager(encryption_key=new_key)

    old_key = get_current_encryption_key()
    if not old_key:
        logger.error("OPENACE_ENCRYPTION_KEY not set")
        return False, 0, []
    if new_key == old_key:
        logger.error("New key is identical to the current key; rotation would be a no-op")
        return False, 0, ["<same-key-noop>"]
    pm_old = SMTPPasswordManager.for_legacy_rotation_key(old_key)

    # Decrypt and re-encrypt EVERYTHING (all stores) before the first write,
    # then write all updates in a single transaction. A per-row autocommit
    # would leave partially rotated stores on a mid-batch failure —
    # unrecoverable with a single key switch.
    #
    # PR #3386 R16/R17 review: scans and writes share ONE transaction and
    # every UPDATE is OPTIMISTIC on the value it scanned (WHERE <old value>,
    # rowcount must be exactly 1 — Fernet ciphertexts are unique per
    # encryption), so a concurrent MODIFICATION of a scanned row becomes a
    # mismatch and rolls the whole rotation back instead of silently
    # overwriting it. This is NOT a lock: with no table locks and default
    # READ COMMITTED snapshots, rows INSERTED after a table's scan are not
    # seen, and postcheck can only REPORT mixed-key data after the fact —
    # it cannot un-commit it. Script safety for a live deployment therefore
    # RESTS ON the documented procedure: stop every old-key reader/writer
    # (app, scheduler, workers) before rotating; the checks here are the
    # detection layer, not the isolation layer.
    # Write plan entries: (sql, params, label)
    updates: list[tuple[str, tuple, str]] = []
    failed_stores: list[str] = []

    def _fetch_dicts(conn, sql: str) -> list[dict]:
        if db._is_postgresql:
            from psycopg2.extras import RealDictCursor

            cur = conn.cursor(cursor_factory=RealDictCursor)
        else:
            cur = conn.cursor()
        cur.execute(db._adapt_sql(sql))
        rows = cur.fetchall()
        if rows and isinstance(rows[0], dict):
            return list(rows)
        return [dict(row) for row in rows]

    # db._adapt_sql (not the module-level adapt_sql) because placeholders
    # must match THIS instance's backend — a temp sqlite db_url on a
    # postgres-configured host is exactly this script's test/ops usage.
    with db.connection() as conn:
        for table, column in DIRECT_SECRET_COLUMNS:
            rows = _fetch_dicts(
                conn,
                f"SELECT {column} AS v FROM {table} "
                f"WHERE {column} IS NOT NULL AND {column} != ''",
            )
            for row in rows:
                old_ct = row["v"]
                label = f"{table}.{column}"
                if _is_registry_format(old_ct):
                    continue
                try:
                    plaintext = pm_old.decrypt(old_ct)
                    if not plaintext:
                        failed_stores.append(label)
                        logger.error(f"{label}: empty decrypted value")
                        continue
                    new_ct = pm_new.encrypt(plaintext)
                except Exception as e:
                    failed_stores.append(label)
                    logger.error(f"{label}: {e}")
                    continue
                # Match on the old ciphertext itself: PK-agnostic and
                # backend-agnostic (Fernet ciphertexts are unique per
                # row-value).
                updates.append(
                    (f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new_ct, old_ct), label)
                )

        for row in _fetch_dicts(
            conn, "SELECT name, config FROM sso_providers WHERE config IS NOT NULL"
        ):
            name = row["name"]
            label = f"sso_providers/{name}"
            try:
                config = json.loads(row["config"])
                old_ct = (config or {}).get("client_secret_encrypted", "")
            except (json.JSONDecodeError, TypeError) as e:
                failed_stores.append(label)
                logger.error(f"{label}: config is not valid JSON: {e}")
                continue
            if not old_ct or _is_registry_format(old_ct):
                continue
            try:
                plaintext = pm_old.decrypt(old_ct)
                if not plaintext:
                    failed_stores.append(label)
                    logger.error(f"{label}: empty decrypted value")
                    continue
                config["client_secret_encrypted"] = pm_new.encrypt(plaintext)
            except Exception as e:
                failed_stores.append(label)
                logger.error(f"{label}: {e}")
                continue
            updates.append(
                (
                    # Optimistic on the ORIGINAL config text: a concurrent
                    # config change makes this affect 0 rows and the
                    # rowcount guard below rolls the whole rotation back,
                    # instead of silently overwriting with the stale
                    # snapshot (PR #3386 R17 review).
                    "UPDATE sso_providers SET config = ? WHERE name = ? AND config = ?",
                    (json.dumps(config), name, row["config"]),
                    label,
                )
            )

        if failed_stores:
            logger.error("No rows written: every value must re-encrypt cleanly first")
            return False, 0, failed_stores

        try:
            cursor = conn.cursor()
            for sql, params, label in updates:
                cursor.execute(db._adapt_sql(sql), params)
                if cursor.rowcount != 1:
                    raise RuntimeError(
                        f"{label}: UPDATE affected {cursor.rowcount} rows (expected 1) — "
                        "concurrent modification detected; rolling back the whole rotation"
                    )
            conn.commit()
        except Exception as e:
            logger.error(f"Rotation failed; transaction rolled back: {e}")
            return False, 0, [label for _, _, label in updates]

    for _, _, label in updates:
        logger.info(f"Re-encrypted {label}")
    return True, len(updates), []


def postcheck_new_key(db_url: str, new_key: str) -> list[str]:
    """Verify every key-bound value decrypts with the new key."""
    from app.repositories.database import Database
    from app.utils.smtp_crypto import SMTPPasswordManager

    db = Database(db_url=db_url)
    pm = SMTPPasswordManager(encryption_key=new_key)
    failed: list[str] = []
    for label, ciphertext in _iter_secret_values(db):
        try:
            if not pm.decrypt(ciphertext):
                failed.append(label)
        except Exception as e:
            logger.warning(f"{label} post-check failed: {e}")
            failed.append(label)
    return failed


def main():
    parser = argparse.ArgumentParser(
        description="Rotate OPENACE_ENCRYPTION_KEY-bound encryption keys (Issue #1815)"
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

    # Validate the candidate key up front with the same rules the runtime
    # applies (SMTPPasswordManager rejects empty/weak/<32-char candidates),
    # so an unusable key exits cleanly before any database work.
    from app.utils.smtp_crypto import SMTPPasswordManager

    try:
        SMTPPasswordManager(encryption_key=args.new_key)
    except ValueError as e:
        logger.error(f"Invalid --new-key: {e}")
        sys.exit(1)

    # Check current key is set
    current_key = get_current_encryption_key()
    if not current_key:
        logger.error("OPENACE_ENCRYPTION_KEY environment variable not set")
        sys.exit(1)
    if args.new_key == current_key:
        logger.error(
            "--new-key is identical to the current OPENACE_ENCRYPTION_KEY; refusing the no-op rotation"
        )
        sys.exit(1)

    # Get database URL
    db_url = args.db_url or os.environ.get("DATABASE_URL") or "sqlite:///openace.db"

    logger.info(f"Using database: {db_url.split('@')[-1] if '@' in db_url else db_url}")

    if args.verify:
        logger.info("Running in VERIFY mode (dry-run pre-flight)")
        success, failed = verify_key_works(db_url, args.new_key)

        if success:
            logger.info("✓ New key round-trips and all stored secrets decrypt with the current key")
            logger.info("You can now run in execute mode to re-encrypt")
        else:
            logger.error(f"✗ Pre-flight failed for: {failed}")
            sys.exit(1)
    else:
        logger.info("Running in EXECUTE mode")
        logger.warning("This will modify the database!")

        # Pre-flight: abort before touching any row if the current key cannot
        # decrypt every stored secret or the new key is unusable.
        logger.info("Running pre-flight checks...")
        verify_success, verify_failed = verify_key_works(db_url, args.new_key)

        if not verify_success:
            logger.error(f"Pre-flight failed for stores: {verify_failed}")
            logger.error("Aborting. Run with --verify first to see details.")
            sys.exit(1)

        # Execute rotation
        success, count, failed = rotate_keys(db_url, args.new_key)

        if not success:
            logger.error(f"✗ Rotation failed for: {failed}")
            sys.exit(1)

        # Post-rotation check: every stored secret must now decrypt with the
        # NEW key (the operator's go/no-go signal before switching the env).
        postcheck_failed = postcheck_new_key(db_url, args.new_key)
        if postcheck_failed:
            logger.error(
                f"✗ Rotation wrote {count} value(s) but post-check failed: {postcheck_failed}"
            )
            sys.exit(1)

        logger.info(f"✓ Successfully re-encrypted {count} value(s) across all stores")
        logger.info("✓ All stored secrets decrypt with the new key")
        logger.info("Update OPENACE_ENCRYPTION_KEY environment variable and restart the service")


if __name__ == "__main__":
    main()
