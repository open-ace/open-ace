import importlib.util
import json
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "external_identity_test",
    Path(__file__).resolve().parents[2] / "app/modules/workspace/external_identity.py",
)
identity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(identity)


class IdentityProbeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.secret = self.root / "secret"
        self.secret.write_bytes(b"k" * 32)
        self.secret.chmod(0o600)
        self.policy_file = self.root / "policy.json"
        self.source = {
            "issuers": [
                {"id": "test-issuer", "secret_file": str(self.secret), "audience": "openace"}
            ],
            "mappings": [
                {
                    "issuer": "test-issuer",
                    "external_org": "1",
                    "external_user": "7",
                    "external_login": "alice",
                    "tenant_id": 3,
                    "user_id": 9,
                }
            ],
        }
        self.save_policy()
        self.replay = identity.ReplayStore(lambda: sqlite3.connect(self.root / "db.sqlite"))
        # The delegation tables ship via the authoritative schema/migrations;
        # create them here the same way instead of runtime DDL.
        from app.repositories.schema_init import load_schema_from_file

        load_schema_from_file(f"sqlite:///{self.root / 'db.sqlite'}")
        self.account = {"id": 9, "tenant_id": 3, "is_active": True}
        self.tenant = {"id": 3, "status": "active"}
        self.raw = b'{"organization":"1","user":"7","login":"alice"}'

    def tearDown(self):
        self.directory.cleanup()

    def save_policy(self):
        self.policy_file.write_text(json.dumps(self.source))
        self.policy_file.chmod(0o600)
        self.policy = identity.load_policy(str(self.policy_file))

    def headers(self, raw=None, nonce="a" * 48, stamp="1800000000"):
        raw = self.raw if raw is None else raw
        return {
            "X-ACE-Issuer": "test-issuer",
            "X-ACE-Time": stamp,
            "X-ACE-Nonce": nonce,
            "X-ACE-Signature": identity.signature(
                b"k" * 32, "test-issuer", stamp, nonce, raw, audience="openace"
            ),
        }

    def probe(self, headers=None, raw=None):
        return identity.verify_signed_request(
            self.policy,
            headers or self.headers(),
            self.raw if raw is None else raw,
            self.replay,
            lambda _: self.account,
            lambda _: self.tenant,
            now=1800000000,
        )

    def test_cross_language_vector_and_explicit_mapping(self):
        self.assertEqual(
            self.headers()["X-ACE-Signature"],
            "a554247797e3bc96f14e0c80a8c5da46e9a9efd9b4c9094a9629f62dfe140b73",
        )
        result = self.probe()
        self.assertEqual(result["identity"], {"tenant_id": 3, "user_id": 9})
        self.assertEqual(result["body"], {"organization": "1", "user": "7", "login": "alice"})
        # Capability presentation is the route's job; verification itself
        # returns only protocol/issuer/nonce/identity/body.
        self.assertNotIn("capabilities", result)
        self.assertNotIn("token", result)

    def test_replay_rejected_by_another_worker(self):
        self.probe()
        self.replay = identity.ReplayStore(lambda: sqlite3.connect(self.root / "db.sqlite"))
        # The delegation tables ship via the authoritative schema/migrations;
        # create them here the same way instead of runtime DDL.
        from app.repositories.schema_init import load_schema_from_file

        load_schema_from_file(f"sqlite:///{self.root / 'db.sqlite'}")
        with self.assertRaises(identity.DelegationDenied):
            self.probe()

    def test_replay_rejected_across_endpoints(self):
        """The nonce table's PK is (issuer, nonce) with no path column: a
        nonce consumed by a validly-signed /capabilities request must also
        deny a validly-signed /token request reusing the same nonce, not
        only a replay against the same endpoint."""
        nonce = "c" * 48
        self.probe(self.headers(nonce=nonce))  # consumes `nonce` via /capabilities

        token_raw = b'{"organization":"1","user":"7","login":"alice","provider":"openai"}'
        token_headers = {
            "X-ACE-Issuer": "test-issuer",
            "X-ACE-Time": "1800000000",
            "X-ACE-Nonce": nonce,
            "X-ACE-Signature": identity.signature(
                b"k" * 32,
                "test-issuer",
                "1800000000",
                nonce,
                token_raw,
                path=identity.TOKEN_PATH,
                audience="openace",
            ),
        }
        with self.assertRaises(identity.DelegationDenied):
            identity.verify_signed_request(
                self.policy,
                token_headers,
                token_raw,
                self.replay,
                lambda _: self.account,
                lambda _: self.tenant,
                now=1800000000,
                path=identity.TOKEN_PATH,
                expected_fields={"organization", "user", "login", "provider"},
                optional_fields={"ttl_seconds"},
            )

    def test_tamper_expiry_and_wrong_audience(self):
        for headers in [
            self.headers(stamp="1799999969"),
            self.headers(stamp="1800000031"),
            {**self.headers(), "X-ACE-Signature": "b" * 64},
            {
                **self.headers(),
                "X-ACE-Signature": identity.signature(
                    b"k" * 32,
                    "test-issuer",
                    "1800000000",
                    "a" * 48,
                    self.raw,
                    path="/api/other",
                    audience="openace",
                ),
            },
        ]:
            with self.assertRaises(identity.DelegationDenied):
                self.probe(headers)
        with self.assertRaises(identity.DelegationDenied):
            self.probe(self.headers(), self.raw.replace(b"alice", b"other"))
        self.probe()  # Failed signatures did not consume the valid nonce.

    def test_unknown_identity_and_duplicate_fields(self):
        for raw in [
            self.raw.replace(b'"1"', b'"2"'),
            self.raw.replace(b'"alice"', b'"other"'),
            b'{"organization":"1","user":"7","login":"alice","tenant_id":3}',
            b'{"organization":"1","user":"8","user":"7","login":"alice"}',
        ]:
            with self.assertRaises(identity.DelegationDenied):
                self.probe(self.headers(raw), raw)

    def test_unknown_issuer_still_runs_the_keyed_comparison(self):
        """L1: an unknown issuer must not short-circuit before hmac.compare_digest.

        Regression guard for a real bug: ``audience=audiences[issuer]`` (a
        direct dict index) raised ``KeyError`` for an unknown issuer name
        *before* ``signature()`` was ever called, skipping the decoy HMAC
        entirely and taking a different, faster code path than a known
        issuer with a bad signature -- exactly the timing side channel the
        dummy-secret comparison exists to close. Asserting the HTTP-level
        403 (as the route test does) does not catch this, because both the
        buggy and fixed code paths deny the request; only tracing whether
        the keyed comparison actually ran does.
        """
        calls = []
        real_signature = identity.signature

        def traced(*args, **kwargs):
            calls.append((args, kwargs))
            return real_signature(*args, **kwargs)

        with unittest.mock.patch.object(identity, "signature", traced):
            with self.assertRaises(identity.DelegationDenied):
                self.probe(
                    {
                        "X-ACE-Issuer": "no-such-issuer",
                        "X-ACE-Time": "1800000000",
                        "X-ACE-Nonce": "a" * 48,
                        "X-ACE-Signature": "a" * 64,
                    }
                )
        self.assertEqual(
            len(calls),
            1,
            "signature() must run the keyed comparison for unknown issuers too, "
            "against a decoy secret, so the failure path carries no timing signal",
        )

    def test_membership_and_account_revocation(self):
        cases = [
            ("is_active", False),
            ("tenant_id", 4),
            ("deleted_at", "yesterday"),
            ("must_change_password", True),
        ]
        for i, (key, value) in enumerate(cases):
            self.account = {"id": 9, "tenant_id": 3, "is_active": True, key: value}
            with self.assertRaises(identity.DelegationDenied):
                self.probe(self.headers(nonce=f"{i:048x}"))
        self.account = {"id": 9, "tenant_id": 3, "is_active": True}
        self.tenant["status"] = "suspended"
        with self.assertRaises(identity.DelegationDenied):
            self.probe()

    def test_policy_and_signing_key_removal(self):
        self.source["mappings"] = []
        self.save_policy()
        with self.assertRaises(identity.DelegationDenied):
            self.probe()
        self.secret.chmod(0o644)
        with self.assertRaises(ValueError):
            identity.load_policy(str(self.policy_file))

    def test_database_failure_fails_closed(self):
        def unavailable():
            raise RuntimeError("database unavailable")

        self.replay = identity.ReplayStore(unavailable)
        with self.assertRaises(RuntimeError):
            self.probe()


