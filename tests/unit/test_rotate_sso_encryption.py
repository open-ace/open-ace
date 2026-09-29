"""Rotation-script regression tests from the PR #3386 review rounds.

Covers the failure modes the reviews reproduced:

1. Mid-write failure atomicity: ``rotate_keys`` used to autocommit each
   UPDATE; a failure on the second row left the first permanently rotated —
   unrecoverable with a single key switch. All writes (now across ALL stores
   sharing the key) must share one transaction and roll back entirely.
2. Explicit candidate-key validation: ``SMTPPasswordManager(encryption_key=...)``
   must reject empty/weak/short candidates with the runtime's own rules
   BEFORE any write.
3. Multi-store coverage: the rotation must re-encrypt EVERY store bound to
   OPENACE_ENCRYPTION_KEY (api_key_store, smtp_settings, model_gateway_config,
   dingtalk/feishu/webhook settings, notification_preferences, sso_providers)
   — rotating SSO alone would brick every other store on the key switch —
   while leaving key-registry ("v1k<id>:" prefixed) values untouched.
"""

import json
import sqlite3

import pytest

from app.utils.smtp_crypto import SMTPPasswordManager
from scripts.rotate_sso_encryption import postcheck_new_key, rotate_keys, verify_key_works

OLD_KEY = "unit-old-encryption-key-0123456789abcdef"
NEW_KEY = "unit-new-encryption-key-9876543210fedcba"
SECRETS = {"first": "secret-one", "second": "secret-two"}

# Every (table, column) in the script's rotation spec; the fixture mirrors it
# so a spec entry silently missing from the fixture (or vice versa) fails loud.
ALL_TABLES = [
    "api_key_store",
    "smtp_settings",
    "model_gateway_config",
    "dingtalk_settings",
    "feishu_settings",
    "webhook_settings",
    "notification_preferences",
    "sso_providers",
]

REGISTRY_FORMAT_VALUE = "v1k5:gAAAAABregistrydataregistrydatregistry="


@pytest.fixture(autouse=True)
def _old_key_env(monkeypatch):
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", OLD_KEY)


