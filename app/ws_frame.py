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
import struct
from base64 import b64encode

# RFC 6455 magic GUID for Sec-WebSocket-Accept computation
_WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Opcodes
OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA


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


def recv_message(sock) -> bytes | str | None:
    """Read one complete WebSocket message from *sock*.

    Supports RFC 6455 fragmentation: continuation frames are accumulated
    until the FIN bit is set.  Control frames (ping/pong/close) may be
    interleaved within a fragmented message and are handled inline.

    Returns:
        - ``bytes`` for binary messages
        - ``str`` for text messages
        - ``None`` on close frame or connection error

    Ping frames are automatically answered with pong.
    """
    fragments: list[bytes] = []
    message_opcode: int | None = None

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
        if opcode == OP_CLOSE:
            return None
        if opcode == OP_PING:
            send_pong(sock, payload)
            continue
        if opcode == OP_PONG:
            continue

        # Data frames
        if opcode in (OP_TEXT, OP_BINARY):
            message_opcode = opcode
            fragments = [payload]
        elif opcode == OP_CONT:
            if message_opcode is None:
                return None
            fragments.append(payload)
        else:
            continue

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
