"""Opt-in external navigation to the native, authenticated Agent workbench.

No external caller receives a login token, WebUI token, model key, tool grant or
filesystem path. Handoff tickets are short-lived, single-use and require the
same native user to be logged in. Runtime isolation stays in the native service.
"""

import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timezone

from app.modules.workspace.external_identity import DelegationDenied, private_file, strict_json

PATH = "/api/integrations/external/agent"
REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
TICKET = re.compile(r"[0-9a-f]{48}\Z")
LOCAL_ENTRIES = ("webui", "filesystem_api", "session_history")
DIMENSIONS = ("identity", "filesystem", "environment", "process")
ENTRY_POINT_STATUSES = {
    "enforced",
    "partial",
    "remote_machine_scope",
    "separate_contract",
    "sandboxed_entry_not_wired",
    "disabled",
}


def agent_policy():
    if os.environ.get("OPENACE_EXTERNAL_AGENT_ENABLED") != "1":
        raise DelegationDenied()
    policy = strict_json(private_file(os.environ.get("OPENACE_EXTERNAL_AGENT_POLICY_FILE"), 262144))
    if (
        not isinstance(policy, dict)
        or set(policy) != {"required_isolation", "accepted_revisions", "acceptance_ref", "subjects"}
        or policy["required_isolation"] not in ("os_user", "sandboxed")
        or not isinstance(policy["acceptance_ref"], str)
        or not policy["acceptance_ref"].strip()
        or not isinstance(policy["accepted_revisions"], list)
        or not policy["accepted_revisions"]
        or any(
            not isinstance(v, str) or not 1 <= len(v) <= 80 for v in policy["accepted_revisions"]
        )
        or not isinstance(policy["subjects"], list)
    ):
        raise DelegationDenied()
    for subject in policy["subjects"]:
        if not isinstance(subject, dict) or set(subject) != {
            "issuer",
            "organization",
            "user",
            "login",
        }:
            raise DelegationDenied()
    return policy


def external_subject(owner):
    return {key: owner[key] for key in ("issuer", "organization", "user", "login")}


def permitted_snapshot(snapshot, level, revisions):
    backend = snapshot.get("backend", "")
    backend_ok = level == "os_user" and backend == "qwen-code-webui-per-user"
    if level == "sandboxed" and isinstance(backend, str) and backend.startswith("opensandbox:"):
        backend_ok = bool(REFERENCE.fullmatch(backend.removeprefix("opensandbox:")))
    entry_points = snapshot.get("entry_points")
    details = snapshot.get("entry_point_details")
    entry_points_ok = isinstance(entry_points, dict) and all(
        status in ENTRY_POINT_STATUSES for status in entry_points.values()
    )
    details_ok = entry_points_ok and isinstance(details, dict)
    if details_ok:
        for entry in LOCAL_ENTRIES:
            detail = details.get(entry)
            if (
                not isinstance(detail, dict)
                or entry_points.get(entry) != "enforced"
                or detail.get("status") != "enforced"
                or detail.get("covered_by_isolation_level") is not True
                or detail.get("limitations") != []
            ):
                details_ok = False
                break
    return (
        snapshot.get("local_workspace_multi_user") == "supported"
        and backend_ok
        and snapshot.get("isolation_level") == level
        and snapshot.get("policy_revision") in revisions
        and snapshot.get("reasons") == []
        and all(
            v in snapshot.get("enforced", []) and v not in snapshot.get("unsupported", [])
            for v in DIMENSIONS
        )
        # This integration only grants local Agent workbench navigation. Remote
        # terminal and VS Code remain governed by native machine ACLs.
        and entry_points_ok
        and details_ok
    )


