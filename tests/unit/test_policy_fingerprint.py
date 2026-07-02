"""Unit tests for policy request fingerprinting & per-tool normalization.

Pure logic — no DB. Covers equivalence cases (must keep the same digest) and
drift cases (must change the digest), per review #3/#4 and the plan test matrix.
"""

from __future__ import annotations

from app.modules.policy.fingerprint import (
    PROFILE_BASH_COMMAND,
    PROFILE_FILE_PATH,
    PROFILE_GENERIC,
    build_fingerprint,
    compute_args_digest,
    compute_fingerprint_hash,
    extract_request_fields,
)
from app.modules.policy.models import RequestFingerprint

# ── equivalence (same digest) ───────────────────────────────────────────────


class TestBashNormalization:
    def test_reordered_leading_flags_same_digest(self):
        a = compute_args_digest(PROFILE_BASH_COMMAND, 1, {"command": "git -a -b commit"})
        b = compute_args_digest(PROFILE_BASH_COMMAND, 1, {"command": "git -b -a commit"})
        assert a == b

    def test_positional_order_preserved(self):
        a = compute_args_digest(PROFILE_BASH_COMMAND, 1, {"command": "mv src dst"})
        b = compute_args_digest(PROFILE_BASH_COMMAND, 1, {"command": "mv dst src"})
        assert a != b  # genuinely different → different digest

    def test_different_command_different_digest(self):
        a = compute_args_digest(PROFILE_BASH_COMMAND, 1, {"command": "rm -rf /tmp"})
        b = compute_args_digest(PROFILE_BASH_COMMAND, 1, {"command": "rm -rf /etc"})
        assert a != b


class TestFilePathNormalization:
    def test_home_expansion_same_digest_when_home_known(self):
        a = compute_args_digest(
            PROFILE_FILE_PATH, 1, {"file_path": "~/foo.txt"}, home_dir="/home/user"
        )
        b = compute_args_digest(
            PROFILE_FILE_PATH, 1, {"file_path": "/home/user/foo.txt"}, home_dir="/home/user"
        )
        assert a == b

    def test_tilde_token_stable_without_home(self):
        a = compute_args_digest(PROFILE_FILE_PATH, 1, {"file_path": "~/foo.txt"})
        b = compute_args_digest(PROFILE_FILE_PATH, 1, {"file_path": "~/foo.txt"})
        assert a == b

    def test_different_paths_different_digest(self):
        a = compute_args_digest(PROFILE_FILE_PATH, 1, {"file_path": "/etc/passwd"})
        b = compute_args_digest(PROFILE_FILE_PATH, 1, {"file_path": "/etc/shadow"})
        assert a != b

    def test_trailing_slash_folded(self):
        a = compute_args_digest(PROFILE_FILE_PATH, 1, {"file_path": "/app/dir"})
        b = compute_args_digest(PROFILE_FILE_PATH, 1, {"file_path": "/app/dir/"})
        assert a == b


class TestGenericNormalization:
    def test_key_order_irrelevant(self):
        a = compute_args_digest(PROFILE_GENERIC, 1, {"a": 1, "b": 2})
        b = compute_args_digest(PROFILE_GENERIC, 1, {"b": 2, "a": 1})
        assert a == b

    def test_generic_does_not_fold_paths(self):
        # generic profile leaves paths untouched: ~/foo != /home/u/foo here
        a = compute_args_digest(PROFILE_GENERIC, 1, {"path": "~/foo"})
        b = compute_args_digest(PROFILE_GENERIC, 1, {"path": "/home/u/foo"})
        assert a != b


# ── fingerprint hash ────────────────────────────────────────────────────────


