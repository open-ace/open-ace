"""
Open ACE - Constants Package

Constants used across the application.
"""

from app.constants.request_stats_meta import REQUEST_STATS_META

# Sentinel object to distinguish "explicitly set to null" from "field not provided"
# Used in quota update API to differentiate:
# - Field omitted: keep current value (no change)
# - Field with null value: set to unlimited
# - Field with integer value: set to specified value
EXPLICIT_NULL = object()

__all__ = ["REQUEST_STATS_META", "EXPLICIT_NULL"]
