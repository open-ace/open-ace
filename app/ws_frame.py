"""Lightweight RFC 6455 WebSocket server-side frame protocol.

Minimal implementation for terminal WebSocket: supports binary, text,
close, and ping/pong frames, including fragmentation reassembly.
No compression, no extension negotiation.

All I/O uses raw ``socket.recv()`` / ``socket.sendall()`` — never the
gevent ``rfile`` wrapper — to avoid the compatibility issue between
geventwebsocket 0.10.1 and newer gevent versions.
"""

from __future__ import annotations




import hashlib
import logging
import os
import struct
from base64 import b64encode

# RFC 6455 magic GUID for Sec-WebSocket-Accept computation
_WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Cap inbound WebSocket messages to keep a malformed or malicious client from
# forcing unbounded allocation in the raw frame bridge.
DEFAULT_MAX_MESSAGE_SIZE = 8 * 1024 * 1024

# RFC 6455 §5.5: Control frames payload limit
MAX_CONTROL_FRAME_PAYLOAD = 125

# Minimum CLOSE frame payload size (status code)
MIN_CLOSE_PAYLOAD_SIZE = 2

# Opcodes
OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

# All valid opcodes for validation
_VALID_OPCODES = (OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG)

logger = logging.getLogger(__name__)


class WebSocketMessageTooLarge(ValueError):
    """Raised when an inbound WebSocket message exceeds the configured cap."""


class WebSocketProtocolError(ValueError):
    """Raised when a WebSocket frame violates RFC 6455 protocol rules."""


def get_max_message_size() -> int:
    """Get the inbound WebSocket message cap in bytes."""
    raw_value = os.environ.get("OPENACE_WS_MAX_MESSAGE_BYTES", "").strip()
    if not raw_value:
        return DEFAULT_MAX_MESSAGE_SIZE

    try:
        value = int(raw_value)
    except ValueError:
        return DEFAULT_MAX_MESSAGE_SIZE

    return value if value > 0 else DEFAULT_MAX_MESSAGE_SIZE


def _compute_accept_key(client_key: str) -> str:
    raw = hashlib.sha1(client_key.encode() + _WS_GUID).digest()
    return b64encode(raw).decode()


def perform_handshake(environ: dict, sock) -> None:
    """Perform the WebSocket server-side handshake.

    Reads Sec-WebSocket-Key from *environ*, computes the accept key,
    and writes the full HTTP 101 response directly to *sock.sendall()*.

    Raises ``ValueError`` if required headers are missing.
    """
    key = environ.get("HTTP_SEC_WEBSOCKET_KEY", "")
    if not key:
        raise ValueError("Missing Sec-WebSocket-Key")

    accept = _compute_accept_key(key)
    headers = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
    )
    protocol = environ.get("HTTP_SEC_WEBSOCKET_PROTOCOL", "")
    if protocol:
        headers += f"Sec-WebSocket-Protocol: {protocol}\r\n"
    headers += "\r\n"
    sock.sendall(headers.encode())


def _recv_exactly(sock, n: int) -> bytes:
    """Read exactly *n* bytes from *sock*."""
    if n == 0:
        return b""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return bytes(buf)  # EOF / connection closed
        buf.extend(chunk)
    return bytes(buf)


def _safe_send_close(sock, code: int, reason: str) -> None:
    """Send close frame with exception safety.

    Attempts to send a close frame, logging any errors but not raising.
    This ensures cleanup logic can continue even if the socket is already broken.
    """
    try:
        send_close(sock, code, reason)
    except Exception:
        logger.debug("Failed to send close frame", exc_info=True)


