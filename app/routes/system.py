"""
Open ACE - System API Routes

REST API endpoints for system status and administration:
- Scheduler status
- Health checks
- Admin operations
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from app.auth.decorators import _extract_token, _load_user_from_token

logger = logging.getLogger(__name__)

system_bp = Blueprint("system", __name__)


@system_bp.before_request
def load_user():
    """Load the current user from session token before each request."""
    token = _extract_token()
    if token:
        user = _load_user_from_token(token)
        if user:
            g.user = user
            g.user_id = user.get("id")
            g.user_role = user.get("role")
            return None
    return jsonify({"error": "Authentication required"}), 401


def _admin_required():
    """Check if the current user is an admin."""
    if not hasattr(g, "user_role") or g.user_role != "admin":
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

        return jsonify({
            "success": True,
            "data": statuses,
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        })

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
        return jsonify({
            "success": True,
            "message": "Quota check triggered",
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        })

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
        batch_size = request.json.get("batch_size", 1000) if request.json else 1000

        from app.repositories.database import Database, adapt_sql, is_postgresql

        db = Database()

        # Count quota_alerts
        count_result = db.fetch_one("SELECT COUNT(*) as count FROM quota_alerts")
        total_count = count_result.get("count", 0) if count_result else 0

        if total_count == 0:
            return jsonify({
                "success": True,
                "data": {
                    "migrated": 0,
                    "total": 0,
                    "message": "No quota_alerts to migrate",
                },
            })

        # Migrate in batches
        migrated = 0
        errors = []

        # For now, just report the count
        # Full migration would require more complex logic

        return jsonify({
            "success": True,
            "data": {
                "total": total_count,
                "migrated": migrated,
                "errors": errors,
                "message": f"Found {total_count} quota_alerts to migrate. Migration not yet implemented.",
            },
        })

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

        return jsonify({
            "success": True,
            "data": {
                "quota_alerts_total": quota_count.get("count", 0) if quota_count else 0,
                "alerts_quota_count": alerts_count.get("count", 0) if alerts_count else 0,
                "migration_in_progress": False,
            },
        })

    except Exception as e:
        logger.error(f"Error getting migration progress: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500