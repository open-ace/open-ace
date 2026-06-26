"""
Integration test for the security model documented in docs/en/SECURITY.md.

Exercises the full, cross-component flow a real deployment relies on:

  1. An admin stores an API key → it is encrypted at rest (only a hash readable).
  2. An admin issues a one-time registration token → only its hash is stored.
  3. A remote machine consumes that token → it cannot be replayed.
  4. The server issues a proxy token bound to the session/tenant/provider.
  5. A different encryption key cannot forge or validate another tenant's token.
  6. The sensitive-field stripper removes credentials before settings reach an agent.

Unlike the unit test (test_security_model.py), this test wires the
APIKeyProxyService and RemoteAgentManager together against the same isolated
SQLite database to prove the layers compose correctly.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
from unittest.mock import patch

import pytest

import app.repositories.database as db_mod


@pytest.fixture(autouse=True)
def _patch_db_compat():
    """Force SQLite mode for every test, regardless of the host's DB config.

    ``api_key_proxy`` and ``remote_agent_manager`` bind ``is_postgresql`` by
    name (``from app.repositories.database import is_postgresql``), so patching
    ``db_mod.is_postgresql`` alone does not reach them — they would still read
    the live config and connect to a real PostgreSQL instance (and leak state
    when other ``*_pg`` integration tests run first in the same session). We
    therefore patch the name on every module that imports it directly, plus
    ``get_database_url`` on the config module, so the proxy/manager use the
    isolated temp SQLite database the fixtures create.
    """
    import scripts.shared.config as config_mod
    from app.modules.workspace import api_key_proxy as akp
    from app.modules.workspace import remote_agent_manager as ram

    orig_adapt = db_mod.adapt_sql
    db_mod.adapt_sql = lambda q: q  # type: ignore[assignment]
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(db_mod, "is_postgresql", return_value=False))
            stack.enter_context(patch.object(akp, "is_postgresql", return_value=False))
            stack.enter_context(patch.object(ram, "is_postgresql", return_value=False))
            stack.enter_context(
                patch.object(config_mod, "get_database_url", return_value="sqlite:///test.db")
            )
            yield
    finally:
        db_mod.adapt_sql = orig_adapt


@pytest.fixture
def isolated_stack(tmp_path, monkeypatch):
    """Spin up a proxy service + agent manager sharing one temp DB."""
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "integration-test-encryption-key")

    db_path = str(tmp_path / "stack.db")

    from app.modules.workspace import api_key_proxy as akp
    from app.modules.workspace.remote_agent_manager import RemoteAgentManager, get_ddl_statements

    monkeypatch.setattr(akp, "DB_PATH", db_path, raising=False)

    proxy = akp.APIKeyProxyService(db_path=db_path)
    proxy._ensure_tables()

    manager = RemoteAgentManager(db_path=db_path)
    with manager.db.connection() as conn:
        cursor = conn.cursor()
        for sql in get_ddl_statements():
            try:
                cursor.execute(sql)
            except Exception:
                pass
        conn.commit()

    return proxy, manager, db_path


class TestEndToEndSecurityFlow:
    """The 10-Minute Demo (DEMO-10-MINUTES.md), asserted as code."""

    def test_store_key_encrypts_only_hash_readable(self, isolated_stack):
        proxy, _manager, _ = isolated_stack
        raw_key = "sk-integration-abc123"

        result = proxy.store_api_key(
            tenant_id=1,
            provider="openai",
            key_name="demo-key",
            api_key=raw_key,
            cli_tools=json.dumps(["qwen-code-cli"]),
        )
        assert result["success"] is True

        # The plaintext must not appear anywhere on disk.
        with proxy._get_connection() as conn:
            rows = conn.execute("SELECT encrypted_key, key_hash FROM api_key_store").fetchall()
        blob = "\n".join(dict(r)["encrypted_key"] + dict(r)["key_hash"] for r in rows)
        assert raw_key not in blob
        # The stored hash equals the SHA-256 of the plaintext.
        assert any(
            dict(r)["key_hash"] == hashlib.sha256(raw_key.encode()).hexdigest() for r in rows
        )

    def test_registration_then_proxy_token_full_flow(self, isolated_stack):
        proxy, manager, _ = isolated_stack

        # Step 4 of the demo: admin generates a registration token.
        reg_token = manager.create_registration_token(tenant_id=1, created_by=1)
        assert re.fullmatch(r"[0-9a-f]{64}", reg_token)

        # Step 5: remote machine registers with the one-time token.
        machine = manager.register_machine(
            registration_token=reg_token,
            machine_id="machine-demo",
            machine_name="demo-host",
            hostname="demo",
        )
        assert machine is not None
        assert machine["agent_token"]  # long-lived agent credential issued

        # The same token cannot be reused (one-time use guarantee).
        replay = manager.register_machine(
            registration_token=reg_token,
            machine_id="machine-replay",
            machine_name="replay-host",
            hostname="replay",
        )
        assert replay is None

        # Step 6: server issues a short-lived proxy token for a coding session.
        proxy_token = proxy.generate_proxy_token(
            user_id=2,
            session_id="sess-demo",
            tenant_id=1,
            provider="openai",
            session_type="terminal",
        )
        payload = proxy.validate_proxy_token(proxy_token)
        assert payload is not None
        assert payload["user_id"] == 2
        assert payload["tenant_id"] == 1
        assert payload["provider"] == "openai"
        # The real API key is never embedded in the proxy token.
        assert "sk-" not in proxy_token

    def test_cross_key_isolation(self, isolated_stack, monkeypatch):
        """A token signed under one encryption key is invalid under another.

        This models tenant/key rotation isolation: a key compromise elsewhere
        cannot forge proxy tokens for this deployment.
        """
        proxy, _manager, _ = isolated_stack
        token = proxy.generate_proxy_token(
            user_id=1,
            session_id="sess-isolation",
            tenant_id=1,
            provider="openai",
            session_type="terminal",
        )

        monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", "attacker-known-different-key")
        from app.modules.workspace.api_key_proxy import APIKeyProxyService

        forged_view = APIKeyProxyService(db_path=proxy.db_path)
        assert forged_view.validate_proxy_token(token) is None

    def test_settings_strip_credentials_before_send(self, isolated_stack):
        proxy, _manager, _ = isolated_stack
        # Simulate a user pasting credentials into the UI settings block.
        leaky_settings = {
            "env": {
                "OPENAI_API_KEY": "sk-should-be-stripped",
                "ANTHROPIC_API_KEY": "sk-ant-stripped",
                "NON_SECRET": "kept",
            },
            "modelProviders": {"openai": [{"id": "m1", "baseUrl": "https://hidden", "name": "m1"}]},
        }
        cleaned = proxy._build_cli_settings_for_tool("qwen-code-cli", leaky_settings)
        env = cleaned["env"]
        assert "OPENAI_API_KEY" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert env["NON_SECRET"] == "kept"
        assert "baseUrl" not in cleaned["modelProviders"]["openai"][0]
