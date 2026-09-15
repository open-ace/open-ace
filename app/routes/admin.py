"""
Open ACE - Admin Routes

API routes for admin operations.
"""

import logging
import secrets
import string
from typing import Any, cast

import bcrypt
from flask import Blueprint, g, jsonify, request

from app.auth.decorators import (
    admin_required,
    enforce_requested_tenant_scope,
    resolve_admin_tenant_scope,
    same_tenant_user_required,
)
from app.auth.permissions import is_platform_level_role
from app.constants import EXPLICIT_NULL
from app.modules.governance.audit_logger import AuditAction, AuditLogger
from app.repositories.usage_repo import UsageRepository
from app.repositories.user_repo import UserRepository
from app.schemas.quota import validate_quota_update, validate_tenant_allocation
from app.services.auth_service import get_security_settings_cached
from app.utils.validators import validate_email, validate_password, validate_username
from app.utils.workspace import ensure_system_user

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__)
user_repo = UserRepository()
usage_repo = UsageRepository()
audit_logger = AuditLogger()


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return cast("str", bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode())


def get_client_info():
    """Get client IP and user agent."""
    return {
        "ip_address": request.remote_addr,
        "user_agent": request.headers.get("User-Agent", ""),
    }


def parse_is_active_field(value: Any) -> bool | None:
    """Parse a request's is_active field into a real boolean (or None).

    Issue #3379 review round 2 (R-3): ``bool(value)`` is not a parser —
    ``bool("false")`` is True, so string/number payloads silently flipped
    into the OPPOSITE state (a "false" deactivated the row on SQLite's
    INTEGER affinity but bypassed the ``is False`` side-effect test, and on
    PostgreSQL's native BOOLEAN the raw value errors or coerces by dialect —
    the observable behavior reversed by backend). Only booleans, ints 0/1,
    and the strings "true"/"false"/"1"/"0" (case-insensitive, whitespace
    stripped) parse; anything else raises ValueError so the caller can 400
    BEFORE any state changes land (the api_keys.py is_active precedent).

    Returns None when the field is absent (caller leaves the column alone).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("true", "1"):
            return True
        if normalized in ("false", "0"):
            return False
    raise ValueError("is_active must be a boolean")


def _spawn_background(fn) -> None:  # noqa: ANN001 - test seam
    """Run *fn* on a fresh greenlet.

    Issue #3379 review round 2 (R-1): the WebUI teardown (proxy-token
    revocation + sandbox destroy with its final export) can take ~90s for
    sandboxed pods; the admin request must return immediately. Tests patch
    this seam to a synchronous executor so the spawned work is observable
    without a gevent hub (#2457 lesson).
    """
    import gevent

    gevent.spawn(fn)


def _schedule_workspace_stop(user_id: int) -> dict[str, Any]:
    """Schedule the user's WebUI teardown off the request path.

    Issue #3379 review round 2 (R-1/R-6/R-11): stop_user_webui itself now
    pops the registry slot under the manager lock and tears down outside it
    (freeing every other user's token validations), but the destroy can
    still take ~90s — the HTTP response must not wait for it. The audit
    therefore records what was SCHEDULED, not what completed.

    Uses peek_webui_manager() (never get_webui_manager(), #3374 review #13):
    constructing a manager for a no-op would read config, mint a token
    secret, and start the cleanup greenlet.

    Single-user mode: stop_user_webui only touches the PER-USER registry,
    which is empty in that mode — the SHARED instance serves every user and
    is deliberately left running (declared residual). The audit says so
    explicitly instead of implying a stop that cannot happen.

    Returns:
        Audit detail fields describing the outcome.
    """
    try:
        from app.services.webui_manager import peek_webui_manager

        manager = peek_webui_manager()
    except Exception as e:  # noqa: BLE001 - deactivation must proceed
        logger.warning(f"Failed to resolve WebUI manager for user {user_id}: {e}")
        return {"workspace_stop_scheduled": False}

    if manager is None:
        return {"workspace_stop_scheduled": False}

    if not getattr(manager.config, "multi_user_mode", True):
        return {"workspace_stopped": "shared_instance_not_stopped"}

    def _stop() -> None:
        try:
            # Identity re-check (_reap_dead_sandboxed_async precedent): if the
            # singleton was replaced between the request and this greenlet,
            # the old manager holds no live instances — nothing to stop.
            from app.services.webui_manager import peek_webui_manager as _peek

            if _peek() is manager:
                # Round 3 (review R-11): the bool says whether a per-user
                # instance was actually found and stopped — logged here
                # because the HTTP response only records the scheduling.
                stopped = manager.stop_user_webui(user_id)
                logger.info(
                    "Scheduled WebUI stop for user %s completed: instance_stopped=%s",
                    user_id,
                    stopped,
                )
        except Exception as e:  # noqa: BLE001 - deactivation must proceed
            logger.warning(f"Failed to stop WebUI instance for user {user_id}: {e}")

    try:
        _spawn_background(_stop)
    except Exception as e:  # noqa: BLE001 - deactivation must proceed
        logger.warning(f"Failed to schedule WebUI stop for user {user_id}: {e}")
        return {"workspace_stop_scheduled": False}
    return {"workspace_stop_scheduled": True}


def reject_privilege_escalation(role: object) -> tuple[Any, int] | None:
    """Deny a tenant-scoped admin assigning a platform-level role.

    Granting one is equivalent to escaping the tenant: the grantee (possibly
    the grantor's own account) becomes a platform admin and can then reach
    every other tenant.

    Returns ``None`` when the assignment is allowed (no role given, an
    ordinary role, or the actor is a platform admin), otherwise the 403 the
    caller must return.
    """
    if role is None or not is_platform_level_role(cast("str | None", role)):
        return None

    actor_tenant_id, denial = resolve_admin_tenant_scope()
    if denial is not None:
        return denial
    if actor_tenant_id is None:
        # Platform admin: allowed to create peers.
        return None

    logger.warning(
        "Privilege escalation denied: actor=%s actor_tenant=%s requested_role=%s path=%s",
        g.user_id,
        actor_tenant_id,
        role,
        request.path,
    )
    return jsonify({"error": "Cannot assign platform-level role"}), 403


def validate_persistable_role(role: object) -> tuple[Any, int] | None:
    """Validate that the role is in the list of persistable roles.

    Issue #3179: The legacy 'admin' role is no longer accepted in
    create/update/restore operations. Only roles in PERSISTABLE_ROLES
    (platform_admin, tenant_admin, manager, user, readonly) are allowed.

    Returns ``None`` when the role is valid or not provided,
    otherwise returns a 400 error response.
    """
    from app.models.user import PERSISTABLE_ROLES

    if role is None:
        return None

    role_str = cast("str", role)
    if role_str in PERSISTABLE_ROLES:
        return None

    logger.warning(
        "Invalid role rejected: role=%s valid_roles=%s path=%s",
        role_str,
        list(PERSISTABLE_ROLES),
        request.path,
    )
    return (
        jsonify(
            {
                "error": "Invalid role",
                "message": f"Role must be one of: {', '.join(sorted(PERSISTABLE_ROLES))}",
            }
        ),
        400,
    )


@admin_bp.route("/admin/users", methods=["GET"])
@admin_required
def api_get_users():
    """Get all users, optionally filtered by tenant.

    A tenant-scoped admin only ever sees their own tenant: an omitted
    ``tenant_id`` is narrowed to theirs rather than left as "all tenants",
    and naming somebody else's tenant is rejected.
    """
    tenant_id, denial = enforce_requested_tenant_scope(request.args.get("tenant_id"))
    if denial is not None:
        return denial

    users = user_repo.get_all_users(tenant_id=tenant_id)

    # Remove password hashes
    for user in users:
        user.pop("password_hash", None)

    # Batch load tenant info to avoid N+1 queries
    tenant_ids = {user.get("tenant_id") for user in users if user.get("tenant_id")}
    if tenant_ids:
        from app.services.tenant_service import TenantService

        tenant_service = TenantService()
        tenants = tenant_service.list_tenants()
        tenant_map = {t.id: t.name for t in tenants if t.id in tenant_ids}
        for user in users:
            user["tenant_name"] = tenant_map.get(user.get("tenant_id"))

    return jsonify(users)


@admin_bp.route("/admin/users", methods=["POST"])
@admin_required
def api_create_user():
    """Create a new user."""
    data = request.get_json() or {}
    username: str = data.get("username", "")
    email: str = data.get("email", "")
    password: str = data.get("password", "")
    role = data.get("role", "user")
    # Issue #2179: Fail-Closed - 必须显式指定 tenant_id
    tenant_id = data.get("tenant_id")
    if tenant_id is None:
        return jsonify({"error": "tenant_id is required"}), 400

    # A tenant-scoped admin may only populate their own tenant, and may not
    # mint a platform-level account there.
    # Also 400s an unusable tenant_id, so nothing unvalidated reaches the
    # quota check or the INSERT.
    tenant_id, denial = enforce_requested_tenant_scope(tenant_id)
    if denial is not None:
        return denial
    escalation = reject_privilege_escalation(role)
    if escalation is not None:
        return escalation

    # Issue #3179: Validate that role is a persistable role
    invalid_role = validate_persistable_role(role)
    if invalid_role is not None:
        return invalid_role

    # Validate inputs
    if not validate_username(username):
        return jsonify({"error": "Invalid username"}), 400

    if not validate_email(email):
        return jsonify({"error": "Invalid email"}), 400

    # Validate password with security policy
    settings = get_security_settings_cached()
    is_valid, error_msg = validate_password(password, policy_settings=settings)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    # Check if user exists (Issue #2755: Distinguish active vs soft-deleted users)
    # Check active users first
    active_user_by_username = user_repo.get_user_by_username(username, include_deleted=False)
    if active_user_by_username:
        return jsonify({"error": "Username already exists"}), 400

    active_user_by_email = user_repo.get_user_by_email(email, include_deleted=False)
    if active_user_by_email:
        return jsonify({"error": "Email already exists"}), 400

    # Check soft-deleted users for conflict detection
    deleted_user_by_username = user_repo.get_soft_deleted_user_by_username(username)
    deleted_user_by_email = user_repo.get_soft_deleted_user_by_email(email)

    # Build conflict response for soft-deleted users
    conflicts = []
    deleted_user = None
    if deleted_user_by_username and deleted_user_by_email:
        # Both match - check if it's the same user
        if deleted_user_by_username["id"] == deleted_user_by_email["id"]:
            deleted_user = deleted_user_by_username
            conflicts = ["username", "email"]
        else:
            # Different soft-deleted users match username and email - return username conflict
            deleted_user = deleted_user_by_username
            conflicts = ["username"]
    elif deleted_user_by_username:
        deleted_user = deleted_user_by_username
        conflicts = ["username"]
    elif deleted_user_by_email:
        deleted_user = deleted_user_by_email
        conflicts = ["email"]

    if deleted_user:
        return (
            jsonify(
                {
                    "error": "USER_SOFT_DELETED",
                    "message": "The username or email belongs to a deleted user",
                    "soft_deleted_user": {
                        "user_id": deleted_user["id"],
                        "username": deleted_user["username"],
                        "email": deleted_user["email"],
                        "deleted_at": (
                            deleted_user["deleted_at"].isoformat()
                            if deleted_user.get("deleted_at")
                            else None
                        ),
                        "tenant_id": deleted_user.get("tenant_id"),
                        "conflicts": conflicts,
                    },
                }
            ),
            409,
        )

    # Check tenant quota before creating user
    from app.services.tenant_service import TenantService

    tenant_service = TenantService()
    if not tenant_service.can_add_user(tenant_id):
        tenant = tenant_service.get_tenant(tenant_id)
        max_users = tenant.quota.max_users if tenant else 0
        return jsonify({"error": f"Tenant user quota exceeded (max: {max_users})"}), 400

    # Create user
    password_hash = hash_password(password)
    system_account = data.get("system_account")
    if system_account and not validate_username(system_account):
        return jsonify({"error": "Invalid system_account name"}), 400
    user_id = user_repo.create_user(
        username,
        email,
        password_hash,
        str(role),
        system_account=system_account,
        tenant_id=tenant_id,
    )

    if user_id:
        # Increment tenant user count (compensate if fails)
        if not tenant_service.increment_user_count(tenant_id):
            # Rollback: delete created user if tenant count update fails
            user_repo.delete_user(user_id)
            logger.error(f"Rollback: deleted user {user_id} due to tenant count update failure")
            return jsonify({"error": "Failed to update tenant user count"}), 500
        # Auto-create system user for workspace if system_account is provided
        if system_account:
            uid = data.get("system_uid")  # Optional: specific UID
            # Issue #3396 review: pass the target tenant so the account
            # enrolls into openace-shared-<tenant_id>; without it the
            # default None maps to the openace-shared-0 pseudo-tenant and
            # the membership would have to be repaired by hand.
            if ensure_system_user(system_account, uid=uid, tenant_id=tenant_id):
                logger.info(f"System user {system_account} ready for workspace")
            else:
                logger.warning(
                    f"Failed to create system user {system_account}, workspace may not work"
                )
        # Audit log for user creation
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.USER_CREATE,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="user",
            resource_id=str(user_id),
            resource_name=username,
            details={"email": email, "role": role, "tenant_id": tenant_id},
            **client_info,
        )
        return jsonify({"success": True, "user_id": user_id}), 201

    return jsonify({"error": "Failed to create user"}), 500


@admin_bp.route("/admin/users/<int:user_id>", methods=["PUT"])
@admin_required
@same_tenant_user_required
def api_update_user(user_id):
    """Update a user."""
    data = request.get_json() or {}

    # Issue #3379 review round 2 (R-3): parse is_active with an explicit
    # parser BEFORE any state changes — bool("false") is True, so the old
    # truthiness coercion accepted garbage, half-deactivated (SQLite), or
    # errored late (PostgreSQL). Garbage must 400 here, ahead of the tenant
    # counter mutations and update_user below.
    try:
        requested_is_active = parse_is_active_field(data.get("is_active"))
    except ValueError:
        return jsonify({"error": "is_active must be a boolean"}), 400

    # Issue #3379 review round 2 (R-7): an admin must not deactivate
    # themselves — the revocation below would kill their own session on the
    # spot, and a lone admin could then only recover by direct DB surgery.
    # DELETE already refuses self-deletion; PUT's deactivation now agrees.
    if requested_is_active is False and g.user_id == user_id:
        return jsonify({"error": "Cannot deactivate yourself"}), 400

    # Get current user state for audit diff (before update)
    current_user = user_repo.get_user_by_id(user_id)

    # A tenant-scoped admin must not grant a platform-level role, and must not
    # move a user across the tenant boundary in either direction: pulling a
    # foreign user in is takeover, pushing one out is exfiltration.
    escalation = reject_privilege_escalation(data.get("role"))
    if escalation is not None:
        return escalation

    # Issue #3179: Validate that role is a persistable role
    invalid_role = validate_persistable_role(data.get("role"))
    if invalid_role is not None:
        return invalid_role

    # Use the value the scope guard returns, never the raw body value. A value
    # the guard cannot normalize (0, -1, "", "abc") comes back as None, which
    # enforce_requested_tenant_scope treats as "no tenant requested" and lets
    # through -- so re-reading data["tenant_id"] afterwards would hand that
    # unvalidated value to the quota check and the UPDATE.
    new_tenant_id = None
    if data.get("tenant_id") is not None:
        new_tenant_id, denial = enforce_requested_tenant_scope(data.get("tenant_id"))
        if denial is not None:
            return denial

    # Auto-create system user if system_account is being set
    system_account = data.get("system_account")
    if system_account and not validate_username(system_account):
        return jsonify({"error": "Invalid system_account name"}), 400
    # Issue #3396 review: a minimal tenant move ({"tenant_id": N} with NO
    # system_account field) must still run the enroll/drop bookkeeping below
    # — otherwise the move silently skips BOTH the new-tenant enrollment and
    # the old-tenant group drop, and the account keeps OS read/write on the
    # OLD tenant's shared projects forever.
    # Round-2 review N2/N3: the bookkeeping uses its OWN locals and never
    # leaks back into system_account, which keeps its raw body semantics for
    # the DB write below (None = unchanged, "" = clear the mapping):
    #   * enroll_account: the body's account, else the DB row's. NO username
    #     fallback — provisioning system_account=username for a mapping-less
    #     row would recreate the auto-backfill multi-user mode removed.
    #   * the old-group drop below always targets the PRE-WRITE row account:
    #     on a remap+move that OLD account is the one holding the old-tenant
    #     membership (the body's new account was never enrolled there), and
    #     an explicit ""+move must still drop it.
    # Round-3 review R1: the enroll itself runs only AFTER the tenant-move
    # quota check passes and the DB write succeeds (bottom of the handler) —
    # enrolling before the quota check left a quota-REJECTED move with a
    # permanent grant on the target tenant's shared group.
    enroll_account = system_account
    if enroll_account is None and current_user:
        enroll_account = current_user.get("system_account")

    # Handle tenant_id change
    if new_tenant_id is not None:
        from app.services.tenant_service import TenantService

        tenant_service = TenantService()
        # Check if user exists and get current tenant
        if current_user:
            # Issue #2179: Fail-Closed - 不再使用默认值
            current_tenant_id = current_user.get("tenant_id")
            if current_tenant_id is None:
                logger.warning(f"User {user_id} has no tenant_id")
                current_tenant_id = 0  # 用于比较，不会匹配任何租户
            # If tenant is changing, check quota for new tenant
            if new_tenant_id != current_tenant_id:
                if not tenant_service.can_add_user(new_tenant_id):
                    tenant = tenant_service.get_tenant(new_tenant_id)
                    max_users = tenant.quota.max_users if tenant else 0
                    return (
                        jsonify({"error": f"Target tenant quota exceeded (max: {max_users})"}),
                        400,
                    )
                # Decrement old tenant count and increment new tenant count
                tenant_service.decrement_user_count(current_tenant_id)
                tenant_service.increment_user_count(new_tenant_id)
                # Issue #3396: a tenant move must also drop the OLD tenant's
                # shared-content group — otherwise the account keeps OS-level
                # read/write on the old tenant's shared projects that the API
                # layer no longer shows it. (A NULL old tenant maps to the
                # sentinel 0 above, which is also the pseudo-tenant group id.)
                # Round-2 review N3: drop the PRE-WRITE row account — it is
                # the one holding the old-tenant membership, not the body's
                # remap target (never enrolled there) and not "" (clear).
                old_account = (current_user or {}).get("system_account")
                if old_account:
                    from app.utils.workspace import remove_user_from_shared_group

                    if not remove_user_from_shared_group(old_account, tenant_id=current_tenant_id):
                        logger.warning(
                            f"Failed to remove {old_account} from old tenant "
                            f"{current_tenant_id} shared group"
                        )

    # requested_is_active is a real boolean (or None) — parsed at the top of
    # the handler (R-3); it flows to the DB write and to the side-effect
    # decisions below unchanged.

    success = user_repo.update_user(
        user_id=user_id,
        username=data.get("username"),
        email=data.get("email"),
        role=data.get("role"),
        is_active=requested_is_active,
        system_account=system_account,
        tenant_id=new_tenant_id,
    )

    if success:
        # Round-3 review R1: enroll into the tenant-scoped shared group only
        # now — after the quota check passed and the DB write succeeded. The
        # previous placement (before the quota check) let a quota-REJECTED
        # move leave a permanent grant on the target tenant's content group;
        # running after a failed update_user has the same rationale. On a
        # tenant move this enrolls for the TARGET tenant, matching the row
        # just written; a failure here is logged, not fatal — the boot sync
        # re-enrolls by the DB row, so the state self-heals on restart.
        if enroll_account:
            uid = data.get("system_uid")
            effective_tenant_id = (
                new_tenant_id
                if new_tenant_id is not None
                else (current_user or {}).get("tenant_id")
            )
            if not ensure_system_user(enroll_account, uid=uid, tenant_id=effective_tenant_id):
                logger.warning(
                    f"Failed to create system user {enroll_account}, workspace may not work"
                )
        # Audit log for user update
        details: dict[str, Any] = {"action": "update"}
        if current_user:
            # Track role change
            old_role = current_user.get("role")
            new_role = data.get("role")
            if new_role and old_role != new_role:
                details["role_change"] = {"from": old_role, "to": new_role}
            # Track status change
            old_active = current_user.get("is_active")
            new_active = requested_is_active
            if new_active is not None and old_active != new_active:
                details["status_change"] = {"from": old_active, "to": new_active}
            # Issue #3379 (PR-A): deactivating a user must stop their
            # running workspace — same invariant as DELETE below — and
            # revoke the sessions: a disabled account must not keep
            # logged-in sessions (password login already refuses
            # is_active=false, this closes the already-issued ones).
            #
            # Review round 2 (R-10): the remediation runs whenever
            # is_active=false is REQUESTED, not only on a true transition —
            # both operations are idempotent, and an account that was already
            # inactive (deactivated before this remediation existed, or by
            # org sync) still owes its sessions and workspace the cleanup.
            # The status_change audit above still records only real changes.
            if new_active is False:
                # R-5: stamp tokens_valid_after — URL tokens minted before
                # this moment stay dead across a later reactivation.
                details["tokens_invalidated"] = user_repo.set_tokens_valid_after(user_id)
                # R-8: record the actual per-table counts (the repository
                # swallows failures into zero counts — a constant True here
                # used to claim a revoke that never happened).
                sessions_revoked: dict[str, int] | None = None
                try:
                    sessions_revoked = user_repo.delete_all_sessions_for_user(user_id)
                except Exception as e:  # noqa: BLE001 - audit the miss
                    logger.warning(f"Failed to revoke sessions for deactivated user {user_id}: {e}")
                details["sessions_revoked"] = sessions_revoked
                # R-1/R-6/R-11: schedule the teardown off the request path;
                # the audit records the scheduling outcome, truthfully.
                details.update(_schedule_workspace_stop(user_id))
                # Issue #3396: deactivation must also drop the tenant
                # shared-content group at the OS layer — the user's shells/
                # agents would otherwise keep read/write on the tenant's
                # shared projects (the API layer already refuses them).
                # Uses the user's EXISTING mapping (the deactivation body
                # need not carry system_account). Fail-soft: group removal
                # failure is logged, not fatal.
                if current_user:
                    deactivated_account = current_user.get("system_account") or current_user.get(
                        "username"
                    )
                    if deactivated_account:
                        from app.utils.workspace import remove_user_from_shared_group

                        deactivated_tenant_id = current_user.get("tenant_id")
                        if not remove_user_from_shared_group(
                            deactivated_account, tenant_id=deactivated_tenant_id
                        ):
                            logger.warning(
                                f"Failed to remove deactivated {deactivated_account} from "
                                f"tenant {deactivated_tenant_id} shared group"
                            )
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.USER_UPDATE,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="user",
            resource_id=str(user_id),
            resource_name=data.get("username")
            or (current_user.get("username") if current_user else None),
            details=details,
            **client_info,
        )
        return jsonify({"success": True})

    return jsonify({"error": "Failed to update user"}), 500


@admin_bp.route("/admin/users/<int:user_id>", methods=["DELETE"])
@admin_required
@same_tenant_user_required
def api_delete_user(user_id):
    """Delete a user.

    Issue #2755: Now includes session revocation and tenant counter decrement.
    """
    # Prevent deleting yourself
    if g.user_id == user_id:
        return jsonify({"error": "Cannot delete yourself"}), 400

    # Get user info for audit and cleanup before deletion
    user = user_repo.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    username = user.get("username")
    tenant_id = user.get("tenant_id")

    # Perform soft delete FIRST (Issue #3379 PR-A review): the workspace
    # teardown below can take tens of seconds (sandboxed pods: final snapshot
    # export + delete), and until deleted_at lands in the DB the token-side
    # active-user check still sees the user alive — leaving already-issued
    # URL tokens valid for that whole window. Soft-deleting first closes the
    # token door immediately; the stop afterwards is fail-soft and needs no
    # rollback.
    #
    # Review round 2 (R-4): the session revocation now also runs AFTER the
    # soft delete. get_session_by_token refuses sessions whose user is
    # inactive or soft-deleted, so deleted_at closes the password-login door
    # on its own; revoking BEFORE the soft delete instead left a window in
    # which the still-active user could log back in and mint a fresh
    # session that then survived the delete.
    success = user_repo.delete_user(user_id)
    if not success:
        return jsonify({"error": "Failed to delete user"}), 500

    # Issue #3379 review round 2 (R-5): stamp tokens_valid_after with the
    # soft-delete time — v1 URL tokens (no TTL, formerly valid forever) and
    # any v2 token minted before this moment stay dead even across a later
    # restore (restore deliberately does not clear the stamp).
    tokens_invalidated = user_repo.set_tokens_valid_after(user_id)

    # Issue #2755: Revoke all sessions (after the soft delete — see the
    # R-4 note above; the user-status check in get_session_by_token has
    # already closed the door, this is row cleanup).
    session_counts = user_repo.delete_all_sessions_for_user(user_id)
    logger.info(
        f"Revoked sessions for user {user_id}: "
        f"sessions={session_counts['sessions']}, sso_sessions={session_counts['sso_sessions']}, "
        f"web_user_auth_sessions={session_counts['web_user_auth_sessions']}"
    )

    # Issue #3379 (PR-A): a deleted user must not keep a running workspace.
    # Stopping the instance also revokes the webui:<uid> LLM proxy token and
    # (for sandboxed instances) destroys the pod. Note (review round 2, R-6):
    # the token-side active-user check only guards OPEN ACE's own URL-token
    # endpoints — the WebUI validates its tokens itself (per-instance
    # --token-secret, end-to-end with the browser), so a client pointed
    # straight at the webui port is only closed out by the INSTANCE actually
    # stopping. The teardown is therefore scheduled asynchronously (R-1) but
    # not optional.
    stop_details = _schedule_workspace_stop(user_id)

    # Issue #3396: a deleted user must also lose the tenant shared-content
    # group — their shells/agents would otherwise keep OS read/write on the
    # tenant's shared projects. Fail-soft (restore re-enrolls via
    # ensure_system_user on the restore path).
    deleted_account = user.get("system_account") or username
    if deleted_account:
        from app.utils.workspace import remove_user_from_shared_group

        if not remove_user_from_shared_group(deleted_account, tenant_id=tenant_id):
            logger.warning(
                f"Failed to remove deleted {deleted_account} from tenant "
                f"{tenant_id} shared group"
            )

    if success:
        # Issue #2755 P0-3/P0-4: Critical - decrement tenant user counter with proper error handling
        counter_decremented = True
        if tenant_id:
            from app.services.tenant_service import TenantService

            tenant_service = TenantService()
            if not tenant_service.decrement_user_count(tenant_id):
                logger.error(
                    f"CRITICAL: Failed to decrement tenant user count for tenant {tenant_id} "
                    f"after deleting user {user_id}. Counter may be incorrect."
                )
                counter_decremented = False
                # Note: We don't rollback the delete here because the user is already soft-deleted
                # and sessions are revoked. The counter being off by 1 is acceptable compared to
                # data consistency issues. Log clearly for manual intervention.

        # Audit log for user deletion
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.USER_DELETE,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="user",
            resource_id=str(user_id),
            resource_name=username,
            details={
                "action": "delete",
                "tenant_id": tenant_id,
                "sessions_revoked": session_counts,
                "tokens_invalidated": tokens_invalidated,
                **stop_details,
                "counter_decremented": counter_decremented,
            },
            **client_info,
        )
        return jsonify({"success": True})

    return jsonify({"error": "Failed to delete user"}), 500


@admin_bp.route("/admin/users/<int:user_id>/restore", methods=["POST"])
@admin_required
@same_tenant_user_required
def api_restore_user(user_id):
    """Restore a soft-deleted user.

    Issue #2755: Restore soft-deleted users with optional field updates.

    The restore operation is atomic and includes:
    1. Verification that user is soft-deleted
    2. Validation that tenant_id matches original (no cross-tenant restore)
    3. Session revocation across all session tables
    4. Tenant counter increment with quota check
    5. Audit logging

    Request body (optional):
        {
            "username": "new_username",  // optional
            "email": "new@email.com",    // optional
            "password": "new_password",  // optional, must meet policy
            "role": "user",              // optional, follows privilege rules
            "is_active": true,           // optional
            "system_account": "account"  // optional
        }
    """
    data = request.get_json() or {}

    # Issue #3379 review round 2 (R-3): restore accepts the same is_active
    # shapes as PUT and must reject garbage the same way, before any
    # quota/restore work — restore_user_with_update passes the raw value to
    # the boolean column otherwise.
    try:
        restore_is_active = parse_is_active_field(data.get("is_active"))
    except ValueError:
        return jsonify({"error": "is_active must be a boolean"}), 400

    # Get the soft-deleted user
    user = user_repo.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Verify user is soft-deleted
    if user.get("deleted_at") is None:
        return jsonify({"error": "User is not soft-deleted"}), 400

    # Get original tenant_id
    original_tenant_id = user.get("tenant_id")

    # Issue #2755 D4: Prohibit cross-tenant restore
    # If tenant_id is provided in request, it must match original
    requested_tenant_id = data.get("tenant_id")
    if requested_tenant_id is not None and requested_tenant_id != original_tenant_id:
        return (
            jsonify(
                {
                    "error": "Cross-tenant restore is not allowed",
                    "message": "tenant_id must match the original tenant. Use update user API after restore to change tenant.",
                }
            ),
            400,
        )

    # Validate role if provided (follows same rules as user update)
    role = data.get("role")
    if role:
        escalation = reject_privilege_escalation(role)
        if escalation is not None:
            return escalation

        # Issue #3179: Validate that role is a persistable role
        invalid_role = validate_persistable_role(role)
        if invalid_role is not None:
            return invalid_role

    # Validate password if provided
    password = data.get("password")
    password_hash = None
    if password:
        settings = get_security_settings_cached()
        is_valid, error_msg = validate_password(password, policy_settings=settings)
        if not is_valid:
            return jsonify({"error": error_msg}), 400
        password_hash = hash_password(password)

    # Validate username/email if provided
    new_username = data.get("username")
    if new_username and not validate_username(new_username):
        return jsonify({"error": "Invalid username"}), 400

    new_email = data.get("email")
    if new_email and not validate_email(new_email):
        return jsonify({"error": "Invalid email"}), 400

    # Check for conflicts with active users (if changing username/email)
    if new_username:
        existing = user_repo.get_user_by_username(new_username, include_deleted=False)
        if existing and existing["id"] != user_id:
            return jsonify({"error": "Username already exists"}), 400

    if new_email:
        existing = user_repo.get_user_by_email(new_email, include_deleted=False)
        if existing and existing["id"] != user_id:
            return jsonify({"error": "Email already exists"}), 400

    # Validate system_account if provided
    system_account = data.get("system_account")
    if system_account and not validate_username(system_account):
        return jsonify({"error": "Invalid system_account name"}), 400

    # Check tenant quota before restoring
    from app.services.tenant_service import TenantService

    tenant_service = TenantService()
    if original_tenant_id and not tenant_service.can_add_user(original_tenant_id):
        tenant = tenant_service.get_tenant(original_tenant_id)
        max_users = tenant.quota.max_users if tenant else 0
        return (
            jsonify(
                {
                    "error": "Tenant user quota exceeded",
                    "message": f"Cannot restore user: tenant has reached maximum users ({max_users})",
                }
            ),
            409,
        )

    # Issue #2755: Revoke all sessions before restore (security cleanup)
    session_counts = user_repo.delete_all_sessions_for_user(user_id)

    # Perform restore with optional updates
    success = user_repo.restore_user_with_update(
        user_id=user_id,
        username=new_username,
        email=new_email,
        password_hash=password_hash,
        role=role,
        is_active=restore_is_active,
        system_account=system_account,
        tenant_id=None,  # Never change tenant during restore
    )

    if not success:
        return jsonify({"error": "Failed to restore user"}), 500

    # Issue #2755 P0-3/P0-4: Critical - increment tenant user counter with proper error handling
    counter_incremented = False
    if original_tenant_id:
        if not tenant_service.increment_user_count(original_tenant_id):
            logger.error(
                f"CRITICAL: Failed to increment tenant user count for tenant {original_tenant_id} "
                f"after restoring user {user_id}. Attempting rollback."
            )
            # Rollback the restore operation
            rollback_success = user_repo.delete_user(user_id, hard=False)
            if rollback_success:
                logger.info(f"Successfully rolled back restore for user {user_id}")
            else:
                logger.error(
                    f"CRITICAL: Failed to rollback restore for user {user_id}. "
                    f"User is restored but tenant counter is incorrect."
                )
            return (
                jsonify(
                    {
                        "error": "Failed to update tenant user count",
                        "message": "User restore was rolled back due to tenant counter update failure",
                    }
                ),
                500,
            )
        counter_incremented = True

    # Auto-create system user for workspace if system_account is provided
    if system_account:
        uid = data.get("system_uid")
        # Issue #3396: restore keeps the user's ORIGINAL tenant (tenant_id is
        # never changed during restore) — re-enroll into that tenant's group.
        if ensure_system_user(system_account, uid=uid, tenant_id=original_tenant_id):
            logger.info(f"System user {system_account} ready for workspace")
        else:
            logger.warning(f"Failed to create system user {system_account}, workspace may not work")

    # Audit log for user restoration
    client_info = get_client_info()
    restored_username = new_username or user.get("username")
    audit_logger.log_action(
        action=AuditAction.USER_RESTORE,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="user",
        resource_id=str(user_id),
        resource_name=restored_username,
        details={
            "action": "restore",
            "original_username": user.get("username"),
            "original_email": user.get("email"),
            "new_username": new_username,
            "new_email": new_email,
            "role": role,
            "tenant_id": original_tenant_id,
            "sessions_revoked": session_counts,
            "counter_incremented": counter_incremented,
        },
        **client_info,
    )

    return jsonify({"success": True})


@admin_bp.route("/admin/users/<int:user_id>/password", methods=["PUT"])
@admin_required
@same_tenant_user_required
def api_update_user_password(user_id):
    """Update a user's password."""
    data = request.get_json() or {}
    password: str = data.get("password", "")

    # Validate password with security policy
    settings = get_security_settings_cached()
    is_valid, error_msg = validate_password(password, policy_settings=settings)
    if not is_valid:
        return jsonify({"error": error_msg}), 400

    # Get user info for audit
    user = user_repo.get_user_by_id(user_id)

    password_hash = hash_password(password)
    success = user_repo.update_password(user_id, password_hash)

    if success:
        # Audit log for password update
        client_info = get_client_info()
        audit_logger.log_action(
            action=AuditAction.USER_PASSWORD_CHANGE,
            user_id=g.user_id,
            username=g.user.get("username"),
            resource_type="user",
            resource_id=str(user_id),
            resource_name=user.get("username") if user else None,
            details={"action": "password_update"},
            **client_info,
        )
        return jsonify({"success": True})

    return jsonify({"error": "Failed to update password"}), 500


@admin_bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
@same_tenant_user_required
def api_reset_user_password(user_id):
    """Reset user password and generate a temporary password.

    The user must change the temporary password on next login.
    Returns the temporary password to the admin for secure delivery to the user.

    Optional JSON body:
        {"password": "CustomP@ss123"}  # If provided, use this password instead of generating one.
    """
    # Get user
    user = user_repo.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Get security settings for password policy
    settings = get_security_settings_cached()

    # Check if a custom password was provided in the request body
    data = request.get_json(silent=True) or {}
    custom_password = data.get("password")

    if custom_password is not None:
        # A password key was explicitly provided
        if not custom_password:
            return jsonify({"error": "Password cannot be empty"}), 400
        # Validate the custom password against security policy
        is_valid, error_msg = validate_password(custom_password, policy_settings=settings)
        if not is_valid:
            return jsonify({"error": error_msg}), 400
        temp_password = custom_password
    else:
        # Generate a temporary password automatically
        min_length = 12  # Default to 12 for temporary passwords

        if settings:
            policy_min = settings.get("password_min_length", 8)
            # Use policy minimum if it's higher, but ensure at least 12 chars for security
            min_length = max(policy_min, 12)

        # Include uppercase, lowercase, digits, and special characters
        chars = string.ascii_letters + string.digits + "!@#$%^&*"
        temp_password = "".join(secrets.choice(chars) for _ in range(min_length))

        # Validate generated password meets policy
        is_valid, error_msg = validate_password(temp_password, policy_settings=settings)
        if not is_valid:
            # If validation fails (unlikely), regenerate with stronger requirements
            chars = string.ascii_uppercase + string.ascii_lowercase + string.digits + "!@#$%^&*"
            temp_password = "".join(secrets.choice(chars) for _ in range(16))

    # Update password
    password_hash = hash_password(temp_password)
    success = user_repo.update_password(user_id, password_hash)

    if not success:
        return jsonify({"error": "Failed to update password"}), 500

    # Set must_change_password flag to force password change on next login
    # (update_password sets it to False, so we must explicitly set it back to True)
    user_repo.set_must_change_password(user_id, True)

    logger.info(f"Password reset for user {user_id} by admin {g.user_id}")

    # Audit log for password reset
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.USER_PASSWORD_CHANGE,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="user",
        resource_id=str(user_id),
        resource_name=user.get("username"),
        details={"action": "password_reset", "must_change": True},
        **client_info,
    )

    return jsonify(
        {
            "success": True,
            "temporary_password": temp_password,
            "message": "Password reset successful. User must change password on next login.",
        }
    )


@admin_bp.route("/admin/users/<int:user_id>/quota", methods=["PUT"])
@admin_required
@same_tenant_user_required
def api_update_user_quota(user_id):
    """Update a user's quota.

    This endpoint validates:
    1. Individual quota values (range, type)
    2. Tenant allocation limit (total allocated quota must not exceed tenant limit)

    For quota increases, it uses pessimistic locking to ensure concurrent safety.
    For quota decreases, it bypasses the tenant allocation check.
    """
    from app.repositories.database import Database

    data = request.get_json() or {}

    # Step 1: Validate individual quota values
    is_valid, errors = validate_quota_update(
        daily_token_quota=data.get("daily_token_quota"),
        monthly_token_quota=data.get("monthly_token_quota"),
        daily_request_quota=data.get("daily_request_quota"),
        monthly_request_quota=data.get("monthly_request_quota"),
    )

    if not is_valid:
        # Return validation errors with i18n-friendly format
        error_messages = []
        for field, msg in errors.items():
            error_messages.append(f"{field}: {msg}")

        return (
            jsonify(
                {
                    "error": "Quota validation failed",
                    "details": errors,
                    "message": "; ".join(error_messages),
                }
            ),
            400,
        )

    # Step 2: Get current user to check if we need tenant allocation validation
    current_user = user_repo.get_user_by_id(user_id)
    if not current_user:
        return jsonify({"error": "User not found"}), 404

    tenant_id = current_user.get("tenant_id")
    if not tenant_id:
        return jsonify({"error": "User has no tenant assigned"}), 400

    # Step 3: Parse quota fields with explicit semantics
    # Three possible values for each field:
    # - Field omitted (not in request): None - keep current value (no change)
    # - Field with null value: EXPLICIT_NULL - set to unlimited
    # - Field with integer value: integer - set to specified value

    def parse_quota_field(field_name: str) -> int | None | object:
        """Parse quota field from request, distinguishing three semantics."""
        if field_name not in data:
            return None  # Field omitted - no change
        value = data[field_name]
        if value is None:
            return EXPLICIT_NULL  # Explicit null - set to unlimited
        if isinstance(value, int):
            return value
        return int(value) if value else 0  # type: ignore[call-overload]

    new_daily_token = parse_quota_field("daily_token_quota")
    new_monthly_token = parse_quota_field("monthly_token_quota")
    new_daily_request = parse_quota_field("daily_request_quota")
    new_monthly_request = parse_quota_field("monthly_request_quota")

    current_daily_token = current_user.get("daily_token_quota")
    current_monthly_token = current_user.get("monthly_token_quota")
    current_daily_request = current_user.get("daily_request_quota")
    current_monthly_request = current_user.get("monthly_request_quota")

    # Helper function to check if a quota value is increasing (needs validation)
    def is_quota_increase(new_val: int | None | object, current_val: int | None) -> bool:
        """Check if quota value is increasing (needs validation).

        Args:
            new_val: None (no change), EXPLICIT_NULL (unlimited), or int (specific value)
            current_val: Current quota value (None = unlimited, or int)

        Returns:
            True if validation is needed, False otherwise.
        """
        if new_val is None:
            return False  # No change - skip validation
        if new_val is EXPLICIT_NULL:
            return False  # Setting to unlimited - doesn't increase limit usage
        if current_val is None:
            return True  # Setting from unlimited to limited - needs validation
        # At this point, new_val must be an int
        assert isinstance(new_val, int), f"Expected int, got {type(new_val)}"
        return new_val > current_val  # Increasing specific value - needs validation

    # Check which fields are increasing
    has_increase = (
        is_quota_increase(new_daily_token, current_daily_token)
        or is_quota_increase(new_monthly_token, current_monthly_token)
        or is_quota_increase(new_daily_request, current_daily_request)
        or is_quota_increase(new_monthly_request, current_monthly_request)
    )

    # Step 4: If any quota increase, validate tenant allocation with locking
    if has_increase:
        db = Database()

        try:
            # Use pessimistic locking for concurrent safety
            # Lock the tenant row to prevent concurrent quota updates
            with db.connection() as conn:
                cursor = conn.cursor()

                # Try to acquire lock on tenant_quotas row (NOWAIT to avoid blocking)
                # For PostgreSQL: SELECT FOR UPDATE NOWAIT
                # For SQLite: No row-level locking, rely on transaction isolation
                if db.is_postgresql:
                    cursor.execute(
                        """
                        SELECT tenant_id FROM tenant_quotas
                        WHERE tenant_id = %s
                        FOR UPDATE NOWAIT
                    """,
                        (tenant_id,),
                    )
                else:
                    # SQLite: Just select the row (no FOR UPDATE)
                    cursor.execute(
                        "SELECT tenant_id FROM tenant_quotas WHERE tenant_id = ?",
                        (tenant_id,),
                    )

                lock_result = cursor.fetchone()
                if not lock_result:
                    conn.rollback()
                    return jsonify({"error": "Tenant quota configuration not found"}), 404

                # Step 5: Validate tenant allocation (within transaction)
                validation_result = validate_tenant_allocation(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    new_daily_token_quota=new_daily_token,
                    new_monthly_token_quota=new_monthly_token,
                    new_daily_request_quota=new_daily_request,
                    new_monthly_request_quota=new_monthly_request,
                    db=db,
                )

                if not validation_result["is_valid"]:
                    conn.rollback()
                    logger.warning(
                        f"Tenant quota allocation rejected for user {user_id}: "
                        f"{validation_result['error']}"
                    )
                    # Build enhanced error response with detailed context
                    error_details: dict[str, Any] = dict(validation_result.get("details", {}))
                    if validation_result.get("available"):
                        error_details["available"] = validation_result["available"]
                    if validation_result.get("is_unlimited_tenant") is not None:
                        error_details["is_unlimited_tenant"] = validation_result[
                            "is_unlimited_tenant"
                        ]
                    error_response = {
                        "error": "Tenant quota exceeded",
                        "message": validation_result.get(
                            "error", "Quota allocation exceeds tenant limit"
                        ),
                        "details": error_details,
                    }

                    return jsonify(error_response), 400

                # Step 6: Update user quota (within transaction)
                # Convert EXPLICIT_NULL to None for database storage (unlimited)
                def to_db_value(val):
                    """Convert quota value for database storage.

                    - None -> keep current (shouldn't reach here in update)
                    - EXPLICIT_NULL -> None (unlimited)
                    - int -> int
                    """
                    if val is EXPLICIT_NULL:
                        return None  # Store as NULL in database (unlimited)
                    return val

                success = user_repo.update_user_quota(
                    user_id=user_id,
                    daily_token_quota=to_db_value(new_daily_token),
                    monthly_token_quota=to_db_value(new_monthly_token),
                    daily_request_quota=to_db_value(new_daily_request),
                    monthly_request_quota=to_db_value(new_monthly_request),
                )

                if not success:
                    conn.rollback()
                    return jsonify({"error": "Failed to update quota"}), 500

                conn.commit()

        except Exception as e:
            # Handle lock acquisition failure (concurrent update in progress)
            if "lock" in str(e).lower() or "could not obtain lock" in str(e).lower():
                logger.warning(f"Concurrent quota update detected for tenant {tenant_id}")
                return (
                    jsonify(
                        {
                            "error": "Concurrent update",
                            "message": "Another quota update is in progress. Please try again.",
                        }
                    ),
                    409,
                )
            logger.exception(f"Failed to update quota for user {user_id}: {e}")
            return jsonify({"error": "Failed to update quota"}), 500
    else:
        # Quota decrease or no change: update directly without tenant check
        # Convert EXPLICIT_NULL to None for database storage
        def to_db_value_else(val):
            if val is EXPLICIT_NULL:
                return None
            return val

        success = user_repo.update_user_quota(
            user_id=user_id,
            daily_token_quota=to_db_value_else(new_daily_token),
            monthly_token_quota=to_db_value_else(new_monthly_token),
            daily_request_quota=to_db_value_else(new_daily_request),
            monthly_request_quota=to_db_value_else(new_monthly_request),
        )

        if not success:
            return jsonify({"error": "Failed to update quota"}), 500

    # Step 7: Audit log for quota update
    user = user_repo.get_user_by_id(user_id)
    client_info = get_client_info()
    audit_logger.log_action(
        action=AuditAction.QUOTA_UPDATE,
        user_id=g.user_id,
        username=g.user.get("username"),
        resource_type="user",
        resource_id=str(user_id),
        resource_name=user.get("username") if user else None,
        details={
            "action": "quota_update",
            "daily_token_quota": new_daily_token,
            "monthly_token_quota": new_monthly_token,
            "daily_request_quota": new_daily_request,
            "monthly_request_quota": new_monthly_request,
        },
        **client_info,
    )
    return jsonify({"success": True})


@admin_bp.route("/admin/quota/usage", methods=["GET"])
@admin_required
def api_quota_usage():
    """Get quota usage for all users the caller may see.

    Same tenant confinement as ``GET /admin/users`` -- this endpoint returns
    the user list too (``SELECT *`` minus the password hash, plus usage), so
    leaving it unscoped would reopen cross-tenant user enumeration at a
    sibling URL. It actually exposes more than /admin/users does, including
    each account's role, which is precisely the targeting data an attacker
    needs.
    """
    from datetime import datetime

    scope_tenant_id, denial = enforce_requested_tenant_scope(request.args.get("tenant_id"))
    if denial is not None:
        return denial

    users = user_repo.get_all_users(tenant_id=scope_tenant_id)
    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")

    for user in users:
        user.pop("password_hash", None)
        user_id: int = user["id"]
        system_account = user.get("system_account") or user.get("username", "")

        # Today's usage
        today_combined = usage_repo.get_combined_usage(
            user_id=user_id,
            system_account=system_account,
            start_date=today,
            end_date=today,
        )
        user["tokens_used_today"] = today_combined["tokens"]
        user["requests_today"] = today_combined["requests"]

        # Monthly usage
        monthly_combined = usage_repo.get_combined_usage(
            user_id=user_id,
            system_account=system_account,
            start_date=month_start,
            end_date=today,
        )
        user["tokens_used_month"] = monthly_combined["tokens"]
        user["requests_month"] = monthly_combined["requests"]

    return jsonify(users)


# Token quotas are stored in M (millions) units
TOKEN_QUOTA_MULTIPLIER = 1_000_000


@admin_bp.route("/admin/quota/stats", methods=["GET"])
@admin_required
def api_quota_stats():
    """Get quota allocation statistics for reference."""
    from app.services.tenant_service import TenantService

    scope_tenant_id, denial = enforce_requested_tenant_scope(request.args.get("tenant_id"))
    if denial is not None:
        return denial

    tenant_service = TenantService()

    # This endpoint compares allocation against ONE tenant's limits, so the
    # limits and the users summed against them must come from the SAME tenant.
    # Resolve a single concrete id and use it for both.
    #
    # Getting this wrong is not hypothetical: scoping only the user query left
    # a platform admin (scope_tenant_id None, which is how the dashboard calls
    # it -- getQuotaStats() sends no tenant_id) reading tenant 1's limits while
    # summing EVERY tenant's users, which reports percentages like 500%.
    effective_tenant_id = scope_tenant_id or g.user.get("tenant_id") or 1

    # Default tenant_id=1 for single-tenant mode.
    tenant = tenant_service.get_tenant(effective_tenant_id)
    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    tenant_quota = tenant.quota

    users = user_repo.get_all_users(tenant_id=effective_tenant_id)

    allocated = {
        "daily_token": 0,
        "monthly_token": 0,
        "daily_request": 0,
        "monthly_request": 0,
    }

    active_users = 0
    for user in users:
        if user.get("is_active", True):
            active_users += 1
            if user.get("daily_token_quota"):
                allocated["daily_token"] += user["daily_token_quota"]
            if user.get("monthly_token_quota"):
                allocated["monthly_token"] += user["monthly_token_quota"]
            if user.get("daily_request_quota"):
                allocated["daily_request"] += user["daily_request_quota"]
            if user.get("monthly_request_quota"):
                allocated["monthly_request"] += user["monthly_request_quota"]

    # Calculate remaining (token quotas stored in M units)
    remaining = {
        "daily_token": tenant_quota.daily_token_limit
        - allocated["daily_token"] * TOKEN_QUOTA_MULTIPLIER,
        "monthly_token": tenant_quota.monthly_token_limit
        - allocated["monthly_token"] * TOKEN_QUOTA_MULTIPLIER,
        "daily_request": tenant_quota.daily_request_limit - allocated["daily_request"],
        "monthly_request": tenant_quota.monthly_request_limit - allocated["monthly_request"],
    }

    # Calculate percentages
    def calc_percent(allocated_val: float, limit_val: int) -> float:
        if limit_val <= 0:
            return 0.0
        return round((allocated_val / limit_val) * 100, 1)

    percentages = {
        "daily_token": calc_percent(
            allocated["daily_token"] * TOKEN_QUOTA_MULTIPLIER, tenant_quota.daily_token_limit
        ),
        "monthly_token": calc_percent(
            allocated["monthly_token"] * TOKEN_QUOTA_MULTIPLIER, tenant_quota.monthly_token_limit
        ),
        "daily_request": calc_percent(allocated["daily_request"], tenant_quota.daily_request_limit),
        "monthly_request": calc_percent(
            allocated["monthly_request"], tenant_quota.monthly_request_limit
        ),
    }

    return jsonify(
        {
            "tenant_quota": {
                "daily_token_limit": tenant_quota.daily_token_limit,
                "monthly_token_limit": tenant_quota.monthly_token_limit,
                "daily_request_limit": tenant_quota.daily_request_limit,
                "monthly_request_limit": tenant_quota.monthly_request_limit,
                "max_users": tenant_quota.max_users,
            },
            "allocated": allocated,
            "remaining": remaining,
            "percentages": percentages,
            "user_count": {
                "total": len(users),
                "active": active_users,
                "max": tenant_quota.max_users,
            },
        }
    )


@admin_bp.route("/admin/quota/validate-allocation", methods=["POST"])
@admin_required
def api_validate_quota_allocation():
    """Validate if a quota allocation would exceed tenant limits.

    This endpoint is used by frontend to provide real-time feedback
    before the user submits the quota update form.

    Request body:
        {
            "user_id": int (optional, for update scenario),
            "daily_token_quota": int (optional),
            "monthly_token_quota": int (optional),
            "daily_request_quota": int (optional),
            "monthly_request_quota": int (optional)
        }

    Response:
        {
            "valid": bool,
            "available": {
                "daily_token": int,
                "monthly_token": int,
                "daily_request": int,
                "monthly_request": int
            },
            "message": str (optional, if invalid)
        }
    """
    from app.repositories.database import Database

    data = request.get_json() or {}

    # Get tenant_id from current user context
    tenant_id = g.user.get("tenant_id")
    if not tenant_id:
        return jsonify({"error": "User has no tenant assigned"}), 400

    user_id = data.get("user_id")
    new_daily_token = data.get("daily_token_quota")
    new_monthly_token = data.get("monthly_token_quota")
    new_daily_request = data.get("daily_request_quota")
    new_monthly_request = data.get("monthly_request_quota")

    # Validate the allocation
    db = Database()
    result = validate_tenant_allocation(
        tenant_id=tenant_id,
        user_id=user_id,
        new_daily_token_quota=new_daily_token,
        new_monthly_token_quota=new_monthly_token,
        new_daily_request_quota=new_daily_request,
        new_monthly_request_quota=new_monthly_request,
        db=db,
    )

    response = {
        "valid": result["is_valid"],
        "available": result.get("available", {}),
        "is_unlimited_tenant": result.get("is_unlimited_tenant", False),
    }

    if not result["is_valid"]:
        response["message"] = result.get("error", "Quota allocation exceeds tenant limit")

    return jsonify(response)


@admin_bp.route("/admin/quota/health-check", methods=["POST"])
@admin_required
def api_quota_health_check():
    """Check tenant quota health status.

    Detects if the total allocated quota exceeds tenant limits.

    Request body (optional):
        {
            "tenant_id": int (optional, defaults to current user's tenant)
        }

    Response:
        {
            "tenant_id": int,
            "status": "ok" | "over_allocated",
            "allocated": {
                "daily_token": int,
                "monthly_token": int,
                "daily_request": int,
                "monthly_request": int
            },
            "limit": {
                "daily_token": int,
                "monthly_token": int,
                "daily_request": int,
                "monthly_request": int
            },
            "over_by": {
                "daily_token": int (if over-allocated),
                "monthly_token": int (if over-allocated),
                "daily_request": int (if over-allocated),
                "monthly_request": int (if over-allocated)
            } (optional)
        }
    """
    from app.services.tenant_service import TenantService

    data = request.get_json(silent=True) or {}

    # Get tenant_id from request, confined to what the caller may look at. The
    # body value used to be taken on trust, so a tenant admin could read any
    # other tenant's quota allocation and headroom by naming it here.
    tenant_id, denial = enforce_requested_tenant_scope(data.get("tenant_id"))
    if denial is not None:
        return denial
    if tenant_id is None:
        # Platform admin with no tenant named, or a caller with no tenant.
        tenant_id = g.user.get("tenant_id")
    if not tenant_id:
        return jsonify({"error": "Tenant ID is required"}), 400

    tenant_service = TenantService()
    tenant = tenant_service.get_tenant(tenant_id)

    if not tenant:
        return jsonify({"error": "Tenant not found"}), 404

    tenant_quota = tenant.quota

    # Calculate allocated quotas from all users
    users = user_repo.get_all_users(tenant_id=tenant_id)

    allocated = {
        "daily_token": 0,
        "monthly_token": 0,
        "daily_request": 0,
        "monthly_request": 0,
    }

    for user in users:
        if user.get("is_active", True):
            if user.get("daily_token_quota"):
                allocated["daily_token"] += user["daily_token_quota"]
            if user.get("monthly_token_quota"):
                allocated["monthly_token"] += user["monthly_token_quota"]
            if user.get("daily_request_quota"):
                allocated["daily_request"] += user["daily_request_quota"]
            if user.get("monthly_request_quota"):
                allocated["monthly_request"] += user["monthly_request_quota"]

    # Convert allocated tokens to actual count for comparison
    allocated_daily_tokens_actual = allocated["daily_token"] * TOKEN_QUOTA_MULTIPLIER
    allocated_monthly_tokens_actual = allocated["monthly_token"] * TOKEN_QUOTA_MULTIPLIER

    # Check if over-allocated
    is_over_allocated = False
    over_by = {}

    if (
        tenant_quota.daily_token_limit
        and allocated_daily_tokens_actual > tenant_quota.daily_token_limit
    ):
        is_over_allocated = True
        over_by["daily_token"] = allocated_daily_tokens_actual - tenant_quota.daily_token_limit

    if (
        tenant_quota.monthly_token_limit
        and allocated_monthly_tokens_actual > tenant_quota.monthly_token_limit
    ):
        is_over_allocated = True
        over_by["monthly_token"] = (
            allocated_monthly_tokens_actual - tenant_quota.monthly_token_limit
        )

    if (
        tenant_quota.daily_request_limit
        and allocated["daily_request"] > tenant_quota.daily_request_limit
    ):
        is_over_allocated = True
        over_by["daily_request"] = allocated["daily_request"] - tenant_quota.daily_request_limit

    if (
        tenant_quota.monthly_request_limit
        and allocated["monthly_request"] > tenant_quota.monthly_request_limit
    ):
        is_over_allocated = True
        over_by["monthly_request"] = (
            allocated["monthly_request"] - tenant_quota.monthly_request_limit
        )

    response = {
        "tenant_id": tenant_id,
        "status": "over_allocated" if is_over_allocated else "ok",
        "allocated": {
            "daily_token": allocated["daily_token"],
            "monthly_token": allocated["monthly_token"],
            "daily_request": allocated["daily_request"],
            "monthly_request": allocated["monthly_request"],
        },
        "limit": {
            "daily_token": tenant_quota.daily_token_limit,
            "monthly_token": tenant_quota.monthly_token_limit,
            "daily_request": tenant_quota.daily_request_limit,
            "monthly_request": tenant_quota.monthly_request_limit,
        },
    }

    if is_over_allocated:
        response["over_by"] = over_by

    return jsonify(response)


@admin_bp.route("/admin/feishu/sync", methods=["POST"])
@admin_required
def api_sync_feishu_org():
    """Manually trigger a Feishu organization sync.

    The body's ``tenant_id`` used to be taken on trust. Because a sync creates
    and updates users and teams in the target tenant, that was a cross-tenant
    *write*: a tenant admin could name somebody else's tenant and reshape its
    org tree. enforce_requested_tenant_scope also handles the int coercion and
    the 400 that used to be done by hand here.
    """
    data = request.get_json(silent=True) or {}
    tenant_id, denial = enforce_requested_tenant_scope(data.get("tenant_id"))
    if denial is not None:
        return denial

    try:
        from app.services.feishu_org_sync import FeishuOrgSyncService, SyncStatus

        result = FeishuOrgSyncService().sync_org(tenant_id=tenant_id)
        success = result.status == SyncStatus.SUCCESS
        response = {"success": success, "result": result.to_dict()}
        if result.status == SyncStatus.FAILED:
            return jsonify(response), 500
        return jsonify(response)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Failed to sync Feishu org: %s", e)
        return jsonify({"error": "Failed to sync Feishu org", "detail": str(e)}), 500


@admin_bp.route("/admin/dingtalk/sync", methods=["POST"])
@admin_required
def api_sync_dingtalk_org():
    """Manually trigger a DingTalk organization sync.

    Same cross-tenant write as the Feishu sibling above -- see that docstring.
    """
    data = request.get_json(silent=True) or {}
    tenant_id, denial = enforce_requested_tenant_scope(data.get("tenant_id"))
    if denial is not None:
        return denial

    try:
        from app.services.dingtalk_org_sync import DingTalkOrgSyncService, SyncStatus

        result = DingTalkOrgSyncService().sync_org(tenant_id=tenant_id)
        success = result.status == SyncStatus.SUCCESS
        response = {"success": success, "result": result.to_dict()}
        if result.status == SyncStatus.FAILED:
            return jsonify(response), 500
        return jsonify(response)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.exception("Failed to sync DingTalk org: %s", e)
        return jsonify({"error": "Failed to sync DingTalk org", "detail": str(e)}), 500


def _org_sync_lock_state_payload(provider: str, key: int) -> dict:
    """Inspect (without releasing) an org-sync advisory lock.

    Returns the holder pid and approximate hold time, or ``running: None`` when
    nothing holds the lock (or on non-Postgres, where there is no cross-process
    lock to inspect). Used by both Feishu and DingTalk lock-state endpoints.
    """
    from app.repositories.database import Database
    from app.services._org_sync_lock import get_running_sync_state

    db = Database()
    state = get_running_sync_state(db, key)
    return {
        "provider": provider,
        "key": key,
        "is_postgresql": bool(getattr(db, "is_postgresql", False)),
        "running": state,
    }


def _release_org_sync_lock_payload(provider: str, key: int) -> dict:
    """Forcefully release a stuck org-sync advisory lock.

    Reports the holder state before the release and whether the lock was gone on
    return. Used to recover from a hung sync whose advisory lock would otherwise
    block every future run. Postgres only (terminates the holder backend via
    ``pg_terminate_backend`` then polls until the lock row disappears); on other
    backends there is no cross-process lock, so ``released`` is True trivially.
    """
    from app.repositories.database import Database
    from app.services._org_sync_lock import force_release_lock, get_running_sync_state

    db = Database()
    before = get_running_sync_state(db, key)
    released = force_release_lock(db, key)
    return {
        "provider": provider,
        "key": key,
        "is_postgresql": bool(getattr(db, "is_postgresql", False)),
        "before": before,
        "released": bool(released),
    }


@admin_bp.route("/admin/feishu/sync/lock-state", methods=["GET"])
@admin_required
def api_feishu_sync_lock_state():
    """Inspect the Feishu org-sync advisory lock (holder pid + hold time)."""
    from app.services.feishu_org_sync import FeishuOrgSyncService

    try:
        return jsonify(
            _org_sync_lock_state_payload("feishu", FeishuOrgSyncService._DB_SYNC_LOCK_KEY)
        )
    except Exception as e:
        logger.exception("Failed to inspect Feishu org-sync lock: %s", e)
        return jsonify({"error": "Failed to inspect Feishu org-sync lock"}), 500


@admin_bp.route("/admin/feishu/sync/release-lock", methods=["POST"])
@admin_required
def api_release_feishu_sync_lock():
    """Forcefully release a stuck Feishu org-sync advisory lock."""
    from app.services.feishu_org_sync import FeishuOrgSyncService

    try:
        return jsonify(
            _release_org_sync_lock_payload("feishu", FeishuOrgSyncService._DB_SYNC_LOCK_KEY)
        )
    except Exception as e:
        logger.exception("Failed to release Feishu org-sync lock: %s", e)
        return jsonify({"error": "Failed to release Feishu org-sync lock"}), 500


@admin_bp.route("/admin/dingtalk/sync/lock-state", methods=["GET"])
@admin_required
def api_dingtalk_sync_lock_state():
    """Inspect the DingTalk org-sync advisory lock (holder pid + hold time)."""
    from app.services.dingtalk_org_sync import DingTalkOrgSyncService

    try:
        return jsonify(
            _org_sync_lock_state_payload("dingtalk", DingTalkOrgSyncService._DB_SYNC_LOCK_KEY)
        )
    except Exception as e:
        logger.exception("Failed to inspect DingTalk org-sync lock: %s", e)
        return jsonify({"error": "Failed to inspect DingTalk org-sync lock"}), 500


@admin_bp.route("/admin/dingtalk/sync/release-lock", methods=["POST"])
@admin_required
def api_release_dingtalk_sync_lock():
    """Forcefully release a stuck DingTalk org-sync advisory lock."""
    from app.services.dingtalk_org_sync import DingTalkOrgSyncService

    try:
        return jsonify(
            _release_org_sync_lock_payload("dingtalk", DingTalkOrgSyncService._DB_SYNC_LOCK_KEY)
        )
    except Exception as e:
        logger.exception("Failed to release DingTalk org-sync lock: %s", e)
        return jsonify({"error": "Failed to release DingTalk org-sync lock"}), 500