class TestFingerprintHash:
    def test_hash_recomputable(self):
        fp = RequestFingerprint(
            tool="Bash",
            action="permission",
            args_digest="d1",
            normalization_profile_id=PROFILE_BASH_COMMAND,
            normalization_profile_version=1,
            machine_id="m1",
            workspace_scope="/p",
            resource_target="rm -rf /tmp",
            policy_rule_id=7,
            policy_rule_version=2,
        )
        h1 = compute_fingerprint_hash(fp)
        h2 = compute_fingerprint_hash(fp)
        assert h1 == h2 and len(h1) == 64

    def test_drift_changes_hash(self):
        base = RequestFingerprint(
            tool="Bash",
            args_digest="d1",
            normalization_profile_id=PROFILE_BASH_COMMAND,
            normalization_profile_version=1,
            machine_id="m1",
            policy_rule_id=1,
            policy_rule_version=1,
        )
        h_base = compute_fingerprint_hash(base)
        # machine drift
        other = RequestFingerprint(
            tool="Bash",
            args_digest="d1",
            normalization_profile_id=PROFILE_BASH_COMMAND,
            normalization_profile_version=1,
            machine_id="m2",
            policy_rule_id=1,
            policy_rule_version=1,
        )
        assert compute_fingerprint_hash(other) != h_base
        # args drift
        other_args = RequestFingerprint(
            tool="Bash",
            args_digest="d2",
            normalization_profile_id=PROFILE_BASH_COMMAND,
            normalization_profile_version=1,
            machine_id="m1",
            policy_rule_id=1,
            policy_rule_version=1,
        )
        assert compute_fingerprint_hash(other_args) != h_base
        # policy version drift
        other_pv = RequestFingerprint(
            tool="Bash",
            args_digest="d1",
            normalization_profile_id=PROFILE_BASH_COMMAND,
            normalization_profile_version=1,
            machine_id="m1",
            policy_rule_id=1,
            policy_rule_version=2,
        )
        assert compute_fingerprint_hash(other_pv) != h_base
        # normalization profile drift → not comparable
        other_profile = RequestFingerprint(
            tool="Bash",
            args_digest="d1",
            normalization_profile_id=PROFILE_GENERIC,
            normalization_profile_version=1,
            machine_id="m1",
            policy_rule_id=1,
            policy_rule_version=1,
        )
        assert compute_fingerprint_hash(other_profile) != h_base


# ── control_request extraction ──────────────────────────────────────────────


class TestExtraction:
    def test_extracts_request_id_tool_action_args(self):
        cr = {
            "type": "control_request",
            "request_id": "rq-1",
            "request": {
                "subtype": "permission",
                "tool_name": "Bash",
                "input": {"command": "ls -la"},
            },
        }
        f = extract_request_fields(cr)
        assert f["request_id"] == "rq-1"
        assert f["tool"] == "Bash"
        assert f["action"] == "permission"
        assert f["command"] == "ls -la"
        assert f["resource_target"] == "ls -la"

    def test_extracts_file_path(self):
        cr = {
            "request": {
                "subtype": "permission",
                "tool_name": "Write",
                "input": {"file_path": "/app/.env"},
            }
        }
        f = extract_request_fields(cr)
        assert f["file_path"] == "/app/.env"
        assert f["resource_target"] == "/app/.env"

    def test_build_fingerprint_picks_bash_profile_for_command(self):
        cr = {
            "request": {
                "subtype": "permission",
                "tool_name": "Bash",
                "input": {"command": "git -a -b"},
            }
        }
        fp, digest, target = build_fingerprint(cr, machine_id="m1")
        assert fp.normalization_profile_id == PROFILE_BASH_COMMAND
        assert fp.machine_id == "m1"
        assert target == "git -a -b"
        assert digest and len(digest) == 64

    def test_build_fingerprint_picks_file_profile_for_path(self):
        cr = {
            "request": {
                "subtype": "permission",
                "tool_name": "Edit",
                "input": {"file_path": "/a/b"},
            }
        }
        fp, _, _ = build_fingerprint(cr)
        assert fp.normalization_profile_id == PROFILE_FILE_PATH
