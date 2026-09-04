"""
Open ACE - Alerts API Routes

REST API endpoints for alert management:
- List alerts
- Get unread count
- Mark alerts as read
- Notification preferences

WebSocket endpoint for real-time alerts.
"""

import json
import logging
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request
from gevent.lock import RLock

from app.auth.decorators import (
    _extract_session_token,
    _load_user_from_token,
    enforce_password_change_requirement,
    tenant_member_required,
)
from app.modules.governance.alert_notifier import (
    NotificationPreference,
    _redact_dingtalk_secret,
    get_alert_notifier,
    normalize_alert_severity,
)

logger = logging.getLogger(__name__)

alerts_bp = Blueprint("alerts", __name__)

# Issue #3332: Global gevent-safe lock for SSE cache operations
_sse_cache_lock = RLock()


@alerts_bp.before_request
def load_user():
    """Load the current user from session token before each request."""
    token = _extract_session_token()
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


# ==================== REST API ====================


@alerts_bp.route("/alerts", methods=["GET"])
def list_alerts():
    """Get alerts with filters."""
    try:
        notifier = get_alert_notifier()

        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        alert_type = request.args.get("type")
        severity = request.args.get("severity")
        unread_only = request.args.get("unread_only", "false").lower() == "true"
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))

        alerts = notifier.get_alerts(
            user_id=user_id,
            alert_type=alert_type,
            severity=severity,
            unread_only=unread_only,
            limit=limit,
            offset=offset,
        )

        return jsonify(
            {
                "success": True,
                "data": {
                    "alerts": [a.to_dict() for a in alerts],
                    "unread_count": notifier.get_unread_count(user_id),
                },
            }
        )
    except Exception as e:
        logger.error(f"Error listing alerts: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@alerts_bp.route("/alerts/unread-count", methods=["GET"])
def get_unread_count():
    """Get count of unread alerts."""
    try:
        notifier = get_alert_notifier()
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        count = notifier.get_unread_count(user_id)

        return jsonify({"success": True, "data": {"count": count}})
    except Exception as e:
        logger.error(f"Error getting unread count: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@alerts_bp.route("/alerts/<alert_id>/read", methods=["POST"])
def mark_alert_read(alert_id):
    """Mark an alert as read and sync to quota_alerts."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        notifier = get_alert_notifier()

        # Issue #3332: Validate alert ownership
        alert = notifier.get_alert_by_id(alert_id)
        if not alert:
            return jsonify({"success": False, "error": "Alert not found"}), 404

        if alert.user_id != user_id:
            logger.warning(f"User {user_id} attempted to mark alert {alert_id} owned by {alert.user_id}")
            return jsonify({"success": False, "error": "Permission denied"}), 403

        success = notifier.mark_as_read(alert_id)

        if not success:
            return jsonify({"success": False, "error": "Failed to mark alert as read"}), 500

        # Issue #3332: Clean up from cache
        from app.utils.cache import get_cache

        cache = get_cache()
        cache_key = f"sse_pushed:{user_id}"

        cached_value = cache.get(cache_key)
        if cached_value:
            with _sse_cache_lock:  # Use global lock
                pushed_alert_ids = set(cached_value)
                pushed_alert_ids.discard(alert_id)  # Remove read alert
                cache.set(cache_key, list(pushed_alert_ids), ttl=86400)

        # Sync to quota_alerts table
        try:
            from app.modules.governance.alert_state_synchronizer import sync_acknowledge

            sync_acknowledge(alert_id, user_id)
        except Exception as e:
            logger.warning(f"Failed to sync acknowledge to quota_alerts: {e}")

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error marking alert as read: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@alerts_bp.route("/alerts/read-all", methods=["POST"])
def mark_all_read():
    """Mark all alerts as read."""
    try:
        notifier = get_alert_notifier()
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

        count = notifier.mark_all_as_read(user_id)

        return jsonify({"success": True, "data": {"marked_count": count}})
    except Exception as e:
        logger.error(f"Error marking all alerts as read: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@alerts_bp.route("/alerts/<alert_id>", methods=["DELETE"])
def delete_alert(alert_id):
    """Delete an alert and sync to quota_alerts."""
    try:
        # Sync delete to quota_alerts first (need alert data before deletion)
        try:
            from app.modules.governance.alert_state_synchronizer import sync_delete

            user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
            sync_delete(alert_id, user_id)
        except Exception as e:
            logger.warning(f"Failed to sync delete to quota_alerts: {e}")

        notifier = get_alert_notifier()
        success = notifier.delete_alert(alert_id)

        if not success:
            return jsonify({"success": False, "error": "Alert not found"}), 404

        return jsonify({"success": True})
    except Exception as e:
        logger.error(f"Error deleting alert: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@alerts_bp.route("/alerts/preferences", methods=["GET"])
def get_preferences():
    """Get notification preferences for current user."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        notifier = get_alert_notifier()
        prefs = notifier.get_notification_preferences(user_id)

        return jsonify(
            {
                "success": True,
                "data": {
                    "email_enabled": prefs.email_enabled,
                    "push_enabled": prefs.push_enabled,
                    "webhook_url": _redact_dingtalk_secret(prefs.webhook_url),
                    "alert_types": prefs.alert_types,
                    "min_severity": prefs.min_severity,
                    "notification_email": prefs.notification_email,
                    "email_verified": prefs.email_verified,
                },
            }
        )
    except Exception as e:
        logger.error(f"Error getting preferences: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@alerts_bp.route("/alerts/preferences", methods=["PUT"])
def update_preferences():
    """Update notification preferences for current user."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        if not user_id:
            return jsonify({"success": False, "error": "Authentication required"}), 401

        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No data provided"}), 400

        notifier = get_alert_notifier()
        webhook_url = data.get("webhook_url")
        if isinstance(webhook_url, str):
            webhook_url = webhook_url.strip() or None

        valid, error = notifier.validate_webhook_url(webhook_url, resolve_dns=False)
        if not valid:
            return jsonify({"success": False, "error": error}), 400

        prefs = NotificationPreference(
            user_id=user_id,
            email_enabled=data.get("email_enabled", True),
            push_enabled=data.get("push_enabled", True),
            webhook_url=webhook_url,
            alert_types=data.get("alert_types", ["quota", "system", "security"]),
            min_severity=data.get("min_severity", "warning"),
            notification_email=data.get("notification_email"),
            email_verified=data.get("email_verified", False),
        )
        # Issue #1832 F2: reject an unknown min_severity threshold at write time
        # so it can't silently default the filter to ``warning`` (rank 1) later.
        try:
            prefs.min_severity = normalize_alert_severity(prefs.min_severity)
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400

        success = notifier.set_notification_preferences(prefs)

        return jsonify({"success": success})
    except Exception as e:
        logger.error(f"Error updating preferences: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


@alerts_bp.route("/alerts/test", methods=["POST"])
def create_test_alert():
    """Create a test alert (for testing purposes)."""
    try:
        user_id = g.user.get("id") if hasattr(g, "user") and g.user else None
        username = g.user.get("username", "") if hasattr(g, "user") and g.user else ""

        data = request.get_json() or {}
        alert_type = data.get("type", "system")
        severity = data.get("severity", "info")
        title = data.get("title", "Test Alert")
        message = data.get("message", "This is a test alert.")

        notifier = get_alert_notifier()
        alert = notifier.create_alert(
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            user_id=user_id,
            username=username,
        )

        return jsonify({"success": True, "data": alert.to_dict()})
    except ValueError as e:
        # Issue #1832 F2: invalid severity (or other bad input) → 400, not 500.
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logger.error(f"Error creating test alert: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== Tenant Alerts (Issue #3082) ====================


@alerts_bp.route("/alerts/tenant", methods=["GET"])
@tenant_member_required
def list_tenant_alerts():
    """
    Get tenant-scoped alerts.

    Issue #3082: Manager 角色查看租户范围内的所有类型告警。

    Data source: alert_notifier.get_alerts_by_tenant()
    Return format: {"success": true, "data": {alerts: [...], unread_count: n}}

    Permission rules:
    - platform_admin: global alerts
    - tenant_admin: tenant-scoped alerts
    - manager: tenant-scoped alerts (read-only)

    Supported filters:
    - type: alert type (quota/system/security)
    - severity: severity level (info/warning/critical)
    - unread_only: only return unread alerts

    Tenant isolation:
    - tenant_id from g.user.tenant_id (not from request parameters)
    - platform_admin with tenant_id=None returns all alerts
    """
    try:
        # Get alert_notifier instance
        notifier = get_alert_notifier()

        # Tenant ID from authenticated user (safe: not from request parameters)
        tenant_id = g.user.get("tenant_id")

        # Parameter validation with range checks
        limit = min(int(request.args.get("limit", 100)), 200)
        offset = max(0, int(request.args.get("offset", 0)))  # Issue #3082: 防止负数

        # Filter parameters
        alert_type = request.args.get("type")
        severity = request.args.get("severity")
        unread_only = request.args.get("unread_only", "false").lower() == "true"

        if tenant_id is None:
            # platform_admin: return all alerts (no tenant filter)
            alerts = notifier.get_alerts(
                alert_type=alert_type,
                severity=severity,
                unread_only=unread_only,
                limit=limit,
                offset=offset,
            )
            unread_count = notifier.get_unread_count()
        else:
            # tenant_admin / manager: tenant-scoped alerts
            alerts = notifier.get_alerts_by_tenant(
                tenant_id=tenant_id,
                alert_type=alert_type,
                severity=severity,
                unread_only=unread_only,
                limit=limit,
                offset=offset,
            )
            unread_count = notifier.get_unread_count_by_tenant(tenant_id)

        # Return format: unified with success flag
        return jsonify(
            {
                "success": True,
                "data": {
                    "alerts": [a.to_dict() for a in alerts],
                    "unread_count": unread_count,
                },
            }
        )

    except ValueError as e:
        # Specific exception for parameter parsing errors
        logger.error(f"Invalid parameter in tenant alerts: {e}")
        return jsonify({"success": False, "error": f"Invalid parameter: {e}"}), 400
    except Exception as e:
        logger.error(f"Error listing tenant alerts: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== WebSocket Support ====================
# Note: For WebSocket support, the application needs to use Flask-SocketIO or similar.
# This provides a simple SSE (Server-Sent Events) alternative for real-time updates.


@alerts_bp.route("/alerts/stream")
def alert_stream():
    """Server-Sent Events stream for real-time alerts."""
    from flask import Response

    from app.utils.cache import get_cache

    user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

    # Cache setup for deduplication (Issue #3332)
    cache = get_cache()
    cache_key = f"sse_pushed:{user_id}"

    # Read from cache (List -> Set)
    cached_value = cache.get(cache_key)
    pushed_alert_ids = set(cached_value) if cached_value else set()

    # Batch update counter
    pending_saves = 0

    def generate():
        """Generate SSE events."""
        import time

        nonlocal pushed_alert_ids, pending_saves

        # Send initial connection message
        yield f"data: {json.dumps({'type': 'connected', 'user_id': user_id})}\n\n"

        # Keep connection alive and check for new alerts
        notifier = get_alert_notifier()

        try:
            while True:
                time.sleep(5)  # Check every 5 seconds

                try:
                    # Get new alerts since last check
                    alerts = notifier.get_alerts(
                        user_id=user_id,
                        unread_only=True,
                        limit=10,
                    )

                    for alert in alerts:
                        with _sse_cache_lock:  # Use global gevent-safe lock
                            if alert.alert_id not in pushed_alert_ids:
                                yield f"data: {json.dumps({'type': 'alert', 'data': alert.to_dict()})}\n\n"
                                pushed_alert_ids.add(alert.alert_id)
                                pending_saves += 1

                                # Memory optimization: limit set size
                                if len(pushed_alert_ids) > 100:
                                    # Keep most recent 50
                                    pushed_alert_ids = set(list(pushed_alert_ids)[-50:])

                        # Batch update: save every 5 alerts
                        if pending_saves >= 5:
                            cache.set(cache_key, list(pushed_alert_ids), ttl=86400)  # 24 hours
                            pending_saves = 0

                    # Send heartbeat
                    yield ": heartbeat\n\n"

                except Exception as e:
                    logger.error(f"Error in SSE stream: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            # Save state on connection close
            with _sse_cache_lock:  # Use global gevent-safe lock
                cache.set(cache_key, list(pushed_alert_ids), ttl=86400)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


# ==================== WebSocket Event Handlers ====================
# These are designed to work with Flask-SocketIO if available


def register_socket_events(socketio):
    """
    Register WebSocket event handlers for real-time alerts.
    Call this function when initializing the Flask-SocketIO app.

    Args:
        socketio: Flask-SocketIO instance.
    """
    from flask import request

    @socketio.on("connect", namespace="/alerts")
    def handle_connect():
        """Handle client connection."""
        logger.info(f"Client connected to alerts: {request.sid}")

    @socketio.on("disconnect", namespace="/alerts")
    def handle_disconnect():
        """Handle client disconnection."""
        logger.info(f"Client disconnected from alerts: {request.sid}")
        notifier = get_alert_notifier()
        notifier.unregister_websocket(request.sid)

    @socketio.on("subscribe", namespace="/alerts")
    def handle_subscribe(data):
        """Handle subscription to user alerts."""
        user_id = data.get("user_id")
        if user_id:
            notifier = get_alert_notifier()
            from flask_socketio import join_room

            join_room(f"user_{user_id}")
            notifier.register_websocket(request.sid, None, user_id)
            logger.info(f"User {user_id} subscribed to alerts")

    @socketio.on("unsubscribe", namespace="/alerts")
    def handle_unsubscribe(data):
        """Handle unsubscription from user alerts."""
        user_id = data.get("user_id")
        if user_id:
            from flask_socketio import leave_room

            leave_room(f"user_{user_id}")
            logger.info(f"User {user_id} unsubscribed from alerts")


def broadcast_alert(socketio, alert, user_id=None):
    """
    Broadcast an alert to WebSocket clients.

    Args:
        socketio: Flask-SocketIO instance.
        alert: Alert object to broadcast.
        user_id: Optional user ID for targeted broadcast.
    """
    try:
        alert_data = alert.to_dict()

        if user_id:
            socketio.emit("alert", alert_data, room=f"user_{user_id}", namespace="/alerts")
        else:
            socketio.emit("alert", alert_data, namespace="/alerts")
    except Exception as e:
        logger.error(f"Error broadcasting alert: {e}")
