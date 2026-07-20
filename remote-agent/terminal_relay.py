#!/usr/bin/env python3
"""
Open ACE Remote Agent - Terminal Relay Client

Establishes a WebSocket relay connection between the local terminal_server
and the backend's relay endpoint. This solves the issue where the backend
cannot directly reach remote machines on private networks.

Started by the agent as a subprocess after terminal_server is running.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import os
import ssl
import sys
import tempfile
from urllib.parse import urlparse

try:
    import websockets
except ImportError:
    print("Error: 'websockets' package is required.", file=sys.stderr)
    print("Install with: pip install 'websockets>=13.0,<17.0'", file=sys.stderr)
    sys.exit(1)

logger = logging.getLogger("openace-terminal-relay")

# Globals from CLI args
BACKEND_URL = ""
TERMINAL_ID = ""
LOCAL_WS_URL = ""
RELAY_TOKEN = ""
SKIP_SSL_VERIFY = False
CA_BUNDLE_PATH = None


def _is_localhost_url(url: str) -> bool:
    """
    判断 URL 是否为本地地址（Issue #1892）。

    涵盖：127.0.0.0/8、::1、localhost、*.localhost

    Args:
        url: 要检查的 URL

    Returns:
        True 如果是 localhost，否则 False
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname or parsed.netloc.split(":")[0]

        # IPv4/IPv6 loopback
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_loopback:
                return True
        except ValueError:
            pass

        # 域名匹配
        host_lower = host.lower()
        if host_lower == "localhost":
            return True
        if host_lower.endswith(".localhost"):
            return True

        return False
    except Exception:
        return False


def _get_ssl_context(skip_verify: bool, ca_bundle_path: str | None) -> ssl.SSLContext | None:
    """
    Build SSL context for WebSocket connections (Issue #1892).

    Args:
        skip_verify: Skip SSL certificate verification
        ca_bundle_path: Custom CA bundle file path

    Returns:
        SSL context or None to use default
    """
    if skip_verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        logger.warning(
            "[SECURITY WARNING] SSL verification disabled for backend connection"
        )
        return ctx

    if ca_bundle_path:
        ctx = ssl.create_default_context()
        try:
            ctx.load_verify_locations(ca_bundle_path)
            logger.info("Using custom CA bundle: %s", ca_bundle_path)
            return ctx
        except Exception as e:
            logger.warning(
                "Failed to load CA bundle %s: %s, falling back to system CA",
                ca_bundle_path,
                e
            )
            return None

    return None  # Use default system SSL context


async def run_relay() -> None:
    """Run the relay: connect to backend and local terminal_server, bridge them."""
    global BACKEND_URL, TERMINAL_ID, LOCAL_WS_URL, RELAY_TOKEN, SKIP_SSL_VERIFY, CA_BUNDLE_PATH

    # Build relay WebSocket URL to backend
    # Use wss:// for https:// backend URLs
    relay_url = BACKEND_URL.replace("https://", "wss://").replace("http://", "ws://")
    relay_url = f"{relay_url}/api/remote/agent/terminal-relay/{TERMINAL_ID}?token={RELAY_TOKEN}"

    logger.info("Connecting to backend relay: %s", relay_url[:80] + "...")
    logger.info("Connecting to local terminal: %s", LOCAL_WS_URL)

    # Issue #1892: Check if local WS URL is localhost
    local_is_localhost = _is_localhost_url(LOCAL_WS_URL)
    if not local_is_localhost:
        logger.warning(
            "Local WS URL %s is not localhost, this may be a security risk",
            LOCAL_WS_URL
        )

    try:
        # Issue #1892: Build SSL context for backend connection
        backend_ssl_context = _get_ssl_context(SKIP_SSL_VERIFY, CA_BUNDLE_PATH)

        # Connect to backend relay endpoint with SSL context
        backend_ws = await websockets.connect(
            relay_url,
            subprotocols=["binary"],
            close_timeout=5,
            ping_interval=20,
            ping_timeout=10,
            ssl=backend_ssl_context,
        )
        logger.info("Connected to backend relay")

        # Issue #1892: Local connection should not use SSL verification
        # (it's a loopback connection, no MITM risk)
        local_ws_url_with_token = f"{LOCAL_WS_URL}?token={RELAY_TOKEN}"
        local_ws = await websockets.connect(
            local_ws_url_with_token,
            subprotocols=["binary"],
            close_timeout=5,
            # No SSL context for local connection - it's loopback
        )
        logger.info("Connected to local terminal server")

        # Bridge bidirectionally
        async def backend_to_local():
            """Forward messages from backend (browser) to local terminal."""
            try:
                async for message in backend_ws:
                    await local_ws.send(message)
            except websockets.exceptions.ConnectionClosed as e:
                logger.info("Backend relay closed: %s", e)
            except Exception as e:
                logger.error("Backend→Local error: %s", e)
            finally:
                try:
                    await local_ws.close()
                except Exception:
                    pass

        async def local_to_backend():
            """Forward messages from local terminal to backend (browser)."""
            try:
                async for message in local_ws:
                    await backend_ws.send(message)
            except websockets.exceptions.ConnectionClosed as e:
                logger.info("Local terminal closed: %s", e)
            except Exception as e:
                logger.error("Local→Backend error: %s", e)
            finally:
                try:
                    await backend_ws.close()
                except Exception:
                    pass

        # Run both directions concurrently
        await asyncio.gather(backend_to_local(), local_to_backend())

        logger.info("Relay session ended for terminal %s", TERMINAL_ID[:8])

    except Exception as e:
        logger.error("Relay connection failed: %s", e)
        # Retry with exponential backoff
        raise


