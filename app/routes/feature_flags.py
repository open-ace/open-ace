"""
Open ACE - Feature Flags API Routes.

REST API endpoint that returns the current state of all configurable feature flags.
Used by frontend to dynamically control UI elements based on feature availability.

Each flag reads from config.json with 60-second TTL cache, mirroring the behavior
of individual flag functions in app/utils/config.py.
"""

import logging

from flask import Blueprint, jsonify

from app.utils.config import (
    is_autonomous_enabled,
    is_model_gateway_enabled,
    is_policy_enabled,
    is_run_timeline_enabled,
)

logger = logging.getLogger(__name__)

feature_flags_bp = Blueprint("feature_flags", __name__)


@feature_flags_bp.route("/api/feature-flags", methods=["GET"])
def get_feature_flags():
    """Get current state of all feature flags.

    Returns:
        JSON object with feature flag states:
        {
            "model_gateway": false,
            "run_timeline": true,
            "policy": false,
            "autonomous": false
        }
    """
    try:
        return jsonify(
            {
                "model_gateway": is_model_gateway_enabled(),
                "run_timeline": is_run_timeline_enabled(),
                "policy": is_policy_enabled(),
                "autonomous": is_autonomous_enabled(),
            }
        )
    except Exception as e:
        logger.error("Error getting feature flags: %s", e)
        return jsonify({"error": "Internal server error"}), 500