"""
Open ACE - System API Routes

REST API endpoints for system status and administration:
- Scheduler status
- Health checks
- Admin operations
- System settings (global configuration)
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import (
    _extract_token,
    _load_user_from_token,
    enforce_password_change_requirement,
)
from app.models.user import User

logger = logging.getLogger(__name__)

system_bp = Blueprint("system", __name__)


# Public endpoints that don't require authentication
_PUBLIC_PATHS = [
    "/settings/sso-enabled",
    "/public/branding",
]


@system_bp.before_request
def load_user():
    """Load the current user from session token before each request."""
    # Skip auth for public endpoints
    for public_path in _PUBLIC_PATHS:
        if request.path.endswith(public_path):
            return None

    token = _extract_token()
    if token:
        user = _load_user_from_token(token)
        if user:
            g.user = user
            g.user_id = user.get("id")
            g.user_role = user.get("role")
            password_change_response = enforce_password_change_requirement(user)
            if password_change_response is not None:
                return password_change_response
            return None
    return jsonify({"error": "Authentication required"}), 401


def _admin_required():
    """Check if the current user is an admin."""
    if not hasattr(g, "user_role") or not User.is_admin_role(g.user_role):
        return jsonify({"error": "Admin access required"}), 403
    return None


# ==================== Scheduler Status ====================


@system_bp.route("/schedulers", methods=["GET"])
def get_scheduler_status():
    """Get status of all background schedulers."""
    # Admin only
    admin_check = _admin_required()
    if admin_check:
        return admin_check

    try:
        from app.services.scheduler_health_monitor import get_scheduler_status

        statuses = get_scheduler_status()

        # Add compensation worker status
        try:
            from app.services.alert_compensation_worker import compensation_worker

            statuses["alert_compensation"] = compensation_worker.get_status()
        except Exception:
            pass

        return jsonify(
            {
                "success": True,
                "data": statuses,
                "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            }
        )

    except Exception as e:
        logger.error(f"Error getting scheduler status: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@system_bp.route("/schedulers/quota-enforcement", methods=["GET"])
def get_quota_enforcement_status():
    """Get quota enforcement scheduler status."""
    admin_check = _admin_required()
    if admin_check:
        return admin_check

    try:
        from app.services.quota_enforcement_scheduler import enforcement_scheduler

        status = enforcement_scheduler.get_status()
        return jsonify({"success": True, "data": status})

    except Exception as e:
        logger.error(f"Error getting quota enforcement status: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@system_bp.route("/schedulers/data-fetch", methods=["GET"])
def get_data_fetch_status():
    """Get data fetch scheduler status."""
    admin_check = _admin_required()
    if admin_check:
        return admin_check

    try:
        from app.services.data_fetch_scheduler import scheduler

        status = scheduler.get_status()
        return jsonify({"success": True, "data": status})

    except Exception as e:
        logger.error(f"Error getting data fetch status: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Alert Failure Queue ====================


@system_bp.route("/alerts/failure-queue", methods=["GET"])
def get_failure_queue_status():
    """Get alert creation failure queue status."""
    admin_check = _admin_required()
    if admin_check:
        return admin_check

    try:
        from app.services.alert_compensation_worker import get_failure_queue_stats

        stats = get_failure_queue_stats()
        return jsonify({"success": True, "data": stats})

    except Exception as e:
        logger.error(f"Error getting failure queue status: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@system_bp.route("/alerts/failure-queue/retry", methods=["POST"])
def retry_failure_queue():
    """Manually trigger processing of the failure queue."""
    admin_check = _admin_required()
    if admin_check:
        return admin_check

    try:
        from app.services.alert_compensation_worker import compensation_worker

        result = compensation_worker.process_now()
        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"Error processing failure queue: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Alert Consistency Check ====================


@system_bp.route("/alerts/consistency-check", methods=["GET"])
def check_alert_consistency():
    """Check consistency between quota_alerts and alerts tables."""
    admin_check = _admin_required()
    if admin_check:
        return admin_check

    try:
        from app.modules.governance.alert_state_synchronizer import get_synchronizer

        synchronizer = get_synchronizer()
        result = synchronizer.check_consistency()
        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"Error checking alert consistency: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@system_bp.route("/alerts/sync-cleanup", methods=["POST"])
def sync_alert_cleanup():
    """Trigger synchronized cleanup of old alerts."""
    admin_check = _admin_required()
    if admin_check:
        return admin_check

    try:
        days = request.json.get("days", 30) if request.json else 30

        from app.modules.governance.alert_state_synchronizer import sync_cleanup

        result = sync_cleanup(days=days)
        return jsonify({"success": True, "data": result})

    except Exception as e:
        logger.error(f"Error in sync cleanup: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Quota Check ====================


@system_bp.route("/quota/check-all", methods=["POST"])
def trigger_quota_check():
    """Manually trigger quota check for all users."""
    admin_check = _admin_required()
    if admin_check:
        return admin_check

    try:
        from app.services.quota_enforcement_scheduler import enforcement_scheduler

        enforcement_scheduler._run_enforcement()
        return jsonify(
            {
                "success": True,
                "message": "Quota check triggered",
                "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            }
        )

    except Exception as e:
        logger.error(f"Error triggering quota check: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Data Migration ====================


@system_bp.route("/migrate-quota-alerts", methods=["POST"])
def migrate_quota_alerts():
    """Migrate quota_alerts to alerts table."""
    admin_check = _admin_required()
    if admin_check:
        return admin_check

    try:
        request.json.get("batch_size", 1000) if request.json else 1000

        from app.repositories.database import Database

        db = Database()

        # Count quota_alerts
        count_result = db.fetch_one("SELECT COUNT(*) as count FROM quota_alerts")
        total_count = count_result.get("count", 0) if count_result else 0

        if total_count == 0:
            return jsonify(
                {
                    "success": True,
                    "data": {
                        "migrated": 0,
                        "total": 0,
                        "message": "No quota_alerts to migrate",
                    },
                }
            )

        # Migrate in batches
        migrated = 0
        errors: list[str] = []

        # For now, just report the count
        # Full migration would require more complex logic

        return jsonify(
            {
                "success": True,
                "data": {
                    "total": total_count,
                    "migrated": migrated,
                    "errors": errors,
                    "message": f"Found {total_count} quota_alerts to migrate. Migration not yet implemented.",
                },
            }
        )

    except Exception as e:
        logger.error(f"Error migrating quota alerts: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@system_bp.route("/migration-progress", methods=["GET"])
def get_migration_progress():
    """Get progress of quota_alerts to alerts migration."""
    admin_check = _admin_required()
    if admin_check:
        return admin_check

    try:
        from app.repositories.database import Database

        db = Database()

        # Get counts
        quota_count = db.fetch_one("SELECT COUNT(*) as count FROM quota_alerts")
        alerts_count = db.fetch_one(
            "SELECT COUNT(*) as count FROM alerts WHERE alert_type = 'quota'"
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "quota_alerts_total": quota_count.get("count", 0) if quota_count else 0,
                    "alerts_quota_count": alerts_count.get("count", 0) if alerts_count else 0,
                    "migration_in_progress": False,
                },
            }
        )

    except Exception as e:
        logger.error(f"Error getting migration progress: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== System Settings ====================


@system_bp.route("/settings", methods=["GET"])
def get_system_settings():
    """Get all system settings.

    Requires authentication. Returns all system-level configuration.
    """
    from app.utils.config import get_all_system_settings

    try:
        settings = get_all_system_settings()
        return jsonify({"success": True, "data": settings})

    except Exception as e:
        logger.error(f"Error getting system settings: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@system_bp.route("/settings", methods=["PUT"])
def update_system_settings():
    """Update system settings.

    Requires admin privileges. Updates system-level configuration.
    """
    admin_check = _admin_required()
    if admin_check:
        return admin_check

    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "Request body required"}), 400

    # Issue #3271: Validate branding settings if present
    from app.utils.branding_validator import validate_branding_settings

    is_valid, errors = validate_branding_settings(
        logo_url=data.get("brand_logo_url"),
        system_name=data.get("brand_system_name"),
        welcome_message=data.get("brand_welcome_message"),
        copyright_text=data.get("brand_copyright_text"),
    )
    if not is_valid:
        return jsonify({"success": False, "error": "; ".join(errors)}), 400

    from app.utils.config import set_system_setting

    try:
        updated_keys = []
        errors_list = []

        for key, value in data.items():
            if set_system_setting(key, value):
                updated_keys.append(key)
            else:
                errors_list.append(f"Failed to update {key}")

        if errors_list:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Failed to update some settings",
                        "details": errors_list,
                        "updated": updated_keys,
                    }
                ),
                500,
            )

        return jsonify(
            {
                "success": True,
                "message": "System settings updated",
                "updated": updated_keys,
            }
        )

    except Exception as e:
        logger.error(f"Error updating system settings: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Public Endpoints ====================


@system_bp.route("/settings/sso-enabled", methods=["GET"])
def get_sso_enabled():
    """Get SSO enabled status.

    Public endpoint - no authentication required.
    Used by login page to determine whether to show SSO login options.

    Issue #2128: This is a GLOBAL setting stored in config.json under
    system_settings.sso_enabled. It affects all tenants - when enabled,
    the login page will display SSO login buttons for all configured providers.

    Returns:
        JSON response with sso_enabled boolean. Default is False if not configured.
    """
    from app.utils.config import is_sso_enabled

    try:
        enabled = is_sso_enabled()
        return jsonify({"success": True, "data": {"sso_enabled": enabled}})

    except Exception as e:
        logger.error(f"Error getting SSO enabled status: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@system_bp.route("/public/branding", methods=["GET"])
def get_public_branding():
    """Get branding configuration for login page.

    Public endpoint - no authentication required.
    Issue #3271: Used by login page to display custom branding.

    Query Parameters:
        tenant_slug (optional): Tenant identifier for tenant-specific branding

    Returns:
        JSON response with branding configuration:
        - logo_url: Custom logo URL or None
        - system_name: System name or None
        - welcome_message: Dict of language -> welcome text
        - copyright_text: Copyright text or None
        - is_custom: True if tenant branding is applied
    """
    from app.utils.config import get_branding_settings

    try:
        # Get tenant_slug parameter
        tenant_slug = request.args.get("tenant_slug")

        # Start with system-level branding
        system_branding = get_branding_settings()
        branding = {
            "logo_url": system_branding.get("brand_logo_url"),
            "system_name": system_branding.get("brand_system_name"),
            "welcome_message": system_branding.get("brand_welcome_message", {}),
            "copyright_text": system_branding.get("brand_copyright_text"),
            "is_custom": False,
        }

        # If tenant_slug is provided, try to get tenant branding
        if tenant_slug:
            try:
                from app.services.tenant_service import TenantService

                tenant_service = TenantService()
                tenant = tenant_service.get_tenant_by_slug(tenant_slug)

                # Check if tenant exists, is active, and has custom branding
                if tenant and tenant.is_active() and tenant.settings.custom_branding:
                    branding["is_custom"] = True

                    # Field-level override: only override non-None tenant fields
                    if tenant.settings.branding_logo_url:
                        branding["logo_url"] = tenant.settings.branding_logo_url
                    if tenant.settings.branding_name:
                        branding["system_name"] = tenant.settings.branding_name
                    if tenant.settings.branding_welcome_message:
                        # Merge welcome messages: tenant languages override system
                        branding["welcome_message"] = {
                            **branding["welcome_message"],
                            **tenant.settings.branding_welcome_message,
                        }
                    # Note: copyright_text is system-level only, not overridden by tenant
            except Exception as e:
                # Log but don't fail - fall back to system branding
                logger.warning(f"Failed to get tenant branding for slug {tenant_slug}: {e}")

        return jsonify({"success": True, "data": branding})

    except Exception as e:
        logger.error(f"Error getting branding settings: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500
