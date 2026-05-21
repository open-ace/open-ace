#!/usr/bin/env python3
"""
Open ACE - WebSocket Terminal Proxy

Acts as a WebSocket proxy between the browser and remote terminal servers.
The browser connects to this proxy, which then connects to the remote machine's
terminal WebSocket server and forwards messages bidirectionally.

This solves the issue where the browser cannot directly connect to remote
private IP addresses (e.g., ws://192.168.64.3:port).

Started as a subprocess by the backend when a terminal session is requested.
"""

import argparse
import asyncio
import hmac
import logging
import os
import signal
import sys
import urllib.parse

# Force default asyncio event loop policy to avoid gevent interference
# when spawned from a gevent-patched parent process
asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())

try:
    import websockets
except ImportError:
    print("Error: 'websockets' package is required.", file=sys.stderr)
    print("Install with: pip install 'websockets>=13.0,<17.0'", file=sys.stderr)
    sys.exit(1)

logger = logging.getLogger("openace-ws-proxy")

# Globals from CLI args
AUTH_TOKEN = ""
BACKEND_URL = ""
MACHINE_ID = ""
TERMINAL_ID = ""


async def get_remote_ws_info() -> tuple[str, str]:
    """Fetch remote WebSocket URL and token from backend.

    Returns:
        (original_ws_url, original_token) - the remote terminal's ws_url and token
        that the proxy needs to connect to the remote terminal server.
    """
    import requests

    url = f"{BACKEND_URL}/api/remote/terminal/{TERMINAL_ID}/status?machine_id={MACHINE_ID}"
    cookies = {"session_token": AUTH_TOKEN}

    try:
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            None, lambda: requests.get(url, cookies=cookies, timeout=10)
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("terminal"):
                term = data["terminal"]
                original_ws_url = term.get("original_ws_url", term.get("ws_url", ""))
                original_token = term.get("original_token", term.get("token", ""))
                return original_ws_url, original_token
    except Exception as e:
        logger.error("Failed to fetch remote WS info: %s", e)

    return "", ""


async def proxy_connection(browser_ws, remote_ws_url: str, remote_token: str):
    """Bidirectional proxy between browser and remote terminal."""
    try:
        # Connect to remote terminal WebSocket
        remote_ws_url_with_token = f"{remote_ws_url}?token={remote_token}"
        remote_ws = await websockets.connect(
            remote_ws_url_with_token,
            subprotocols=["binary"],
            close_timeout=5,
        )
        logger.info("Connected to remote terminal: %s", remote_ws_url)

        try:
            # Bidirectional forwarding
            async def browser_to_remote():
                """Forward messages from browser to remote."""
                try:
                    async for message in browser_ws:
                        if isinstance(message, str):
                            # JSON control message (resize, etc.)
                            await remote_ws.send(message)
                        else:
                            # Binary data (terminal input)
                            await remote_ws.send(message)
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    logger.debug("Browser→Remote error: %s", e)

            async def remote_to_browser():
                """Forward messages from remote to browser."""
                try:
                    async for message in remote_ws:
                        await browser_ws.send(message)
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    logger.debug("Remote→Browser error: %s", e)

            # Run both directions concurrently
            await asyncio.gather(browser_to_remote(), remote_to_browser())

        finally:
            await remote_ws.close()
            logger.info("Disconnected from remote terminal")

    except Exception as e:
        logger.error("Remote connection error: %s", e)
        try:
            await browser_ws.close(1011, f"Remote connection error: {e}")
        except Exception:
            pass


async def handle_browser_connection(browser_ws):
    """Handle a browser WebSocket connection."""
    # Validate auth token from query params
    # websockets >= 13 stores path in request.path; older versions used websocket.path
    raw_path = ""
    if hasattr(browser_ws, "request") and browser_ws.request is not None:
        raw_path = browser_ws.request.path
    elif hasattr(browser_ws, "path"):
        raw_path = browser_ws.path
    logger.info("Browser connection received, path: %s", raw_path)
    params = urllib.parse.parse_qs(urllib.parse.urlparse(raw_path).query)
    token = params.get("token", [None])[0]

    logger.info("Token validation: %s", "provided" if token else "missing")

    if not token or not hmac.compare_digest(token, AUTH_TOKEN):
        logger.warning("Rejected connection: invalid token")
        await browser_ws.close(4001, "Authentication failed")
        return

    logger.info("Browser connected, fetching remote WS info...")

    # Get remote WebSocket URL and token
    remote_ws_url, remote_token = await get_remote_ws_info()

    if not remote_ws_url:
        logger.error("No remote WebSocket URL available")
        await browser_ws.close(1011, "Remote terminal not available")
        return

    # Start proxy
    await proxy_connection(browser_ws, remote_ws_url, remote_token)


async def run_server(port: int):
    """Start the WebSocket proxy server."""
    async with websockets.serve(
        handle_browser_connection,
        "0.0.0.0",
        port,
        subprotocols=["binary"],
    ) as server:
        actual_port = server.sockets[0].getsockname()[1]
        logger.info("WebSocket proxy listening on ws://0.0.0.0:%d", actual_port)
        print(f"READY:{actual_port}", flush=True)
        await asyncio.Future()  # Block forever


def main():
    global AUTH_TOKEN, BACKEND_URL, MACHINE_ID, TERMINAL_ID

    parser = argparse.ArgumentParser(description="Open ACE WebSocket Terminal Proxy")
    parser.add_argument("--backend-url", required=True, help="Open ACE backend URL")
    parser.add_argument("--machine-id", required=True, help="Remote machine ID")
    parser.add_argument("--terminal-id", required=True, help="Terminal session ID")
    parser.add_argument("--port", type=int, default=0, help="Port to listen on (0=auto)")
    args = parser.parse_args()

    # Read token from environment variable (not CLI arg, to avoid ps aux exposure)
    AUTH_TOKEN = os.environ.get("OPEN_ACE_PROXY_TOKEN", "")
    BACKEND_URL = args.backend_url
    MACHINE_ID = args.machine_id
    TERMINAL_ID = args.terminal_id

    # Log to file for debugging (stdout reserved for READY signal only)
    log_file = f"/tmp/ws_proxy_{TERMINAL_ID[:8]}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file),
        ],
    )
    # Print startup message to stderr (stdout reserved for READY signal)
    print("WebSocket proxy starting", file=sys.stderr, flush=True)

    async def shutdown(sig, loop):
        logging.info(f"Received signal {sig.name}, shutting down...")
        tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        loop.stop()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.ensure_future(shutdown(s, loop)))

    try:
        loop.run_until_complete(run_server(args.port))
    finally:
        loop.close()


if __name__ == "__main__":
    main()
