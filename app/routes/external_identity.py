"""Default-off external identity routes: capability probe and token exchange.

Both are authenticated by the same signed request; no cookie, bearer, browser
origin or query identity is ever accepted. The probe is a read-only dry run of
the exchange; the exchange issues a short-lived scoped proxy token for the
mapped user, which then talks to the existing LLM proxy like any other client.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from app.modules.workspace.external_identity import (
    DEFAULT_TTL_SECONDS,
    IDENTIFIER,
    DelegationDenied,
    ReplayStore,
    load_policy,
    verify_signed_request,
)

external_identity_bp = Blueprint("external_identity", __name__)


def _guarded_body():
    """Reject anything but a small, pure-JSON body with no ambient identity."""
    if request.query_string or any(
        request.headers.get(name) for name in ("Origin", "Cookie", "Authorization")
    ):
        return None, (jsonify({"error_code": "not_accessible"}), 403)
    if request.mimetype != "application/json":
        return None, (jsonify({"error_code": "invalid_request"}), 400)
    raw = request.stream.read(4097)
    if len(raw) > 4096:
        return None, (jsonify({"error_code": "invalid_request"}), 413)
    return raw, None


def _lookups():
    from app.repositories import database
    from app.repositories.tenant_repo import TenantRepository
    from app.repositories.user_repo import UserRepository

    def tenant_lookup(tenant_id):
        tenant = TenantRepository().get_by_id(tenant_id)
        return tenant.to_dict() if tenant else None

    return (
        ReplayStore(database.get_connection, database.get_param_placeholder()),
        UserRepository().get_user_by_id,
        tenant_lookup,
    )


@external_identity_bp.route("/integrations/external/capabilities", methods=["POST"])
def capabilities():
    path = os.environ.get("OPENACE_EXTERNAL_IDENTITY_POLICY_FILE")
    if not path:
        return jsonify({"error_code": "not_found"}), 404
    raw, denied = _guarded_body()
    if denied is not None:
        return denied
    try:
        policy = load_policy(path)
        replay, account_lookup, tenant_lookup = _lookups()
        result = verify_signed_request(
            policy, request.headers, raw, replay, account_lookup, tenant_lookup
        )
        # The exchange dry run: issuer policy readiness, reported without
        # consuming anything. Providers/models stay empty until the operator
        # configures them, and the exchange fails closed in that state.
        exchange = policy[3][result["issuer"]]
        result["capabilities"] = {"diagnosis_llm": False, "agent_workspace": False}
        result["reason"] = "governance_adapter_not_implemented"
        # Dry run of the exchange: policy grants AND the enable gate — the
        # probe must not advertise an exchange that /token would 404 on.
        result["exchange"] = {
            "available": (
                bool(exchange["providers"])
                and bool(exchange["models"])
                and os.environ.get("OPENACE_EXTERNAL_TOKEN_ENABLED") == "1"
            ),
            "providers": sorted(exchange["providers"]),
            "max_ttl_seconds": exchange["max_ttl_seconds"],
        }
        return jsonify(result)
    except DelegationDenied:
        return jsonify({"error_code": "not_accessible"}), 403
    except Exception:
        # Do not log signatures, policy contents, or database exception parameters.
        return jsonify({"error_code": "unavailable"}), 503


@external_identity_bp.route("/integrations/external/token", methods=["POST"])
# Failure atomicity: the session row is created before the token is minted.
# If minting or auditing then fails, the 503 leaves an unreferenced ACTIVE
# session behind — visible in the admin session list, harmless (token
# validation fails closed on a missing record), and the safe direction
# compared with a minted token whose session row is missing.
def token():
    from app.modules.workspace.external_identity import TOKEN_PATH

    path = os.environ.get("OPENACE_EXTERNAL_IDENTITY_POLICY_FILE")
    if not path or os.environ.get("OPENACE_EXTERNAL_TOKEN_ENABLED") != "1":
        return jsonify({"error_code": "not_found"}), 404
    raw, denied = _guarded_body()
    if denied is not None:
        return denied
    try:
        policy = load_policy(path)
        replay, account_lookup, tenant_lookup = _lookups()
        result = verify_signed_request(
            policy,
            request.headers,
            raw,
            replay,
            account_lookup,
            tenant_lookup,
            path=TOKEN_PATH,
            expected_fields={"organization", "user", "login", "provider"},
            optional_fields={"ttl_seconds"},
        )
        issuer = result["issuer"]
        exchange = policy[3][issuer]
        body = result["body"]
        provider = body["provider"]
        # Shape-check the provider before the set membership test: an
        # unhashable value would otherwise surface as a 503, not a denial.
        if not isinstance(provider, str) or not IDENTIFIER.fullmatch(provider):
            raise DelegationDenied()
        # Fail closed: an issuer without provider or model grants exchanges
        # nothing, whatever the mapping says.
        if provider not in exchange["providers"] or not exchange["models"]:
            raise DelegationDenied()
        # An omitted ttl defaults to the standard window, but never beyond
        # this issuer's ceiling: an issuer with max_ttl_seconds < 900 must
        # not require callers to pass an explicit ttl on every exchange.
        ttl = body.get("ttl_seconds", min(DEFAULT_TTL_SECONDS, exchange["max_ttl_seconds"]))
        if (
            not isinstance(ttl, int)
            or isinstance(ttl, bool)
            or not 1 <= ttl <= exchange["max_ttl_seconds"]
        ):
            raise DelegationDenied()

        identity = result["identity"]
        from app.modules.workspace.session_manager import get_session_manager

        # One external session per (issuer, user): create_session is
        # idempotent on session_id, so repeated exchanges reuse the same row
        # instead of leaving one permanently-active session behind each.
        session = get_session_manager().create_session(
            session_id=f"external:{issuer}:{identity['user_id']}",
            tool_name=f"external:{issuer}",
            user_id=identity["user_id"],
            tenant_id=identity["tenant_id"],
            session_type="external",
            title=f"External issuer {issuer}",
        )
        # The shared row is the per-identity kill switch: an admin stopping
        # it must surface as an explicit denial here, not as a minted token
        # that fails validation on every proxy call (validation only accepts
        # active/paused sessions). Lifting the switch resumes exchanges.
        session_status = getattr(session, "status", "active")
        if session_status not in ("active", "paused"):
            return jsonify({"error_code": "session_stopped"}), 403
        from app.modules.workspace.api_key_proxy import get_api_key_proxy_service

        api = get_api_key_proxy_service()
        issued_at = datetime.now(timezone.utc)
        issued = api.generate_proxy_token(
            user_id=identity["user_id"],
            session_id=session.session_id,
            tenant_id=identity["tenant_id"],
            provider=provider,
            # Exact seconds: a minutes floor would make the minted lifetime
            # disagree with the advertised expires_at (up to 59s short, or up
            # to 60s past a sub-minute issuer ceiling).
            expires_seconds=ttl,
            session_type="external",
            extra_payload={
                "issuer": issuer,
                "redact_policy": "deny",
                "allowed_models": exchange["models"],
            },
        )

        from app.modules.governance.audit_logger import AuditAction, AuditLogger

        audited = AuditLogger().log_action(
            action=AuditAction.EXTERNAL_TOKEN_ISSUED,
            user_id=identity["user_id"],
            resource_type="external_identity",
            severity="medium",
            details={
                "issuer": issuer,
                "session_id": session.session_id[:16],
                "tenant_id": identity["tenant_id"],
                "ttl_seconds": ttl,
                "provider": provider,
            },
        )
        if not audited:
            # Issuance must fail closed without its audit record — but the
            # blast radius is this one token: revoke it by jti and deny the
            # exchange. Stopping the shared session would turn one transient
            # audit-write failure into a permanent outage for the identity,
            # and revoking the whole session would kill other tasks'
            # in-flight tokens.
            try:
                from base64 import b64decode as _b64

                minted_payload = json.loads(_b64(issued.split(".")[0]))
                api.revoke_proxy_token_jti(minted_payload.get("jti", ""))
            except Exception:
                pass
            return jsonify({"error_code": "unavailable"}), 503

        # expires_at is UTC with offset, derived from the same instant the
        # token was minted at, so an external server in any timezone can
        # interpret it unambiguously. The proxy endpoint path is fixed and
        # documented (no host-derived base_url: the Host header is not part
        # of the signature and can be attacker-influenced behind proxies).
        response = jsonify(
            {
                "protocol": "openace-external-v1",
                "issuer": issuer,
                "request_nonce": result["request_nonce"],
                "token": issued,
                "session_id": session.session_id,
                "expires_at": (issued_at + timedelta(seconds=ttl)).isoformat(),
                "proxy_path": "/api/remote/llm-proxy",
            }
        )
        response.headers["Cache-Control"] = "no-store"
        return response
    except DelegationDenied:
        return jsonify({"error_code": "not_accessible"}), 403
    except Exception:
        # Do not log signatures, policy contents, or database exception parameters.
        return jsonify({"error_code": "unavailable"}), 503
