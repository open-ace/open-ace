"""HTTP reverse proxy for VSCode (code-server) sessions.

Proxies browser HTTP requests to the remote code-server instance
running on a remote machine, accessible from the open-ace server.
Uses streaming to efficiently handle large responses (VSCode assets).
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

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

# Create a session that bypasses system proxy settings
# System proxies (like SpeedCat) can interfere with direct IP access to remote machines
_proxy_session = requests.Session()
_proxy_session.trust_env = False


def _prepare_request_headers(headers: dict, target_url: str) -> dict:
    """Build upstream request headers for code-server.

    Force identity encoding so Flask can stream the response body without
    having to understand browser-negotiated encodings such as br or zstd.
    """
    filtered_headers = {
        k: v
        for k, v in headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() != "accept-encoding"
    }

    parsed = urllib.parse.urlparse(target_url)
    filtered_headers["Host"] = parsed.netloc
    filtered_headers["Accept-Encoding"] = "identity"
    return filtered_headers


def proxy_request(
    method: str,
    target_url: str,
    headers: dict,
    body: bytes | None = None,
    params: dict | None = None,
) -> tuple[int, dict, bytes]:
    """Proxy a single HTTP request to the remote code-server (non-streaming).

    Args:
        method: HTTP method (GET, POST, PUT, DELETE, etc.)
        target_url: Full URL of the remote code-server
        headers: Request headers from the browser
        body: Request body bytes
        params: Query parameters to pass through

    Returns:
        Tuple of (status_code, response_headers, response_body)
    """
    filtered_headers = _prepare_request_headers(headers, target_url)

    try:
        resp = _proxy_session.request(
            method=method,
            url=target_url,
            headers=filtered_headers,
            data=body,
            params=params,
            timeout=60,
            allow_redirects=False,
        )

        # Build response headers, filtering hop-by-hop
        response_headers = {}
        for k, v in resp.headers.items():
            if k.lower() not in HOP_BY_HOP_HEADERS:
                response_headers[k] = v

        return resp.status_code, response_headers, resp.content

    except requests.ConnectionError as e:
        logger.error("VSCode proxy connection error: %s", e)
        return 502, {"Content-Type": "text/plain"}, b"Bad Gateway: code-server unreachable"
    except requests.Timeout:
        return 504, {"Content-Type": "text/plain"}, b"Gateway Timeout: code-server did not respond"
    except Exception as e:
        logger.error("VSCode proxy error: %s", e)
        return 500, {"Content-Type": "text/plain"}, f"Internal error: {e}".encode()


def proxy_request_streaming(
    method: str,
    target_url: str,
    headers: dict,
    body: bytes | None = None,
    params: dict | None = None,
) -> tuple[int, dict, Generator[bytes, None, None]]:
    """Proxy a single HTTP request with streaming response.

    Uses iter_content to stream the response back to the browser,
    which avoids loading large VSCode assets entirely into memory.

    Args:
        method: HTTP method (GET, POST, PUT, DELETE, etc.)
        target_url: Full URL of the remote code-server
        headers: Request headers from the browser
        body: Request body bytes
        params: Query parameters to pass through

    Returns:
        Tuple of (status_code, response_headers, content_generator)
    """
    filtered_headers = _prepare_request_headers(headers, target_url)

    try:
        resp = _proxy_session.request(
            method=method,
            url=target_url,
            headers=filtered_headers,
            data=body,
            params=params,
            stream=True,
            timeout=60,
            allow_redirects=False,
        )

        # Build response headers, filtering hop-by-hop
        response_headers = {}
        for k, v in resp.headers.items():
            if k.lower() not in HOP_BY_HOP_HEADERS:
                response_headers[k] = v

        def generate() -> Generator[bytes, None, None]:
            try:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        yield chunk
            finally:
                resp.close()

        return resp.status_code, response_headers, generate()

    except requests.ConnectionError as e:
        logger.error("VSCode proxy connection error: %s", e)

        def error_gen() -> Generator[bytes, None, None]:
            yield b"Bad Gateway: code-server unreachable"

        return 502, {"Content-Type": "text/plain"}, error_gen()
    except requests.Timeout:

        def timeout_gen() -> Generator[bytes, None, None]:
            yield b"Gateway Timeout: code-server did not respond"

        return 504, {"Content-Type": "text/plain"}, timeout_gen()
    except Exception as exc:
        logger.error("VSCode proxy error: %s", exc)
        err_msg = f"Internal error: {exc}".encode()

        def err_gen() -> Generator[bytes, None, None]:
            yield err_msg

        return 500, {"Content-Type": "text/plain"}, err_gen()


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