def _make_db(tmp_path, fail_table=None):
    db_path = tmp_path / "sso.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE api_key_store (id INTEGER PRIMARY KEY, encrypted_key TEXT, name TEXT)"
    )
    conn.execute("CREATE TABLE smtp_settings (id INTEGER PRIMARY KEY, encrypted_password TEXT)")
    conn.execute(
        "CREATE TABLE model_gateway_config (id INTEGER PRIMARY KEY, encrypted_api_key TEXT)"
    )
    conn.execute(
        "CREATE TABLE dingtalk_settings (app_key TEXT, app_secret_enc TEXT, fallback_webhook_secret_enc TEXT)"
    )
    conn.execute("CREATE TABLE feishu_settings (app_id TEXT, app_secret_enc TEXT)")
    conn.execute("CREATE TABLE webhook_settings (webhook_secret_enc TEXT)")
    conn.execute(
        "CREATE TABLE notification_preferences (user_id INTEGER PRIMARY KEY, dingtalk_webhook_secret TEXT)"
    )
    conn.execute("CREATE TABLE sso_providers (name TEXT PRIMARY KEY, config TEXT)")

    pm = SMTPPasswordManager()
    # Direct-column stores: one real value, one NULL, one empty, one
    # registry-format value that must be skipped everywhere.
    conn.execute(
        "INSERT INTO api_key_store (encrypted_key, name) VALUES (?, ?)",
        (pm.encrypt("api-key-plain"), "primary"),
    )
    conn.execute(
        "INSERT INTO api_key_store (encrypted_key, name) VALUES (?, ?)",
        (REGISTRY_FORMAT_VALUE, "registry-migrated"),
    )
    conn.execute("INSERT INTO api_key_store (encrypted_key, name) VALUES (NULL, 'none')")
    conn.execute(
        "INSERT INTO smtp_settings (encrypted_password) VALUES (?)", (pm.encrypt("smtp-pass"),)
    )
    conn.execute("INSERT INTO smtp_settings (encrypted_password) VALUES ('')")
    conn.execute(
        "INSERT INTO model_gateway_config (encrypted_api_key) VALUES (?)", (pm.encrypt("gw-key"),)
    )
    conn.execute(
        "INSERT INTO dingtalk_settings (app_key, app_secret_enc, fallback_webhook_secret_enc) VALUES (?, ?, ?)",
        ("dk-1", pm.encrypt("dt-app-secret"), pm.encrypt("dt-fallback")),
    )
    conn.execute(
        "INSERT INTO dingtalk_settings (app_key, app_secret_enc, fallback_webhook_secret_enc) VALUES (?, ?, ?)",
        ("dk-2", REGISTRY_FORMAT_VALUE, None),
    )
    conn.execute(
        "INSERT INTO feishu_settings (app_id, app_secret_enc) VALUES (?, ?)",
        ("fs-1", pm.encrypt("fs-secret")),
    )
    conn.execute(
        "INSERT INTO webhook_settings (webhook_secret_enc) VALUES (?)", (pm.encrypt("wh-secret"),)
    )
    conn.execute(
        "INSERT INTO notification_preferences (user_id, dingtalk_webhook_secret) VALUES (?, ?)",
        (1, pm.encrypt("user-dt-secret")),
    )
    for name, secret in SECRETS.items():
        conn.execute(
            "INSERT INTO sso_providers (name, config) VALUES (?, ?)",
            (
                name,
                json.dumps(
                    {"client_id": f"{name}-id", "client_secret_encrypted": pm.encrypt(secret)}
                ),
            ),
        )

    if fail_table:
        # Reproduces the review's mid-write failure: any UPDATE on the given
        # table aborts (fires after the first table's rows were written).
        conn.execute(f"""
            CREATE TRIGGER fail_{fail_table}_update BEFORE UPDATE ON {fail_table}
            BEGIN SELECT RAISE(ABORT, 'injected mid-write failure'); END
            """)
    conn.commit()
    conn.close()
    return f"sqlite:///{db_path}"


