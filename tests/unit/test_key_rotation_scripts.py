"""
Tests for the key-rotation export/import scripts (Issue #1857).

These scripts are the recommended rotation path documented in
docs/{cn,en}/KEY_MANAGEMENT.md, so they must:
  - export decrypted plaintext that round-trips through import,
  - write the plaintext export with restrictive (0600) permissions,
  - honor --dry-run without writing a file,
  - use adapt_sql()/adapt_boolean_condition() so they run on PostgreSQL as
    well as SQLite (the import script originally used bare ``?`` placeholders
    that raise on PG).
"""

import importlib.util
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

ENCRYPTION_KEY = "rotation-test-key-0123456789abcdef"


def _load_script(module_name: str, rel_path: str):
    """Load a script under scripts/ as a module without re-importing the package."""
    path = SCRIPTS_DIR / rel_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed_sqlite_db(db_path: Path) -> None:
    """Create the three encrypted-data tables and insert one row each.

    Values are encrypted with the current key so export can decrypt them.
    The caller is responsible for setting OPENACE_ENCRYPTION_KEY (via
    monkeypatch) before calling this so no env state leaks across tests.
    """
    from app.modules.workspace.api_key_proxy import APIKeyProxyService
    from app.utils.smtp_crypto import SMTPPasswordManager

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE api_key_store (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tenant_id INTEGER,
            provider TEXT,
            key_name TEXT,
            encrypted_key TEXT,
            base_url TEXT,
            cli_tools TEXT,
            cli_settings TEXT,
            scope TEXT,
            priority INTEGER,
            weight INTEGER,
            is_active INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE smtp_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            smtp_host TEXT,
            smtp_port INTEGER,
            smtp_user TEXT,
            encrypted_password TEXT,
            from_address TEXT,
            use_tls INTEGER,
            is_verified INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE model_gateway_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT,
            base_url TEXT,
            encrypted_api_key TEXT,
            model_prefix_mode TEXT,
            model_prefix TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    api_service = APIKeyProxyService(db_path=str(db_path))
    smtp_mgr = SMTPPasswordManager()

    cur.execute(
        """
        INSERT INTO api_key_store
            (tenant_id, provider, key_name, encrypted_key, base_url, cli_tools,
             cli_settings, scope, priority, weight, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            1,
            "openai",
            "test-key",
            api_service._encrypt_key("sk-test-secret-api-key"),
            "https://api.openai.com",
            "[]",
            "{}",
            "remote",
            0,
            100,
            1,
        ),
    )
    cur.execute(
        """
        INSERT INTO smtp_settings
            (smtp_host, smtp_port, smtp_user, encrypted_password, from_address,
             use_tls, is_verified)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "smtp.example.com",
            587,
            "user@example.com",
            smtp_mgr.encrypt("smtp-secret-password"),
            "noreply@example.com",
            1,
            1,
        ),
    )
    cur.execute(
        """
        INSERT INTO model_gateway_config
            (mode, base_url, encrypted_api_key, model_prefix_mode, model_prefix)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "remote",
            "https://gateway.example.com",
            smtp_mgr.encrypt("gateway-secret-key"),
            "prefix",
            "gw-",
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def rotation_env(tmp_path, monkeypatch):
    """Point the scripts at an isolated SQLite DB seeded with encrypted rows."""
    db_path = tmp_path / "rotation.db"

    # Set the key BEFORE seeding (via monkeypatch so it's restored after the
    # test and cannot leak into sibling tests in the same session).
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", ENCRYPTION_KEY)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")

    _seed_sqlite_db(db_path)

    yield {"db_path": db_path, "tmp_path": tmp_path}


class TestExportScript:
    def test_export_decrypts_all_three_stores(self, rotation_env):
        export = _load_script("export_encrypted_data_under_test", "export_encrypted_data.py")
        conn = export.get_database_connection()
        try:
            api_keys = export.export_api_keys(conn)
            smtp = export.export_smtp_settings(conn)
            gateway = export.export_gateway_config(conn)
        finally:
            conn.close()

        assert len(api_keys) == 1
        assert api_keys[0]["plaintext_api_key"] == "sk-test-secret-api-key"
        assert smtp is not None and smtp["plaintext_password"] == "smtp-secret-password"
        assert gateway is not None and gateway["plaintext_api_key"] == "gateway-secret-key"

    def test_dry_run_writes_no_file(self, rotation_env, monkeypatch):
        export = _load_script("export_dry_run_under_test", "export_encrypted_data.py")
        out_path = rotation_env["tmp_path"] / "dry.json"

        monkeypatch.setattr(sys, "argv", ["export", "--output", str(out_path), "--dry-run"])
        export.main()

        # Core guarantee of --dry-run: no plaintext file is written.
        assert not out_path.exists()

    def test_export_file_is_mode_0600(self, rotation_env):
        export = _load_script("export_perms_under_test", "export_encrypted_data.py")
        out_path = rotation_env["tmp_path"] / "out.json"

        # main() uses argparse on sys.argv
        original_argv = sys.argv
        sys.argv = ["export", "--output", str(out_path), "--quiet"]
        try:
            export.main()
        finally:
            sys.argv = original_argv

        mode = stat.S_IMODE(os.stat(out_path).st_mode)
        assert mode == 0o600, f"export file should be 0600, got {oct(mode)}"


class TestImportScript:
    def test_adapt_sql_is_used_for_placeholders(self):
        """The import UPDATE statements must go through adapt_sql so the bare
        ``?`` placeholders are rewritten to ``%s`` on PostgreSQL."""
        _load_script("import_adapt_under_test", "import_encrypted_data.py")
        # Importing the module exercises its top-level imports; the real guard
        # is that each import_* helper calls adapt_sql. Verify the helper is
        # referenced in the module source.
        source = (SCRIPTS_DIR / "import_encrypted_data.py").read_text()
        assert (
            source.count("adapt_sql(") >= 3
        ), "import_api_keys/smtp/gateway must each use adapt_sql() for PG placeholders"

    def test_export_then_import_roundtrip(self, rotation_env, monkeypatch):
        """Re-encrypting exported plaintext under a NEW key must decrypt back
        to the original values — i.e. the rotation actually works end-to-end."""
        export = _load_script("export_roundtrip_under_test", "export_encrypted_data.py")
        import_mod = _load_script("import_roundtrip_under_test", "import_encrypted_data.py")

        db_path = rotation_env["db_path"]
        backup = rotation_env["tmp_path"] / "backup.json"

        # 1. Export with the OLD key
        sys.argv = ["export", "--output", str(backup), "--quiet"]
        export.main()

        # 2. Switch to a NEW key and re-encrypt via import.
        #    NOTE: import constructs APIKeyProxyService()/get_password_manager()
        #    fresh, so the new key is picked up (the SMTP singleton is reset by
        #    the import-time get_password_manager() only if None; force-reset).
        import app.utils.smtp_crypto as smtp_crypto

        new_key = "rotated-new-key-9876543210fedcba"
        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", new_key)
        smtp_crypto._password_manager_instance = None  # drop old-key singleton

        conn = import_mod.get_database_connection()
        try:
            with open(backup, encoding="utf-8") as f:
                data = json.load(f)
            import_mod.import_api_keys(conn, data.get("api_keys", []))
            import_mod.import_smtp_settings(conn, data.get("smtp_settings"))
            import_mod.import_gateway_config(conn, data.get("gateway_config"))
        finally:
            conn.close()

        # 3. Verify the DB now decrypts correctly under the NEW key.
        from app.modules.workspace.api_key_proxy import APIKeyProxyService
        from app.utils.smtp_crypto import SMTPPasswordManager

        api_service = APIKeyProxyService(db_path=str(db_path))
        smtp_mgr = SMTPPasswordManager()
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT encrypted_key FROM api_key_store WHERE id = 1").fetchone()
            assert api_service._decrypt_key(row["encrypted_key"]) == "sk-test-secret-api-key"

            row = conn.execute(
                "SELECT encrypted_password FROM smtp_settings WHERE id = 1"
            ).fetchone()
            assert smtp_mgr.decrypt(row["encrypted_password"]) == "smtp-secret-password"

            row = conn.execute(
                "SELECT encrypted_api_key FROM model_gateway_config WHERE id = 1"
            ).fetchone()
            assert smtp_mgr.decrypt(row["encrypted_api_key"]) == "gateway-secret-key"
        finally:
            conn.close()

    def test_verify_import_handles_boolean_on_pg_helper(self):
        """verify_import uses adapt_boolean_condition (not a hardcoded = 1)."""
        source = (SCRIPTS_DIR / "import_encrypted_data.py").read_text()
        assert "adapt_boolean_condition" in source, (
            "verify_import must use adapt_boolean_condition for is_active, "
            "not a hardcoded '= 1' that breaks on PostgreSQL"
        )