def runtime_capabilities(owner, account, policy):
    # Missing module/version is a refusal, never a shared-account fallback.
    from app.services.webui_manager import get_webui_manager
    from app.services.workspace_isolation_contract import (
        build_workspace_isolation_snapshot,
        evaluate_isolation_requirement,
    )

    manager = get_webui_manager()
    snapshot = build_workspace_isolation_snapshot(manager)
    public = snapshot.public_dict()
    account_name = account.get("system_account")
    allowed = (
        external_subject(owner) in policy["subjects"]
        and permitted_snapshot(public, policy["required_isolation"], policy["accepted_revisions"])
        # The native UI launches without our query args. Require a hard
        # server floor; a derived default could silently weaken later.
        and getattr(manager.config, "required_isolation_level", "") == policy["required_isolation"]
        and (policy["required_isolation"] == "sandboxed" or bool(account_name))
    )
    if allowed:
        allowed = (
            evaluate_isolation_requirement(
                policy["required_isolation"],
                snapshot=snapshot,
                system_account=account_name,
                manager=manager,
            )
            is None
        )
    workspace = hashlib.sha256(
        json.dumps(
            [owner["tenant_id"], owner["user_id"], account_name, public.get("backend")],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return {
        "contract": "openace-agent-v1",
        "available": bool(allowed),
        "reason": "" if allowed else "agent_isolation_not_accepted",
        "identity": {"tenant_id": owner["tenant_id"], "user_id": owner["user_id"]},
        "workspace_ref": workspace,
        "governance": "openace_native",
        "isolation": public,
    }


def owned_session(manager, owner, session_id):
    if not isinstance(session_id, str) or not REFERENCE.fullmatch(session_id):
        raise DelegationDenied()
    value = manager.get_session(session_id, include_messages=False, tenant_id=owner["tenant_id"])
    if value is None:
        raise DelegationDenied()
    row = value.to_dict()
    # Admin status never broadens the external integration's ownership scope.
    if (
        row.get("user_id") != owner["user_id"]
        or row.get("tenant_id") != owner["tenant_id"]
        or row.get("workspace_type") != "local"
        or row.get("session_type") != "chat"
        or row.get("tool_name") not in ("qwen", "qwen-code")
        or not row.get("project_path")
        or row.get("status") not in ("active", "completed")
    ):
        raise DelegationDenied()
    return row


def public_session(row, workspace):
    created = row["created_at"]
    if isinstance(created, str):
        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
    if isinstance(created, datetime):
        created = created.replace(tzinfo=timezone.utc) if created.tzinfo is None else created
        created = int(created.timestamp())
    return {
        "id": row["session_id"],
        "title": str(row.get("title") or "Agent session")[:120],
        "state": row["status"],
        "workspace_ref": workspace,
        "created_at": int(created),
    }


class HandoffStore:
    """SQL atomic one-use navigation receipts. Not authentication credentials."""

    def __init__(self, connection, placeholder):
        self.connection = connection
        self.placeholder = placeholder

    def transact(self, action, *, ticket, value=None, native_identity=None):
        if not isinstance(ticket, str) or not TICKET.fullmatch(ticket):
            raise DelegationDenied()
        conn = self.connection()

        def sql(query):
            return query.replace("?", self.placeholder)

        try:
            cursor = conn.cursor()
            now = int(time.time())
            cursor.execute(
                sql("DELETE FROM external_agent_handoffs WHERE expires_at < ?"), (now - 86400,)
            )
            hashed = hashlib.sha256(ticket.encode()).hexdigest()
            if action == "issue":
                cursor.execute(
                    sql(
                        """INSERT INTO external_agent_handoffs (ticket_hash, payload, expires_at, consumed)
                    VALUES (?, ?, ?, 0) ON CONFLICT (ticket_hash) DO NOTHING"""
                    ),
                    (hashed, json.dumps(value, sort_keys=True), now + 60),
                )
            cursor.execute(
                sql("SELECT * FROM external_agent_handoffs WHERE ticket_hash = ?"), (hashed,)
            )
            row = cursor.fetchone()
            if not row or row["expires_at"] <= now or row["consumed"]:
                raise DelegationDenied()
            payload = json.loads(row["payload"])
            if action == "issue" and payload != value:
                raise DelegationDenied()
            if action == "consume":
                if native_identity != (payload["owner"]["tenant_id"], payload["owner"]["user_id"]):
                    raise DelegationDenied()
                cursor.execute(
                    sql(
                        "UPDATE external_agent_handoffs SET consumed = 1 WHERE ticket_hash = ? AND consumed = 0 AND expires_at > ?"
                    ),
                    (hashed, now),
                )
                if cursor.rowcount != 1:
                    raise DelegationDenied()
            conn.commit()
            return payload, row["expires_at"]
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


class ExternalAgentService:
    """Agent navigation admission and one-use ticket issuance."""

    def __init__(self, owner, account, secret, store, live):
        self.owner, self.account, self.secret, self.store, self.live = (
            owner,
            account,
            secret,
            store,
            live,
        )

    def capabilities(self):
        self.account = self.live()
        policy = agent_policy()
        if external_subject(self.owner) not in policy["subjects"]:
            raise DelegationDenied()
        return runtime_capabilities(self.owner, self.account, policy)

    def handle(self, operation, parameters):
        if not isinstance(parameters, dict):
            raise DelegationDenied()
        caps = self.capabilities()
        reply = {"capabilities": caps, "sessions": [], "ticket": "", "expires_at": 0}
        if operation == "capabilities" and parameters == {}:
            return reply
        expected = {"required_isolation", "accepted_revisions"}
        if operation == "launch":
            expected.add("request_id")
        elif operation == "open":
            expected.add("session_id")
        elif operation != "sessions":
            raise DelegationDenied()
        if (
            set(parameters) != expected
            or not caps["available"]
            or not isinstance(parameters["accepted_revisions"], list)
            or not permitted_snapshot(
                caps["isolation"],
                parameters["required_isolation"],
                parameters["accepted_revisions"],
            )
        ):
            raise DelegationDenied()
        from app.modules.workspace.session_manager import get_session_manager

        manager = get_session_manager()
        if operation == "sessions":
            listed = manager.list_sessions(
                user_id=self.owner["user_id"],
                tenant_id=self.owner["tenant_id"],
                tool_name="qwen",
                session_type="chat",
                page=1,
                limit=50,
            )
            for candidate in listed["sessions"]:
                row = candidate.to_dict() if hasattr(candidate, "to_dict") else candidate
                try:
                    owned = owned_session(manager, self.owner, row["session_id"])
                except DelegationDenied:
                    continue
                reply["sessions"].append(public_session(owned, caps["workspace_ref"]))
            return reply
        session_id = parameters.get("session_id", "")
        if operation == "open":
            owned = owned_session(manager, self.owner, session_id)
            reply["sessions"] = [public_session(owned, caps["workspace_ref"])]
            import secrets

            intent = secrets.token_hex(24)
        else:
            intent = parameters["request_id"]
            if not isinstance(intent, str) or not TICKET.fullmatch(intent):
                raise DelegationDenied()
        payload = {
            "owner": self.owner,
            "signing_binding": hashlib.sha256(self.secret).hexdigest(),
            "session_id": session_id,
            "workspace_ref": caps["workspace_ref"],
            "policy_revision": caps["isolation"]["policy_revision"],
            "required_isolation": parameters["required_isolation"],
        }
        ticket = hmac.new(
            self.secret,
            b"openace-agent-handoff-v1:" + json.dumps([payload, intent], sort_keys=True).encode(),
            hashlib.sha256,
        ).hexdigest()[:48]
        _, expiry = self.store.transact("issue", ticket=ticket, value=payload)
        self.live()
        reply.update(ticket=ticket, expires_at=expiry)
        return reply