def _dump(db_url):
    path = db_url.replace("sqlite:///", "", 1)
    conn = sqlite3.connect(path)
    dump = {}
    for table in ALL_TABLES:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        cols = [d[0] for d in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
        dump[table] = [dict(zip(cols, r)) for r in rows]
    conn.close()
    return dump


def _decrypt_all_with(dump, key):
    """Return {store_label: plaintext} for every non-registry ciphertext."""
    pm = SMTPPasswordManager(encryption_key=key)
    found = {}
    for row in dump["api_key_store"]:
        v = row["encrypted_key"]
        if v and not v.startswith("v1k"):
            found[f"api_key_store/{row['name']}"] = pm.decrypt(v)
    for row in dump["smtp_settings"]:
        if row["encrypted_password"]:
            found[f"smtp_settings/{row['id']}"] = pm.decrypt(row["encrypted_password"])
    if dump["model_gateway_config"][0]["encrypted_api_key"]:
        found["model_gateway_config"] = pm.decrypt(
            dump["model_gateway_config"][0]["encrypted_api_key"]
        )
    for row in dump["dingtalk_settings"]:
        for col in ("app_secret_enc", "fallback_webhook_secret_enc"):
            v = row[col]
            if v and not v.startswith("v1k"):
                found[f"dingtalk/{row['app_key']}/{col}"] = pm.decrypt(v)
    for row in dump["feishu_settings"]:
        if row["app_secret_enc"]:
            found[f"feishu/{row['app_id']}"] = pm.decrypt(row["app_secret_enc"])
    if dump["webhook_settings"][0]["webhook_secret_enc"]:
        found["webhook_settings"] = pm.decrypt(dump["webhook_settings"][0]["webhook_secret_enc"])
    if dump["notification_preferences"][0]["dingtalk_webhook_secret"]:
        found["notification_preferences"] = pm.decrypt(
            dump["notification_preferences"][0]["dingtalk_webhook_secret"]
        )
    for row in dump["sso_providers"]:
        enc = json.loads(row["config"])["client_secret_encrypted"]
        if enc:
            found[f"sso/{row['name']}"] = pm.decrypt(enc)
    return found


EXPECTED_PLAINTEXTS = {
    "api_key_store/primary": "api-key-plain",
    "smtp_settings/1": "smtp-pass",
    "model_gateway_config": "gw-key",
    "dingtalk/dk-1/app_secret_enc": "dt-app-secret",
    "dingtalk/dk-1/fallback_webhook_secret_enc": "dt-fallback",
    "feishu/fs-1": "fs-secret",
    "webhook_settings": "wh-secret",
    "notification_preferences": "user-dt-secret",
    "sso/first": SECRETS["first"],
    "sso/second": SECRETS["second"],
}


def test_rotation_covers_every_shared_store(tmp_path):
    db_url = _make_db(tmp_path)

    success, count, failed = rotate_keys(db_url, NEW_KEY)

    assert (success, failed) == (True, [])
    # every non-registry value across every store rotated to the new key
    assert _decrypt_all_with(_dump(db_url), NEW_KEY) == EXPECTED_PLAINTEXTS
    # registry-format values untouched (bound to OPENACE_ENCRYPTION_KEYS, not this key)
    dump = _dump(db_url)
    registry_rows = [r for r in dump["api_key_store"] if r["name"] == "registry-migrated"]
    assert registry_rows[0]["encrypted_key"] == REGISTRY_FORMAT_VALUE
    assert [r for r in dump["dingtalk_settings"] if r["app_key"] == "dk-2"][0][
        "app_secret_enc"
    ] == REGISTRY_FORMAT_VALUE
    # postcheck (the operator's go/no-go signal) is clean
    assert postcheck_new_key(db_url, NEW_KEY) == []
    assert count == len(EXPECTED_PLAINTEXTS)


def test_mid_write_failure_rolls_back_every_store(tmp_path):
    # The trigger fires on the smtp_settings table while api_key_store rows
    # were already staged in the same transaction.
    db_url = _make_db(tmp_path, fail_table="smtp_settings")
    before = _dump(db_url)

    success, count, failed = rotate_keys(db_url, NEW_KEY)

    assert success is False
    assert count == 0
    # Atomicity: NOTHING changed in ANY store — every value still decrypts
    # with the old key, so the operator can retry (or switch keys once).
    assert _dump(db_url) == before
    assert _decrypt_all_with(_dump(db_url), OLD_KEY) == EXPECTED_PLAINTEXTS
    # ...and a retry pre-flight against the old key still passes.
    assert verify_key_works(db_url, NEW_KEY)[0] is True


def test_preflight_names_the_failing_store(tmp_path):
    db_url = _make_db(tmp_path)
    path = db_url.replace("sqlite:///", "", 1)
    conn = sqlite3.connect(path)
    conn.execute(
        "UPDATE api_key_store SET encrypted_key = ? WHERE name = 'primary'", ("gAAAAACorrupt",)
    )
    conn.commit()
    conn.close()

    success, failed = verify_key_works(db_url, NEW_KEY)

    assert success is False
    assert failed == ["api_key_store.encrypted_key"]


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
    before = _dump(db_url)

    with pytest.raises(ValueError):
        SMTPPasswordManager(encryption_key=bad_key)
    # The script paths construct the manager first, so the same rejection
    # fires before any database work:
    with pytest.raises(ValueError):
        verify_key_works(db_url, bad_key)
    with pytest.raises(ValueError):
        rotate_keys(db_url, bad_key)

    assert _dump(db_url) == before


def test_candidate_key_validation_is_mode_independent(monkeypatch):
    # Unlike the environment path (warn-only in development), an explicit
    # candidate is rejected even in development mode — a rotation must never
    # complete with a key the production runtime would refuse.
    monkeypatch.setenv("OPENACE_SECURITY_MODE", "development")
    with pytest.raises(ValueError):
        SMTPPasswordManager(encryption_key="")


def test_short_legacy_old_key_migrates_to_strong_new_key(tmp_path, monkeypatch):
    """Regression (PR #3386 review): a legacy deployment with a SHORT old key
    must still be able to rotate AWAY from it — rejecting the old key strands
    non-compliant deployments on the weak key forever, defeating the tool's
    recovery purpose. Only the NEW key must satisfy strength rules."""
    legacy_old = "legacy-short-key"  # 16 chars — decrypts fine, fails strength
    monkeypatch.setenv("OPENACE_ENCRYPTION_KEY", legacy_old)
    db_url = _make_db(tmp_path)

    # pre-flight passes with the short legacy key
    assert verify_key_works(db_url, NEW_KEY)[0] is True
    # rotation completes: every value decrypts with the strong new key
    success, count, failed = rotate_keys(db_url, NEW_KEY)
    assert (success, failed) == (True, [])
    assert _decrypt_all_with(_dump(db_url), NEW_KEY) == EXPECTED_PLAINTEXTS


def test_same_key_noop_rotation_is_refused(tmp_path):
    """Regression (PR #3386 review): rotating to the SAME key must fail
    closed — a "successful" no-op re-encryption reports a false completion
    signal in a key-compromise response."""
    db_url = _make_db(tmp_path)
    before = _dump(db_url)

    assert verify_key_works(db_url, OLD_KEY) == (False, ["<same-key-noop>"])
    assert rotate_keys(db_url, OLD_KEY) == (False, 0, ["<same-key-noop>"])

    # nothing was rewritten
    assert _dump(db_url) == before


def test_key_management_docs_route_rotation_through_atomic_script_only():
    """P1 (PR #3386 R15 review): export_encrypted_data.py /
    import_encrypted_data.py cover only api_key_store / smtp_settings /
    model_gateway_config. A 'Recommended' rotation Method A built on them
    leaves sso_providers, dingtalk/feishu/webhook settings and
    notification_preferences encrypted under the OLD key — undecryptable
    after the env switch. Both language docs must route rotation through
    the atomic full-store script and must not describe the key as
    protecting 'three data stores'."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    for lang in ("cn", "en"):
        doc = (repo_root / "docs" / lang / "KEY_MANAGEMENT.md").read_text(encoding="utf-8")
        assert "export_encrypted_data.py" not in doc, (
            f"docs/{lang}/KEY_MANAGEMENT.md still recommends the 3-store "
            "export/import flow — 5 more stores would brick on the key switch"
        )
        assert "import_encrypted_data.py" not in doc, (
            f"docs/{lang}/KEY_MANAGEMENT.md: import flow is not a valid "
            "rotation path for the full store set"
        )
        for stale in ("三个数据存储", "three data stores"):
            assert stale not in doc, (
                f"docs/{lang}/KEY_MANAGEMENT.md: overview still claims the key "
                "protects only three data stores"
            )
        assert (
            "rotate_sso_encryption.py" in doc
        ), f"docs/{lang}/KEY_MANAGEMENT.md must point rotation at the atomic script"
        # The OPENING overview must match the authoritative 8-store list (PR
        # #3386 R16 review): a three-table summary up top misleads skim-readers
        # about the blast radius long before the impact section corrects it.
        head = doc[: doc.index("## 密钥共享影响面" if lang == "cn" else "## Key Sharing Impact")]
        assert "sso_providers" in head and "webhook_settings" in head, (
            f"docs/{lang}/KEY_MANAGEMENT.md: opening overview/diagram still "
            "lists only the three legacy stores"
        )


def _insert_api_key(db_url: str, ciphertext: str, name: str) -> None:
    import sqlite3

    conn = sqlite3.connect(db_url.replace("sqlite:///", ""))
    conn.execute(
        "INSERT INTO api_key_store (encrypted_key, name) VALUES (?, ?)", (ciphertext, name)
    )
    conn.commit()
    conn.close()


def test_rotate_rejects_rowcount_mismatch_from_concurrent_modification(tmp_path):
    """P1 (PR #3386 R16 review): each planned UPDATE targets exactly one row
    (Fernet ciphertexts are unique per encryption). An affected-row count !=
    1 means a concurrent writer changed the row after the scan — the
    rotation must roll back entirely instead of silently skipping it."""
    db_url = _make_db(tmp_path)

    # Two rows sharing one ciphertext: only possible via direct DB write
    # (Fernet never duplicates) — models a raced/duplicated value; the
    # UPDATE ... WHERE col = ? would affect 2 rows.
    import sqlite3

    conn = sqlite3.connect(db_url.replace("sqlite:///", ""))
    ciphertext = conn.execute(
        "SELECT encrypted_key FROM api_key_store WHERE name = 'primary'"
    ).fetchone()[0]
    conn.close()
    _insert_api_key(db_url, ciphertext, "duplicate")

    ok, count, failures = rotate_keys(db_url, NEW_KEY)

    assert ok is False
    assert count == 0
    # everything still decrypts under the OLD key (full rollback)
    assert postcheck_new_key(db_url, OLD_KEY) == []


def test_rotate_picks_up_rows_written_between_preflight_and_rotation(tmp_path):
    """PR #3386 R16/R17 review: the scan happens at rotation time (inside
    rotate_keys), not cached from any earlier pre-flight probe — rows that
    exist when the rotation starts are included in the same atomic pass.
    NOTE: this is NOT a concurrency guarantee — rows inserted after a
    table's scan inside the transaction are still invisible (READ COMMITTED
    statement snapshots, no table locks); live-deployment safety rests on
    the documented stop-writers procedure."""
    from app.utils.smtp_crypto import SMTPPasswordManager

    db_url = _make_db(tmp_path)

    pm_old = SMTPPasswordManager.for_legacy_rotation_key(OLD_KEY)
    _insert_api_key(db_url, pm_old.encrypt("late-secret"), "late")

    ok, count, failures = rotate_keys(db_url, NEW_KEY)

    assert ok is True, failures
    assert count >= 3  # seeded values + the late row
    assert postcheck_new_key(db_url, NEW_KEY) == []


def test_postcheck_detects_old_key_row_written_after_rotation(tmp_path):
    """PR #3386 R16/R17 review: a writer still on the old key that inserts
    AFTER the rotation commits is REPORTED by the new-key post-check —
    mixed-key data is surfaced, never silently accepted. Detection only:
    postcheck cannot roll back committed data, which is exactly why the
    runbook requires writers to be stopped before rotating."""
    from app.utils.smtp_crypto import SMTPPasswordManager

    db_url = _make_db(tmp_path)
    ok, count, _ = rotate_keys(db_url, NEW_KEY)
    assert ok is True

    pm_old = SMTPPasswordManager.for_legacy_rotation_key(OLD_KEY)
    _insert_api_key(db_url, pm_old.encrypt("straggler-secret"), "straggler")

    assert postcheck_new_key(db_url, NEW_KEY)  # non-empty = residual detected


def test_sso_update_is_optimistic_on_the_original_config():
    """P2 (PR #3386 R16/R17 review): the sso_providers UPDATE must compare
    the ORIGINAL config in its WHERE clause (not just the name — rowcount
    would stay 1 and silently overwrite a concurrent config change with the
    stale snapshot). Together with the rowcount==1 guard this turns a
    concurrent modification into a full rollback."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "scripts" / "rotate_sso_encryption.py").read_text(
        encoding="utf-8"
    )
    assert (
        "UPDATE sso_providers SET config = ? WHERE name = ? AND config = ?" in src
    ), "sso_providers UPDATE must be optimistic on the original config text"
