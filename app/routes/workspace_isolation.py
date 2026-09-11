"""
Open ACE - Workspace Isolation Capability Endpoint (Issue #3374).

GET /api/workspace/isolation-capabilities returns the versioned capability
contract for local interactive workspace multi-user isolation. Admins and
trusted integrators query this contract instead of relying on README claims
or client-side capability booleans.
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
    """
    try:
        snapshot = build_workspace_isolation_snapshot()
    except Exception as e:
        logger.error("Failed to build isolation capability snapshot: %s", e)
        return jsonify({"error": "Internal server error"}), 500
    return jsonify(snapshot.public_dict())
