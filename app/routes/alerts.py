#!/usr/bin/env python3
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
from datetime import datetime

from flask import Blueprint, g, jsonify, request

from app.modules.governance.alert_notifier import NotificationPreference, get_alert_notifier
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)

alerts_bp = Blueprint("alerts", __name__)
auth_service = AuthService()


@alerts_bp.before_request
def load_user():
    """Load the current user from session token before each request.

    All alerts endpoints require authentication. Returns 401 if no valid
    session token is provided.
    """
    token = request.cookies.get("session_token") or request.headers.get(
        "Authorization", ""
    ).replace("Bearer ", "")

    if token:
        session = auth_service.validate_session(token)
        if session[0]:
            session_data = session[1]
            g.user = {
                "id": session_data.get("user_id"),
                "username": session_data.get("username"),
                "email": session_data.get("email"),
                "role": session_data.get("role"),
            }
            return None  # Authenticated
        else:
            return jsonify({"error": "Authentication required"}), 401
    else:
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
    """Mark an alert as read."""
    try:
        notifier = get_alert_notifier()
        success = notifier.mark_as_read(alert_id)

        if not success:
            return jsonify({"success": False, "error": "Alert not found"}), 404

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
    """Delete an alert."""
    try:
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
                    "webhook_url": prefs.webhook_url,
                    "alert_types": prefs.alert_types,
                    "min_severity": prefs.min_severity,
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
        prefs = NotificationPreference(
            user_id=user_id,
            email_enabled=data.get("email_enabled", True),
            push_enabled=data.get("push_enabled", True),
            webhook_url=data.get("webhook_url"),
            alert_types=data.get("alert_types", ["quota", "system", "security"]),
            min_severity=data.get("min_severity", "warning"),
        )

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
    except Exception as e:
        logger.error(f"Error creating test alert: {e}")
        return jsonify({"success": False, "error": "Internal server error"}), 500


# ==================== WebSocket Support ====================
# Note: For WebSocket support, the application needs to use Flask-SocketIO or similar.
# This provides a simple SSE (Server-Sent Events) alternative for real-time updates.


@alerts_bp.route("/alerts/stream")
def alert_stream():
    """Server-Sent Events stream for real-time alerts."""
    from flask import Response

    user_id = g.user.get("id") if hasattr(g, "user") and g.user else None

    def generate():
        """Generate SSE events."""
        import time

        # Send initial connection message
        yield f"data: {json.dumps({'type': 'connected', 'user_id': user_id})}\n\n"

        # Keep connection alive and check for new alerts
        last_check = datetime.utcnow()
        notifier = get_alert_notifier()

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
                    if alert.created_at > last_check:
                        yield f"data: {json.dumps({'type': 'alert', 'data': alert.to_dict()})}\n\n"

                last_check = datetime.utcnow()

                # Send heartbeat
                yield ": heartbeat\n\n"

            except Exception as e:
                logger.error(f"Error in SSE stream: {e}")
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

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
