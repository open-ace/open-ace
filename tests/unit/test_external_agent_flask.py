"""Agent bridge contracts: real Flask, HMAC and SQLite; native runtime fakes.

These tests do not certify Linux UID/process/file isolation or production SSO.
"""

import copy
import json
import os
import secrets
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace

import pytest

from app.modules.workspace.external_agent import PATH, HandoffStore, permitted_snapshot
from app.modules.workspace.external_identity import DelegationDenied, signature
from app.services.workspace_isolation_contract import ENTRY_POINT_DETAILS
from tests.unit.bound_family_app import bound_app  # noqa: F401
from tests.unit.bound_family_app import external  # noqa: F401

POLICY_REVISION = "2026-09-16.1"


@pytest.fixture
def agent(external, bound_app, tmp_path, monkeypatch):
    _, api, state, _ = external
    client = bound_app[0]
    from app.modules.workspace import session_manager
    from app.repositories import user_repo
    from app.services import webui_manager

    account = {"id": 9, "tenant_id": 3, "is_active": True, "system_account": "ace_alice"}
    bob = {**account, "id": 10, "system_account": "ace_bob"}
    accounts = {9: account, 10: bob}
    monkeypatch.setattr(
        user_repo,
        "UserRepository",
        lambda: SimpleNamespace(get_user_by_id=lambda user: accounts.get(user)),
    )
    snapshot = {
        "local_workspace_multi_user": "supported",
        "backend": "qwen-code-webui-per-user",
        "isolation_level": "os_user",
        "enforced": ["identity", "filesystem", "environment", "process"],
        "unsupported": ["resources", "network_egress", "kernel"],
        "reasons": [],
        "entry_points": {
            "webui": "enforced",
            "filesystem_api": "enforced",
            "session_history": "enforced",
            "terminal": "remote_machine_scope",
            "vscode": "remote_machine_scope",
            "autonomous": "separate_contract",
        },
        "entry_point_details": copy.deepcopy(ENTRY_POINT_DETAILS),
        "policy_revision": POLICY_REVISION,
    }
    config = SimpleNamespace(required_isolation_level="os_user")
    runtime = SimpleNamespace(config=config)
    monkeypatch.setattr(webui_manager, "get_webui_manager", lambda: runtime)
    contract = ModuleType("app.services.workspace_isolation_contract")
    contract.build_workspace_isolation_snapshot = lambda manager: SimpleNamespace(
        public_dict=lambda: copy.deepcopy(snapshot)
    )

    def evaluate(*args, **kwargs):
        if args[0] == "sandboxed":
            assert kwargs["system_account"] is None
        else:
            assert kwargs["system_account"] in ("ace_alice", "ace_bob")
        return None

    contract.evaluate_isolation_requirement = evaluate
    monkeypatch.setitem(sys.modules, contract.__name__, contract)
    rows = {
        "alice-session": {
            "session_id": "alice-session",
            "tenant_id": 3,
            "user_id": 9,
            "workspace_type": "local",
            "session_type": "chat",
            "tool_name": "qwen",
            "project_path": "/workspace/alice",
            "status": "active",
            "title": "Alice",
            "created_at": "2026-09-16T00:00:00Z",
        },
        "bob-session": {
            "session_id": "bob-session",
            "tenant_id": 3,
            "user_id": 10,
            "workspace_type": "local",
            "session_type": "chat",
            "tool_name": "qwen",
            "project_path": "/workspace/bob",
            "status": "active",
            "title": "Bob",
            "created_at": "2026-09-16T00:00:00Z",
        },
    }

    def get(session_id, **kwargs):
        row = rows.get(session_id)
        return SimpleNamespace(to_dict=lambda: copy.deepcopy(row)) if row else None

    def listing(**kwargs):
        assert kwargs == {
            "user_id": 9,
            "tenant_id": 3,
            "tool_name": "qwen",
            "session_type": "chat",
            "page": 1,
            "limit": 50,
        }
        # Even a mis-filtered repository result must not disclose foreign rows.
        return {"sessions": list(rows.values())}

    manager = SimpleNamespace(get_session=get, list_sessions=listing)
    monkeypatch.setattr(session_manager, "get_session_manager", lambda: manager)
    policy = {
        "required_isolation": "os_user",
        "accepted_revisions": [POLICY_REVISION],
        "acceptance_ref": "synthetic-test-not-acceptance",
        "subjects": [{"issuer": "client", "organization": "1", "user": "7", "login": "alice"}],
    }
    path = tmp_path / "agent-policy.json"
    path.write_text(json.dumps(policy))
    path.chmod(0o600)
    monkeypatch.setenv("OPENACE_EXTERNAL_AGENT_POLICY_FILE", str(path))
    monkeypatch.setenv("OPENACE_EXTERNAL_AGENT_ENABLED", "1")

    def call(operation, parameters=None, *, nonce=None, headers=None):
        if parameters is None:
            parameters = (
                {}
                if operation == "capabilities"
                else {"required_isolation": "os_user", "accepted_revisions": [POLICY_REVISION]}
            )
            if operation == "launch":
                parameters["request_id"] = secrets.token_hex(24)
            if operation == "open":
                parameters["session_id"] = "alice-session"
        raw = json.dumps(
            {
                "organization": "1",
                "user": "7",
                "login": "alice",
                "operation": operation,
                "parameters": parameters,
            }
        ).encode()
        nonce = nonce or secrets.token_hex(24)
        stamp = str(int(time.time()))
        signed = {
            "Content-Type": "application/json",
            "X-ACE-Issuer": "client",
            "X-ACE-Time": stamp,
            "X-ACE-Nonce": nonce,
            "X-ACE-Signature": signature(
                b"k" * 32, "client", stamp, nonce, raw, path=PATH, audience="openace"
            ),
        }
        signed.update(headers or {})
        return client.application.test_client(use_cookies=False).post(
            PATH, data=raw, headers=signed
        )

    def handoff(ticket, authenticated=True):
        return client.get(
            PATH + "/handoff?ticket=" + ticket,
            headers={"Authorization": "Bearer session-test-token"} if authenticated else {},
        )

    return SimpleNamespace(
        call=call,
        handoff=handoff,
        api=api,
        state=state,
        snapshot=snapshot,
        account=account,
        config=config,
        rows=rows,
        policy=policy,
        policy_path=path,
        client=client,
    )


