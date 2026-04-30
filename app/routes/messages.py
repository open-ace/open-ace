#!/usr/bin/env python3
"""
Open ACE - Messages Routes

API routes for message data operations.
"""

import time

from flask import Blueprint, jsonify, request

from app.services.message_service import MessageService

messages_bp = Blueprint("messages", __name__)
message_service = MessageService()

# Simple in-memory cache for expensive queries
_senders_cache = {"data": None, "timestamp": 0}
_senders_cache_ttl = 300  # 5 minutes


@messages_bp.route("/messages")
def api_messages():
    """Get messages with pagination and filters."""
    date = request.args.get("date")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    tool = request.args.get("tool")
    host = request.args.get("host")
    sender = request.args.get("sender")
    role = request.args.get("role")
    search = request.args.get("search")
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)

    result = message_service.get_messages(
        date=date,
        start_date=start_date,
        end_date=end_date,
        tool_name=tool,
        host_name=host,
        sender_name=sender,
        role=role,
        search=search,
        limit=limit,
        offset=offset,
    )
    return jsonify(result)


@messages_bp.route("/senders")
def api_senders():
    """Get list of all senders (cached for 5 minutes)."""
    host = request.args.get("host")
    now = time.time()
    if host:
        # Host-specific queries are less common, skip cache
        senders = message_service.get_all_senders(host_name=host)
        return jsonify(senders)

    if (
        _senders_cache["data"] is not None
        and (now - _senders_cache["timestamp"]) < _senders_cache_ttl
    ):
        return jsonify(_senders_cache["data"])

    senders = message_service.get_all_senders()
    _senders_cache["data"] = senders
    _senders_cache["timestamp"] = now
    return jsonify(senders)


@messages_bp.route("/conversation-history")
def api_conversation_history():
    """Get conversation history."""
    date = request.args.get("date")
    tool = request.args.get("tool")
    host = request.args.get("host")
    sender = request.args.get("sender")
    limit = request.args.get("limit", 50, type=int)
    offset = request.args.get("offset", 0, type=int)

    conversations = message_service.get_conversation_history(
        date=date, tool_name=tool, host_name=host, sender_name=sender, limit=limit, offset=offset
    )
    total = message_service.count_conversations(
        date=date, tool_name=tool, host_name=host, sender_name=sender
    )
    return jsonify({"data": conversations, "total": total})


@messages_bp.route("/conversation-timeline/<path:session_id>")
def api_conversation_timeline(session_id):
    """Get timeline of messages for a conversation."""
    messages = message_service.get_conversation_timeline(session_id)
    return jsonify(messages)


@messages_bp.route("/conversation-details/<path:session_id>")
def api_conversation_details(session_id):
    """Get details of a conversation."""
    details = message_service.get_conversation_details(session_id)
    if details:
        return jsonify(details)
    return jsonify({"error": "Conversation not found"}), 404


@messages_bp.route("/messages/count")
def api_messages_count():
    """Get count of messages with filters."""
    date = request.args.get("date")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    tool = request.args.get("tool")
    host = request.args.get("host")
    sender = request.args.get("sender")
    role = request.args.get("role")
    search = request.args.get("search")

    count = message_service.count_messages(
        date=date,
        start_date=start_date,
        end_date=end_date,
        tool_name=tool,
        host_name=host,
        sender_name=sender,
        role=role,
        search=search,
    )
    return jsonify({"count": count})
