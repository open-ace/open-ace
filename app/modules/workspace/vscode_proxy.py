"""HTTP reverse proxy for VSCode (code-server) sessions.

Proxies browser HTTP requests to the remote code-server instance
running on a remote machine, accessible from the open-ace server.
"""

from __future__ import annotations

import logging
import urllib.parse

import requests

logger = logging.getLogger(__name__)

# Headers to strip when forwarding (hop-by-hop headers)
HOP_BY_HOP_HEADERS = frozenset(
    h.lower()
    for h in [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    ]
)


def proxy_request(
    method: str,
    target_url: str,
    headers: dict,
    body: bytes | None = None,
    params: dict | None = None,
) -> tuple[int, dict, bytes]:
    """Proxy a single HTTP request to the remote code-server.

    Args:
        method: HTTP method (GET, POST, PUT, DELETE, etc.)
        target_url: Full URL of the remote code-server
        headers: Request headers from the browser
        body: Request body bytes
        params: Query parameters to pass through

    Returns:
        Tuple of (status_code, response_headers, response_body)
    """
    # Filter hop-by-hop headers
    filtered_headers = {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP_HEADERS}

    # Set Host header to match the target
    parsed = urllib.parse.urlparse(target_url)
    filtered_headers["Host"] = parsed.netloc

    try:
        resp = requests.request(
            method=method,
            url=target_url,
            headers=filtered_headers,
            data=body,
            params=params,
            stream=True,
            timeout=60,
            allow_redirects=False,
        )

        # Read the full body
        response_body = resp.content

        # Build response headers, filtering hop-by-hop
        response_headers = {}
        for k, v in resp.headers.items():
            if k.lower() not in HOP_BY_HOP_HEADERS:
                response_headers[k] = v

        return resp.status_code, response_headers, response_body

    except requests.ConnectionError as e:
        logger.error("VSCode proxy connection error: %s", e)
        return 502, {"Content-Type": "text/plain"}, b"Bad Gateway: code-server unreachable"
    except requests.Timeout:
        return 504, {"Content-Type": "text/plain"}, b"Gateway Timeout: code-server did not respond"
    except Exception as e:
        logger.error("VSCode proxy error: %s", e)
        return 500, {"Content-Type": "text/plain"}, f"Internal error: {e}".encode()


def build_target_url(original_url: str, path: str) -> str:
    """Build the target URL by combining the code-server base URL with the request path.

    Args:
        original_url: The base URL of the code-server (e.g., http://host:port)
        path: The request path to append

    Returns:
        Full target URL
    """
    base = original_url.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path
    return base + path