def test_agent_signed_list_and_ticket_requires_same_native_login(agent):
    assert agent.call("capabilities").json["result"]["capabilities"]["available"]
    listed = agent.call("sessions")
    assert [r["id"] for r in listed.json["result"]["sessions"]] == ["alice-session"]
    assert b"project_path" not in listed.data and b"/workspace/" not in listed.data
    response = agent.call("launch")
    assert response.status_code == 200, response.json
    ticket = response.json["result"]["ticket"]
    assert agent.handoff(ticket, False).status_code == 401
    agent.state["user"].update(id=10, role="admin")
    assert agent.handoff(ticket).status_code == 403  # even an administrator
    agent.state["user"].update(id=9)
    result = agent.handoff(ticket)
    assert result.status_code == 303 and result.headers["Location"] == "/work/workspace"
    assert result.headers["Referrer-Policy"] == "no-referrer"
    assert agent.handoff(ticket).status_code == 403


def test_agent_handoff_redirects_browsers_without_native_login(agent):
    response = agent.call("launch")
    ticket = response.json["result"]["ticket"]
    denied = agent.client.get(
        PATH + "/handoff?ticket=" + ticket,
        headers={"Accept": "text/html,application/xhtml+xml"},
    )
    assert denied.status_code == 303 and denied.headers["Location"] == "/login"
    assert denied.headers["Referrer-Policy"] == "no-referrer"
    # The redirect never consumes the ticket; after signing in the same
    # one-use entry still works.
    result = agent.handoff(ticket)
    assert result.status_code == 303 and result.headers["Location"] == "/work/workspace"


@pytest.mark.parametrize(
    "drift",
    [
        "unsupported",
        "partial",
        "revision",
        "provisional",
        "floor",
        "mapping",
        "disabled-account",
        "acceptance",
        "grant",
    ],
)
def test_agent_runtime_and_policy_drift_denies_before_navigation(agent, drift):
    ticket = agent.call("launch").json["result"]["ticket"]
    if drift == "unsupported":
        agent.snapshot["isolation_level"] = "none"
    elif drift == "partial":
        agent.snapshot["entry_points"]["filesystem_api"] = "partial"
    elif drift == "revision":
        agent.snapshot["policy_revision"] = "new-unreviewed"
    elif drift == "provisional":
        agent.snapshot["reasons"] = [{"code": "launch_path_unverified", "message": ""}]
    elif drift == "floor":
        agent.config.required_isolation_level = ""
    elif drift == "mapping":
        agent.account["system_account"] = ""
    elif drift == "disabled-account":
        agent.account["is_active"] = False
    else:
        if drift == "acceptance":
            agent.policy["acceptance_ref"] = ""
        else:
            agent.policy["subjects"] = []
        agent.policy_path.write_text(json.dumps(agent.policy))
    assert agent.handoff(ticket).status_code == 403
    assert agent.call("launch").status_code == 403


