"""
Open ACE - Request Stats Metadata Constants

Defines metadata for request statistics API responses.
Issue #2773: Add _meta field to request statistics API.
"""

# Metadata for request statistics API responses
REQUEST_STATS_META = {
    "definition": "AI assistant response count (role='assistant')",
    "source": "daily_messages table",
    "note": "Counts completed user-to-AI interactions",
    "status": "implemented",
}

__all__ = ["REQUEST_STATS_META"]
