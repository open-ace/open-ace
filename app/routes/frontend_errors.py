"""
Frontend Error Reporting API

Receives error reports from the frontend and logs them.
"""

import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request

from app.auth.decorators import public_endpoint

frontend_errors_bp = Blueprint("frontend_errors", __name__)
logger = logging.getLogger(__name__)

# Rate limiting constants
MAX_CACHE_ENTRIES = 10000
RATE_LIMIT = 10
RATE_WINDOW = 60

# Thread-safe rate limit cache
_rate_limit_cache: dict[str, list[datetime]] = defaultdict(list)
_rate_limit_lock = threading.Lock()


def check_rate_limit(ip: str) -> bool:
    """Check IP-level rate limit.

    Args:
        ip: Client IP address

    Returns:
        True if request is allowed, False if rate limit exceeded
    """
    now = datetime.now()
    window_start = now - timedelta(seconds=RATE_WINDOW)

    with _rate_limit_lock:
        # Always clean up expired entries for current IP
        _rate_limit_cache[ip] = [t for t in _rate_limit_cache[ip] if t > window_start]

        # Periodic cleanup when cache is too large
        if len(_rate_limit_cache) > MAX_CACHE_ENTRIES:
            for cached_ip in list(_rate_limit_cache.keys()):
                _rate_limit_cache[cached_ip] = [
                    t for t in _rate_limit_cache[cached_ip] if t > window_start
                ]
                if not _rate_limit_cache[cached_ip]:
                    del _rate_limit_cache[cached_ip]

        # Check if limit exceeded
        if len(_rate_limit_cache[ip]) >= RATE_LIMIT:
            return False

        # Record this request
        _rate_limit_cache[ip].append(now)
        return True


@frontend_errors_bp.route("/frontend-errors", methods=["POST"])
@public_endpoint
def report_frontend_error():
    """Receive frontend error reports (public endpoint, no auth required)."""
    # Check rate limit
    client_ip = request.remote_addr or "unknown"
    if not check_rate_limit(client_ip):
        return jsonify({"error": "Rate limit exceeded"}), 429

    # Parse JSON
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    # Validate required fields
    required_fields = ["category", "errorId", "name", "message", "buildVersion"]
    for field in required_fields:
        if field not in data:
            return jsonify({"error": f"Missing required field: {field}"}), 400

    # Log the error
    logger.error(
        "Frontend error reported",
        extra={
            "category": data.get("category", "unknown"),
            "error_id": data.get("errorId", "unknown"),
            "name": data.get("name", "unknown"),
            "message": str(data.get("message", ""))[:500],  # Limit length
            "pathname": data.get("pathname", "unknown"),
            "build_version": data.get("buildVersion", "unknown"),
            "commit_sha": data.get("commitSha", "unknown"),
            "user_agent": data.get("userAgent", "unknown"),
            "client_ip": client_ip,
        },
    )

    return jsonify({"status": "ok"}), 200