@pytest.mark.parametrize(
    "drift",
    [
        "missing-map",
        "malformed-map",
        "missing-detail",
        "malformed-detail",
        "status-mismatch",
        "coverage-false",
        "limitation",
    ],
)
def test_agent_entry_point_detail_contract_fails_closed(agent, drift):
    details = agent.snapshot["entry_point_details"]
    if drift == "missing-map":
        del agent.snapshot["entry_point_details"]
    elif drift == "malformed-map":
        agent.snapshot["entry_point_details"] = []
    elif drift == "missing-detail":
        del details["filesystem_api"]
    elif drift == "malformed-detail":
        details["filesystem_api"] = []
    elif drift == "status-mismatch":
        details["filesystem_api"]["status"] = "partial"
    elif drift == "coverage-false":
        details["filesystem_api"]["covered_by_isolation_level"] = False
    else:
        details["filesystem_api"]["limitations"] = [
            {"code": "uncovered_write_path", "message": "A real local isolation gap."}
        ]
    assert not permitted_snapshot(agent.snapshot, "os_user", [POLICY_REVISION])
    assert agent.call("launch").status_code == 403


@pytest.mark.parametrize(
    "entry", ["webui", "filesystem_api", "session_history", "terminal", "vscode", "autonomous"]
)
def test_agent_unknown_status_on_any_entry_point_fails_closed(agent, entry):
    agent.snapshot["entry_points"][entry] = "future_unreviewed_status"
    assert not permitted_snapshot(agent.snapshot, "os_user", [POLICY_REVISION])
    assert agent.call("launch").status_code == 403


def test_agent_real_residual_objects_do_not_block_admission(agent):
    residuals = agent.snapshot["entry_point_details"]["filesystem_api"]["residuals"]
    assert residuals
    assert all(set(item) == {"code", "message"} for item in residuals)
    assert permitted_snapshot(agent.snapshot, "os_user", [POLICY_REVISION])
    assert agent.call("launch").status_code == 200


@pytest.mark.parametrize(
    "invalid", ["other-user", "other-tenant", "remote", "terminal", "no-path", "invalid-path-id"]
)
def test_agent_session_references_are_not_authority(agent, invalid):
    row = agent.rows["alice-session"]
    if invalid == "other-user":
        row["user_id"] = 10
    elif invalid == "other-tenant":
        row["tenant_id"] = 4
    elif invalid == "remote":
        row["workspace_type"] = "remote"
    elif invalid == "terminal":
        row["session_type"] = "terminal"
    elif invalid == "no-path":
        row["project_path"] = ""
    if invalid == "invalid-path-id":
        result = agent.call(
            "open",
            {
                "required_isolation": "os_user",
                "accepted_revisions": [POLICY_REVISION],
                "session_id": "../alice-session",
            },
        )
    else:
        result = agent.call("open")
    assert result.status_code == 403


def test_agent_resume_rechecks_owner_and_encodes_native_fields(agent, monkeypatch):
    from flask import jsonify

    native = ModuleType("app.routes.workspace")
    native.restore_session = lambda session_id: jsonify(
        {
            "success": True,
            "data": {
                "session_id": session_id,
                "encoded_project_name": "-workspace-alice&permissionMode=yolo",
                "tool_name": "qwen",
            },
        }
    )
    monkeypatch.setitem(sys.modules, native.__name__, native)
    ticket = agent.call("open").json["result"]["ticket"]
    response = agent.handoff(ticket)
    assert response.status_code == 303
    assert "%26permissionMode%3Dyolo" in response.headers["Location"]
    ticket = agent.call("open").json["result"]["ticket"]
    agent.rows["alice-session"]["user_id"] = 10
    assert agent.handoff(ticket).status_code == 403