async def main_async() -> None:
    """Main async entry point with retry logic."""
    max_retries = 5
    base_delay = 1.0

    for attempt in range(max_retries):
        try:
            await run_relay()
            break  # Success, exit loop
        except Exception as e:
            if attempt < max_retries - 1:
                delay = base_delay * (2**attempt)
                logger.warning(
                    "Relay attempt %d/%d failed, retrying in %.1fs: %s",
                    attempt + 1,
                    max_retries,
                    delay,
                    e,
                )
                await asyncio.sleep(delay)
            else:
                logger.error("Relay failed after %d attempts: %s", max_retries, e)
                sys.exit(1)


def main() -> None:
    global BACKEND_URL, TERMINAL_ID, LOCAL_WS_URL, RELAY_TOKEN, SKIP_SSL_VERIFY, CA_BUNDLE_PATH

    parser = argparse.ArgumentParser(description="Open ACE Terminal Relay Client")
    parser.add_argument("--backend-url", required=True, help="Backend server URL")
    parser.add_argument("--terminal-id", required=True, help="Terminal session ID")
    parser.add_argument("--local-ws-url", required=True, help="Local terminal WebSocket URL")
    # Issue #1892: SSL configuration options (backward compatible defaults)
    parser.add_argument(
        "--skip-ssl-verify",
        action="store_true",
        default=False,
        help="Skip SSL certificate verification (insecure, not recommended for production)"
    )
    parser.add_argument(
        "--ca-bundle",
        dest="ca_bundle",
        default=None,
        help="Path to custom CA bundle file for SSL verification"
    )
    args = parser.parse_args()

    BACKEND_URL = args.backend_url
    TERMINAL_ID = args.terminal_id
    LOCAL_WS_URL = args.local_ws_url
    SKIP_SSL_VERIFY = args.skip_ssl_verify
    CA_BUNDLE_PATH = args.ca_bundle

    # Read token from environment variable (not CLI arg, to avoid ps aux exposure)
    RELAY_TOKEN = os.environ.get("OPEN_ACE_RELAY_TOKEN", "")
    if not RELAY_TOKEN:
        print("Error: OPEN_ACE_RELAY_TOKEN environment variable is required", file=sys.stderr)
        sys.exit(1)

    # Set up logging
    log_file = os.path.join(tempfile.gettempdir(), f"terminal_relay_{TERMINAL_ID[:8]}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stderr),
        ],
    )

    logger.info(
        "Terminal relay starting: backend=%s terminal=%s local=%s ssl_verify=%s",
        BACKEND_URL,
        TERMINAL_ID[:8],
        LOCAL_WS_URL,
        not SKIP_SSL_VERIFY,
    )

    # Run with asyncio
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logger.info("Relay interrupted by user")
    except Exception as e:
        logger.error("Relay crashed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()