def test_policy_rejects_issuer_without_audience(tmp_path):
    import importlib.util
    from pathlib import Path

    root = tmp_path
    secret = root / "secret"
    secret.write_bytes(b"k" * 32)
    secret.chmod(0o600)
    policy_file = root / "policy.json"
    policy_file.write_text(
        json.dumps({"issuers": [{"id": "test-issuer", "secret_file": str(secret)}], "mappings": []})
    )
    policy_file.chmod(0o600)
    with pytest.raises(ValueError, match="Invalid issuer"):
        identity.load_policy(str(policy_file))


def test_wrong_audience_signature_rejected():
    case = IdentityProbeTest()
    case.setUp()
    try:
        raw = case.raw
        stamp = "1800000000"
        nonce = "a" * 48
        good = identity.signature(b"k" * 32, "test-issuer", stamp, nonce, raw, audience="openace")
        bad = identity.signature(b"k" * 32, "test-issuer", stamp, nonce, raw, audience="staging")
        assert good != bad
        headers = dict(case.headers())
        headers["X-ACE-Signature"] = bad
        with pytest.raises(identity.DelegationDenied):
            case.probe(headers)
        headers["X-ACE-Signature"] = good
        assert case.probe(headers)["identity"] == {"tenant_id": 3, "user_id": 9}
    finally:
        case.tearDown()
