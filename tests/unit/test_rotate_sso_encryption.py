"""Rotation-script regression tests from the PR #3386 review round.

Covers the two failure modes the review reproduced:

1. Mid-write failure atomicity: ``rotate_keys`` used to autocommit each
   UPDATE; a failure on the second row left the first permanently rotated —
   unrecoverable with a single key switch. All writes must now share one
   transaction and roll back entirely.
2. Explicit candidate-key validation: ``SMTPPasswordManager(encryption_key=...)``
   must reject empty/weak/short candidates with the runtime's own rules
   BEFORE any write, or a rotation can complete with a key the production
   runtime then refuses (e.g. an unset shell variable giving ``--new-key ''``).
"""

import json
import sqlite3

import pytest

from app.utils.smtp_crypto import SMTPPasswordManager
from scripts.rotate_sso_encryption import rotate_keys, verify_key_works

OLD_KEY = "unit-old-encryption-key-0123456789abcdef"
NEW_KEY = "unit-new-encryption-key-9876543210fedcba"
SECRETS = {"first": "secret-one", "second": "secret-two"}


@pytest.fixture(autouse=True)
def _old_key_env(monkeypatch):
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", OLD_KEY)


def _make_db(tmp_path, trigger_second=False):
    db_path = tmp_path / "sso.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sso_providers (name TEXT PRIMARY KEY, config TEXT)")
    pm = SMTPPasswordManager()
    for name, secret in SECRETS.items():
        config = {
            "client_id": f"{name}-id",
            "client_secret_encrypted": pm.encrypt(secret),
        }
        conn.execute(
            "INSERT INTO sso_providers (name, config) VALUES (?, ?)",
            (name, json.dumps(config)),
        )
    if trigger_second:
        # Reproduces the review's mid-write failure: the second UPDATE aborts.
        conn.execute("""
            CREATE TRIGGER fail_second_update BEFORE UPDATE ON sso_providers
            WHEN NEW.name = 'second'
            BEGIN SELECT RAISE(ABORT, 'injected mid-write failure'); END
            """)
    conn.commit()
    conn.close()
    return f"sqlite:///{db_path}"


def _read_configs(db_url):
    path = db_url.replace("sqlite:///", "", 1)
    conn = sqlite3.connect(path)
    rows = dict(conn.execute("SELECT name, config FROM sso_providers").fetchall())
    conn.close()
    return {name: json.loads(cfg) for name, cfg in rows.items()}


def _decrypt(configs, key):
    pm = SMTPPasswordManager(encryption_key=key)
    return {name: pm.decrypt(cfg["client_secret_encrypted"]) for name, cfg in configs.items()}


def test_rotation_rewrites_all_rows_with_new_key(tmp_path):
    db_url = _make_db(tmp_path)

    success, count, failed = rotate_keys(db_url, NEW_KEY)

    assert (success, count, failed) == (True, len(SECRETS), [])
    assert _decrypt(_read_configs(db_url), NEW_KEY) == SECRETS


def test_mid_write_failure_rolls_back_every_row(tmp_path):
    db_url = _make_db(tmp_path, trigger_second=True)
    before = _read_configs(db_url)

    success, count, failed = rotate_keys(db_url, NEW_KEY)

    assert success is False
    assert count == 0
    assert set(failed) == set(SECRETS)
    # Atomicity: NOTHING changed — both rows still decrypt with the old key,
    # so the operator can retry (or switch keys once) without data loss.
    assert _read_configs(db_url) == before
    assert _decrypt(_read_configs(db_url), OLD_KEY) == SECRETS
    # ...and a retry pre-flight against the old key still passes.
    assert verify_key_works(db_url, NEW_KEY)[0] is True


@pytest.mark.parametrize(
    "bad_key",
    [
        "",  # unset shell variable: --new-key "$UNSET"
        "change-me-in-production",  # denylisted placeholder
        "replace-with-random-abc",  # denylisted prefix
        "a" * 31,  # one char short of the 32-char minimum
    ],
    ids=["empty", "denylisted", "denylisted-prefix", "too-short"],
)
def test_invalid_candidate_key_rejected_before_any_write(tmp_path, bad_key):
    db_url = _make_db(tmp_path)
    before = _read_configs(db_url)

    with pytest.raises(ValueError):
        SMTPPasswordManager(encryption_key=bad_key)
    # The script paths construct the manager first, so the same rejection
    # fires before any database work:
    with pytest.raises(ValueError):
        verify_key_works(db_url, bad_key)
    with pytest.raises(ValueError):
        rotate_keys(db_url, bad_key)

    assert _read_configs(db_url) == before


def test_candidate_key_validation_is_mode_independent(monkeypatch, tmp_path):
    # Unlike the environment path (warn-only in development), an explicit
    # candidate is rejected even in development mode — a rotation must never
    # complete with a key the production runtime would refuse.
    monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
    with pytest.raises(ValueError):
        SMTPPasswordManager(encryption_key="")
