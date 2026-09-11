"""
Open ACE - Workspace Isolation Capability Endpoint (Issue #3374).

GET /api/workspace/isolation-capabilities returns the versioned capability
contract for local interactive workspace multi-user isolation. Available to
any authenticated user (session cookie or Bearer): the contract carries no
secrets, and issue #3374 asks for admin AND trusted-integrator access —
WebUI-token iframe callers are not served here (they use their own
per-resource tokens; this endpoint is not part of the iframe flow).
"""

import logging

from flask import Blueprint, jsonify

from app.auth.decorators import auth_required
from app.services.workspace_isolation_contract import build_workspace_isolation_snapshot

logger = logging.getLogger(__name__)

workspace_isolation_bp = Blueprint("workspace_isolation", __name__)


@workspace_isolation_bp.route("/workspace/isolation-capabilities", methods=["GET"])
@auth_required
def get_isolation_capabilities():
    """Get the workspace isolation capability contract.

    Returns:
        JSON contract: local_workspace_multi_user, backend, isolation_level,
        enforced/unsupported dimension lists, reasons, entry_points, and
        policy_revision. See docs/workspace-isolation-capabilities.md.

    Read-only by construction: never creates the WebUI manager (which would
    mint a token secret and spawn a cleanup greenlet) — when no manager
    exists yet the snapshot is derived from disk config only.
    """
    try:
        from app.services.webui_manager import peek_webui_manager

        manager = peek_webui_manager()
        snapshot = build_workspace_isolation_snapshot(manager)
    except Exception as e:
        logger.error("Failed to build isolation capability snapshot: %s", e)
        return jsonify({"error": "Internal server error"}), 500
    return jsonify(snapshot.public_dict())
