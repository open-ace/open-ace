#!/usr/bin/env python3
"""
Open ACE - Upload Routes

API routes for data upload operations.
"""

import json
import logging
import os
from functools import wraps

from flask import Blueprint, jsonify, request

from app.services.message_service import MessageService
from app.services.usage_service import UsageService

upload_bp = Blueprint("upload", __name__)
usage_service = UsageService()
message_service = MessageService()
logger = logging.getLogger(__name__)

# Upload authentication key for API access
UPLOAD_AUTH_KEY = os.environ.get("UPLOAD_AUTH_KEY")


def require_upload_auth(f):
    """Decorator to require upload authentication."""

    @wraps(f)
    def decorated(*args, **kwargs):
        # Check for UPLOAD_AUTH_KEY in environment
        if not UPLOAD_AUTH_KEY:
            logger.error("UPLOAD_AUTH_KEY not configured - upload endpoints disabled")
            return jsonify({"error": "Upload service not configured"}), 503

        # Check Authorization header
        auth_header = request.headers.get("X-Upload-Auth")
        if not auth_header or auth_header != UPLOAD_AUTH_KEY:
            logger.warning("Unauthorized upload attempt")
            return jsonify({"error": "Unauthorized"}), 401

        return f(*args, **kwargs)

    return decorated


@upload_bp.route("/upload/usage", methods=["POST"])
@require_upload_auth
def api_upload_usage():
    """Upload usage data."""
    data = request.get_json() or {}

    # Validate required fields
    date = data.get("date")
    tool_name = data.get("tool_name")
    tokens_used = data.get("tokens_used", 0)

    if not date or not tool_name:
        return jsonify({"error": "date and tool_name are required"}), 400

    # Optional fields
    input_tokens = data.get("input_tokens", 0)
    output_tokens = data.get("output_tokens", 0)
    cache_tokens = data.get("cache_tokens", 0)
    request_count = data.get("request_count", 0)
    models_used = data.get("models_used")
    host_name = data.get("host_name", "localhost")

    success = usage_service.save_usage(
        date=date,
        tool_name=tool_name,
        tokens_used=tokens_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_tokens=cache_tokens,
        request_count=request_count,
        models_used=models_used,
        host_name=host_name,
    )

    if success:
        return jsonify({"success": True, "message": "Usage data saved"})

    return jsonify({"error": "Failed to save usage data"}), 500


@upload_bp.route("/upload/messages", methods=["POST"])
@require_upload_auth
def api_upload_messages():
    """Upload message data."""
    data = request.get_json() or {}

    # Validate required fields
    date = data.get("date")
    tool_name = data.get("tool_name")
    messages = data.get("messages", [])

    if not date or not tool_name:
        return jsonify({"error": "date and tool_name are required"}), 400

    saved_count = 0
    errors = []

    for msg in messages:
        try:
            message_id = msg.get("message_id")
            role = msg.get("role")

            if not message_id or not role:
                errors.append("Missing message_id or role in message")
                continue

            success = message_service.save_message(
                date=date,
                tool_name=tool_name,
                message_id=message_id,
                role=role,
                host_name=msg.get("host_name", "localhost"),
                parent_id=msg.get("parent_id"),
                content=msg.get("content"),
                full_entry=(
                    json.dumps(msg) if msg.get("full_entry") is None else msg.get("full_entry")
                ),
                tokens_used=msg.get("tokens_used", 0),
                input_tokens=msg.get("input_tokens", 0),
                output_tokens=msg.get("output_tokens", 0),
                model=msg.get("model"),
                timestamp=msg.get("timestamp"),
                sender_id=msg.get("sender_id"),
                sender_name=msg.get("sender_name"),
                message_source=msg.get("message_source"),
                feishu_conversation_id=msg.get("feishu_conversation_id"),
                group_subject=msg.get("group_subject"),
                is_group_chat=msg.get("is_group_chat"),
                agent_session_id=msg.get("agent_session_id"),
                conversation_id=msg.get("conversation_id"),
            )

            if success:
                saved_count += 1
        except Exception as e:
            errors.append(str(e))

    return jsonify(
        {
            "success": True,
            "saved_count": saved_count,
            "total_count": len(messages),
            "errors": errors[:10] if errors else None,
        }
    )


@upload_bp.route("/upload/batch", methods=["POST"])
@require_upload_auth
def api_upload_batch():
    """Upload batch data (usage and messages)."""
    data = request.get_json() or {}

    results = {"usage": {"saved": 0, "errors": []}, "messages": {"saved": 0, "errors": []}}

    # Process usage data
    usage_data = data.get("usage", [])
    for u in usage_data:
        try:
            success = usage_service.save_usage(
                date=u.get("date"),
                tool_name=u.get("tool_name"),
                tokens_used=u.get("tokens_used", 0),
                input_tokens=u.get("input_tokens", 0),
                output_tokens=u.get("output_tokens", 0),
                cache_tokens=u.get("cache_tokens", 0),
                request_count=u.get("request_count", 0),
                models_used=u.get("models_used"),
                host_name=u.get("host_name", "localhost"),
            )
            if success:
                results["usage"]["saved"] += 1
        except Exception as e:
            results["usage"]["errors"].append(str(e))

    # Process message data
    messages_data = data.get("messages", [])
    for m in messages_data:
        try:
            success = message_service.save_message(
                date=m.get("date"),
                tool_name=m.get("tool_name"),
                message_id=m.get("message_id"),
                role=m.get("role"),
                host_name=m.get("host_name", "localhost"),
                parent_id=m.get("parent_id"),
                content=m.get("content"),
                full_entry=m.get("full_entry"),
                tokens_used=m.get("tokens_used", 0),
                input_tokens=m.get("input_tokens", 0),
                output_tokens=m.get("output_tokens", 0),
                model=m.get("model"),
                timestamp=m.get("timestamp"),
                sender_id=m.get("sender_id"),
                sender_name=m.get("sender_name"),
                message_source=m.get("message_source"),
                feishu_conversation_id=m.get("feishu_conversation_id"),
                group_subject=m.get("group_subject"),
                is_group_chat=m.get("is_group_chat"),
                agent_session_id=m.get("agent_session_id"),
                conversation_id=m.get("conversation_id"),
            )
            if success:
                results["messages"]["saved"] += 1
        except Exception as e:
            results["messages"]["errors"].append(str(e))

    return jsonify({"success": True, "results": results})