def recv_message(sock) -> bytes | str | None:
    """Read one complete WebSocket message from *sock*.

    Supports RFC 6455 fragmentation: continuation frames are accumulated
    until the FIN bit is set.  Control frames (ping/pong/close) may be
    interleaved within a fragmented message and are handled inline.

    Returns:
        - ``bytes`` for binary messages
        - ``str`` for text messages
        - ``None`` on close frame or connection error

    Raises:
        - ``WebSocketProtocolError`` on RFC 6455 protocol violations
        - ``WebSocketMessageTooLarge`` when message exceeds size limit

    Ping frames are automatically answered with pong.

    State Machine (message_opcode):
      - None: Initial state or message complete
      - OP_TEXT/OP_BINARY: Message in progress

    Transitions:
      1. None + TEXT/BINARY -> message_opcode = opcode
      2. message_opcode + CONT -> accumulate
      3. message_opcode + FIN -> return message, message_opcode = None
      4. message_opcode + TEXT/BINARY -> PROTOCOL ERROR
      5. message_opcode + CLOSE/PING/PONG -> handle control frame (allowed)

    Control frames can appear at any time and do not affect message_opcode.
    """
    fragments: list[bytes] = []
    message_opcode: int | None = None
    total_length = 0
    max_message_size = get_max_message_size()

    while True:
        header = _recv_exactly(sock, 2)
        if len(header) < 2:
            return None

        first, second = header[0], header[1]
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F

        if length == 126:
            ext = _recv_exactly(sock, 2)
            if len(ext) < 2:
                return None
            length = struct.unpack("!H", ext)[0]
        elif length == 127:
            ext = _recv_exactly(sock, 8)
            if len(ext) < 8:
                return None
            length = struct.unpack("!Q", ext)[0]

        # RFC 6455 §5.5: Control frames payload must be <= 125 bytes
        if opcode in (OP_CLOSE, OP_PING, OP_PONG):
            if length > MAX_CONTROL_FRAME_PAYLOAD:
                _safe_send_close(sock, 1002, "Control frame payload exceeds 125 bytes")
                raise WebSocketProtocolError(
                    f"Control frame payload {length} exceeds {MAX_CONTROL_FRAME_PAYLOAD} bytes"
                )

        # Check frame length before reading payload to prevent DoS
        if length > max_message_size:
            _safe_send_close(sock, 1009, "Message too large")
            raise WebSocketMessageTooLarge(
                f"WebSocket frame length {length} exceeds limit {max_message_size}"
            )

        # RFC 6455 §5.1/§5.2: Client frames must be masked
        if not masked:
            _safe_send_close(sock, 1002, "Client frame must be masked")
            raise WebSocketProtocolError("Client frame must be masked")

        mask_key = _recv_exactly(sock, 4) if masked else b""
        if masked and len(mask_key) < 4:
            return None

        payload = _recv_exactly(sock, length) if length else b""
        if len(payload) < length:
            return None

        # Unmask
        if masked and mask_key:
            payload = bytearray(payload)
            for i in range(len(payload)):
                payload[i] ^= mask_key[i % 4]
            payload = bytes(payload)

        # Control frames can appear between fragments
        # RFC 6455 §5.5: Control frames MUST NOT be fragmented (FIN must be 1)
        if opcode == OP_CLOSE:
            # RFC 6455 §5.5.1: CLOSE payload must be empty or >= 2 bytes
            if 0 < len(payload) < MIN_CLOSE_PAYLOAD_SIZE:
                _safe_send_close(
                    sock, 1002, "Close frame payload must be empty or at least 2 bytes"
                )
                raise WebSocketProtocolError(
                    f"Close frame payload {len(payload)} bytes, must be 0 or >= {MIN_CLOSE_PAYLOAD_SIZE}"
                )
            # RFC 6455 §5.5: Control frames must have FIN=1
            if not fin:
                _safe_send_close(sock, 1002, "Control frame must not be fragmented")
                raise WebSocketProtocolError("Close frame must not be fragmented")
            return None
        if opcode == OP_PING:
            # RFC 6455 §5.5: Control frames must have FIN=1
            if not fin:
                _safe_send_close(sock, 1002, "Control frame must not be fragmented")
                raise WebSocketProtocolError("Ping frame must not be fragmented")
            send_pong(sock, payload)
            continue
        if opcode == OP_PONG:
            # RFC 6455 §5.5: Control frames must have FIN=1
            if not fin:
                _safe_send_close(sock, 1002, "Control frame must not be fragmented")
                raise WebSocketProtocolError("Pong frame must not be fragmented")
            continue

        # Data frames
        if opcode in (OP_TEXT, OP_BINARY):
            # RFC 6455 §5.4: New data frame must not interrupt incomplete message
            if message_opcode is not None:
                _safe_send_close(sock, 1002, "Message interrupted by new data frame")
                raise WebSocketProtocolError(
                    f"New data frame (opcode {opcode}) interrupts incomplete message"
                )
            message_opcode = opcode
            fragments = [payload]
            total_length = len(payload)
        elif opcode == OP_CONT:
            if message_opcode is None:
                return None
            # Check total length BEFORE append to prevent transient 2x allocation
            expected_total = total_length + len(payload)
            if expected_total > max_message_size:
                _safe_send_close(sock, 1009, "Message too large")
                raise WebSocketMessageTooLarge(
                    f"WebSocket message length {expected_total} exceeds limit {max_message_size}"
                )
            fragments.append(payload)
            total_length = expected_total
        else:
            # RFC 6455 §5.5: Reserved opcodes must be treated as protocol error
            _safe_send_close(sock, 1002, "Reserved opcode not allowed")
            raise WebSocketProtocolError(f"Reserved opcode {opcode} not allowed")

        if fin:
            combined = b"".join(fragments)
            if message_opcode == OP_TEXT:
                return combined.decode("utf-8", errors="replace")
            return combined


def send_message(sock, data: bytes | str) -> None:
    """Send one WebSocket message (text or binary) to *sock*."""
    if isinstance(data, str):
        opcode = OP_TEXT
        payload = data.encode("utf-8")
    else:
        opcode = OP_BINARY
        payload = bytes(data)

    _send_frame(sock, payload, opcode)


def send_close(sock, code: int = 1000, reason: str = "") -> None:
    """Send a WebSocket close frame."""
    payload = struct.pack("!H", code) + reason.encode("utf-8")
    _send_frame(sock, payload, OP_CLOSE)


def send_ping(sock, payload: bytes = b"") -> None:
    """Send a WebSocket ping frame."""
    _send_frame(sock, payload, OP_PING)


def send_pong(sock, payload: bytes = b"") -> None:
    """Send a WebSocket pong frame."""
    _send_frame(sock, payload, OP_PONG)


def _send_frame(sock, payload: bytes, opcode: int, fin: bool = True) -> None:
    """Construct and send one WebSocket frame (server-to-client, no mask)."""
    first = (0x80 if fin else 0x00) | opcode
    length = len(payload)

    if length < 126:
        header = struct.pack("!BB", first, length)
    elif length < 65536:
        header = struct.pack("!BBH", first, 126, length)
    else:
        header = struct.pack("!BBQ", first, 127, length)

    sock.sendall(header + payload)
