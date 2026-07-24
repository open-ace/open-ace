"""Query parameter sanitizer middleware for Open ACE.

Sanitizes sensitive query parameters from request logs to prevent
credential leakage in access logs, monitoring systems, and error reports.

Issue #1896: URL token security.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Sensitive query parameter names to sanitize
SENSITIVE_PARAMS = frozenset(
    [
        "token",
        "session_token",
        "auth",
        "api_key",
        "secret",
        "password",
        "access_token",
        "refresh_token",
        "api-key",
        "apikey",
    ]
)

# Replacement text for sanitized values
REDACTED = "[REDACTED]"


def sanitize_query_string(query_string: str) -> str:
    """Sanitize sensitive parameters from a query string.

    Args:
        query_string: Raw query string (e.g., "token=abc123&other=value").

    Returns:
        Sanitized query string with sensitive values replaced.
    """
    if not query_string:
        return query_string

    # Split into parameters
    params = query_string.split("&")
    sanitized_params = []

    for param in params:
        if not param:
            continue

        # Split into key=value
        if "=" in param:
            key, value = param.split("=", 1)
            # Check if key is sensitive
            if key.lower() in SENSITIVE_PARAMS:
                sanitized_params.append(f"{key}={REDACTED}")
            else:
                sanitized_params.append(param)
        else:
            # No value, just key
            if param.lower() in SENSITIVE_PARAMS:
                sanitized_params.append(f"{param}={REDACTED}")
            else:
                sanitized_params.append(param)

    return "&".join(sanitized_params)


def sanitize_url(url: str) -> str:
    """Sanitize sensitive parameters from a URL.

    Args:
        url: Full URL or path with query string.

    Returns:
        Sanitized URL with sensitive values replaced.
    """
    if not url:
        return url

    # Split URL into parts
    if "?" in url:
        base, query = url.split("?", 1)
        sanitized_query = sanitize_query_string(query)
        return f"{base}?{sanitized_query}"

    return url


class QueryParamSanitizer:
    """WSGI middleware that sanitizes sensitive query parameters from logs.

    This middleware wraps the WSGI application and sanitizes the query
    string before it's logged or stored in access logs.

    Usage:
        app = Flask(__name__)
        app.wsgi_app = QueryParamSanitizer(app.wsgi_app)
    """

    def __init__(self, app: Any):
        """Initialize the middleware.

        Args:
            app: The WSGI application to wrap.
        """
        self.app = app

    def __call__(self, environ: dict, start_response: Any) -> Any:
        """Process the request, sanitizing the query string in environ.

        Args:
            environ: WSGI environ dict.
            start_response: WSGI start_response callable.

        Returns:
            Response iterator.
        """
        # Sanitize QUERY_STRING
        query_string = environ.get("QUERY_STRING", "")
        if query_string:
            environ["QUERY_STRING"] = sanitize_query_string(query_string)

        # Sanitize REQUEST_URI if present (for some WSGI servers)
        request_uri = environ.get("REQUEST_URI", "")
        if request_uri:
            environ["REQUEST_URI"] = sanitize_url(request_uri)

        # Sanitize PATH_INFO's query string if present
        path_info = environ.get("PATH_INFO", "")
        if "?" in path_info:
            base, query = path_info.split("?", 1)
            environ["PATH_INFO"] = f"{base}?{sanitize_query_string(query)}"

        return self.app(environ, start_response)


__all__ = [
    "sanitize_query_string",
    "sanitize_url",
    "QueryParamSanitizer",
    "SENSITIVE_PARAMS",
    "REDACTED",
]
