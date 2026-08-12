"""Placeholder value detection utilities.

Used by feishu_config_service, feishu_org_sync, and dingtalk_org_sync
to detect template/placeholder configuration values.
"""

import re

# Placeholder patterns to detect template values
PLACEHOLDER_PATTERNS = [
    # Angle-bracket placeholders (config.json.sample format)
    r"<FEISHU_APP_ID>",
    r"<FEISHU_APP_SECRET>",
    r"<DINGTALK_APP_KEY>",
    r"<DINGTALK_APP_SECRET>",
    r"<APP_ID>",
    r"<APP_SECRET>",
    r"<APP_KEY>",
    # Generic your_* placeholders
    r"your_app_id",
    r"your_app_secret",
    r"your_app_key",
    # CLI placeholder format
    r"cli_xxxxxxxxxxxxxxxx",
]


def is_placeholder_value(value: str) -> bool:
    """Check if a value is a placeholder/template value.

    Args:
        value: The value to check.

    Returns:
        True if value matches any known placeholder pattern, False otherwise.
    """
    if not value:
        return False
    return any(re.search(pattern, value, re.IGNORECASE) for pattern in PLACEHOLDER_PATTERNS)