def test_agent_expiry_and_concurrent_single_consumption(agent):
    issued = agent.call("launch").json["result"]
    store = HandoffStore(agent.api._get_connection, "?")

    def consume(_):
        try:
            store.transact("consume", ticket=issued["ticket"], native_identity=(3, 9))
            return True
        except DelegationDenied:
            return False

    with ThreadPoolExecutor(max_workers=8) as workers:
        assert sum(workers.map(consume, range(8))) == 1
    ticket = agent.call("launch").json["result"]["ticket"]
    with sqlite3.connect(agent.api.db_path) as db:
        db.execute("UPDATE external_agent_handoffs SET expires_at = ?", (int(time.time()) - 1,))
    assert agent.handoff(ticket).status_code == 403


def test_agent_no_replay_browser_identity_or_supplied_tools(agent):
    nonce = secrets.token_hex(24)
    assert agent.call("capabilities", nonce=nonce).status_code == 200
    assert agent.call("capabilities", nonce=nonce).status_code == 403
    for header in ("Authorization", "Cookie", "Origin"):
        assert agent.call("capabilities", headers={header: "untrusted"}).status_code == 403
    assert (
        agent.call(
            "open",
            {
                "required_isolation": "none",
                "accepted_revisions": [POLICY_REVISION],
                "session_id": "alice-session",
            },
        ).status_code
        == 403
    )
    assert (
        agent.call(
            "launch",
            {
                "required_isolation": "os_user",
                "accepted_revisions": [POLICY_REVISION],
                "request_id": secrets.token_hex(24),
                "cwd": "/root",
            },
        ).status_code
        == 403
    )


def test_agent_mapping_alias_and_signing_rotation_rejected(agent):
    ticket = agent.call("launch").json["result"]["ticket"]
    path = os.environ["OPENACE_EXTERNAL_IDENTITY_POLICY_FILE"]
    with open(path) as source:
        policy = json.load(source)
    policy["mappings"][1]["user_id"] = 9
    with open(path, "w") as target:
        json.dump(policy, target)
    assert agent.call("capabilities").status_code == 403
    assert agent.handoff(ticket).status_code == 403
    policy["mappings"][1]["user_id"] = 10
    with open(path, "w") as target:
        json.dump(policy, target)
    ticket = agent.call("launch").json["result"]["ticket"]
    with open(policy["issuers"][0]["secret_file"], "w") as target:
        target.write("r" * 32)
    assert agent.handoff(ticket).status_code == 403


def test_agent_old_runtime_or_disabled_adapter_refuses(agent, monkeypatch):
    example = copy.deepcopy(agent.snapshot)
    example.update(isolation_level="sandboxed", backend="opensandbox:verified-tier")
    example["entry_points"]["filesystem_api"] = "sandboxed_entry_not_wired"
    assert not permitted_snapshot(example, "sandboxed", [POLICY_REVISION])
    example["entry_points"]["filesystem_api"] = "enforced"
    assert permitted_snapshot(example, "sandboxed", [POLICY_REVISION])
    agent.snapshot.update(example)
    agent.account["system_account"] = None
    agent.config.required_isolation_level = "sandboxed"
    agent.policy["required_isolation"] = "sandboxed"
    agent.policy_path.write_text(json.dumps(agent.policy))
    assert (
        agent.call(
            "launch",
            {
                "required_isolation": "sandboxed",
                "accepted_revisions": [POLICY_REVISION],
                "request_id": secrets.token_hex(24),
            },
        ).status_code
        == 200
    )
    monkeypatch.setitem(sys.modules, "app.services.workspace_isolation_contract", None)
    assert agent.call("launch").status_code == 503
    monkeypatch.delenv("OPENACE_EXTERNAL_AGENT_ENABLED")
    assert agent.call("capabilities").status_code == 404


def test_agent_launch_retry_does_not_extend_receipt(agent):
    args = {
        "required_isolation": "os_user",
        "accepted_revisions": [POLICY_REVISION],
        "request_id": secrets.token_hex(24),
    }
    first = agent.call("launch", args).json["result"]
    second = agent.call("launch", args).json["result"]
    assert first["ticket"] == second["ticket"] and first["expires_at"] == second["expires_at"]
    assert agent.handoff(first["ticket"]).status_code == 303
    assert agent.call("launch", args).status_code == 403
