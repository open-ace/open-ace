"""Unit tests for app.ws_frame — lightweight WebSocket frame protocol."""

import hashlib
import struct
from base64 import b64encode
from unittest.mock import MagicMock

import pytest

import app.ws_frame as ws_frame

# Re-export for convenience
OP_BINARY = ws_frame.OP_BINARY
OP_CLOSE = ws_frame.OP_CLOSE
OP_CONT = ws_frame.OP_CONT
OP_PING = ws_frame.OP_PING
OP_PONG = ws_frame.OP_PONG
OP_TEXT = ws_frame.OP_TEXT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WS_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _make_accept(key: str) -> str:
    """Compute expected Sec-WebSocket-Accept for a given key."""
    return b64encode(hashlib.sha1(key.encode() + _WS_GUID).digest()).decode()


class FakeSocket:
    """Minimal socket double that records sends and replays receives."""

    def __init__(self, recv_data: bytes = b""):
        self._recv_buf = bytearray(recv_data)
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, n: int) -> bytes:
        if not self._recv_buf:
            return b""
        chunk = bytes(self._recv_buf[:n])
        self._recv_buf = self._recv_buf[n:]
        return chunk

    @property
    def all_sent(self) -> bytes:
        return b"".join(self.sent)


def _mask(mask_key: bytes, payload: bytes) -> bytes:
    """Apply WebSocket mask to payload (client-to-server framing)."""
    return bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))


def _build_client_frame(
    payload: bytes,
    opcode: int = OP_TEXT,
    fin: bool = True,
    mask: bool = True,
) -> bytes:
    """Build a complete client-to-server WebSocket frame."""
    first = (0x80 if fin else 0x00) | opcode
    mask_bit = 0x80 if mask else 0x00
    length = len(payload)
    mask_key = b"\x37\xfa\x21\x3d" if mask else b""

    if length < 126:
        header = struct.pack("!BB", first, mask_bit | length)
    elif length < 65536:
        header = struct.pack("!BBH", first, mask_bit | 126, length)
    else:
        header = struct.pack("!BBQ", first, mask_bit | 127, length)

    masked_payload = _mask(mask_key, payload) if mask else payload
    return header + mask_key + masked_payload


# ---------------------------------------------------------------------------
# Tests: _compute_accept_key
# ---------------------------------------------------------------------------


class TestComputeAcceptKey:
    def test_rfc_example(self):
        """RFC 6455 Section 4.2.2 example vectors."""
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        expected = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
        assert ws_frame._compute_accept_key(key) == expected

    def test_deterministic(self):
        key = "test-key-12345"
        assert ws_frame._compute_accept_key(key) == ws_frame._compute_accept_key(key)

    def test_different_keys_differ(self):
        assert ws_frame._compute_accept_key("aaa") != ws_frame._compute_accept_key("bbb")


# ---------------------------------------------------------------------------
# Tests: perform_handshake
# ---------------------------------------------------------------------------


class TestPerformHandshake:
    def test_sends_101_response(self):
        sock = FakeSocket()
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        environ = {"HTTP_SEC_WEBSOCKET_KEY": key}
        ws_frame.perform_handshake(environ, sock)

        response = sock.all_sent.decode()
        assert response.startswith("HTTP/1.1 101 Switching Protocols\r\n")
        assert "Upgrade: websocket\r\n" in response
        assert "Connection: Upgrade\r\n" in response
        accept = _make_accept(key)
        assert f"Sec-WebSocket-Accept: {accept}\r\n" in response
        assert response.endswith("\r\n\r\n")

    def test_includes_protocol_header(self):
        sock = FakeSocket()
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        environ = {
            "HTTP_SEC_WEBSOCKET_KEY": key,
            "HTTP_SEC_WEBSOCKET_PROTOCOL": "binary",
        }
        ws_frame.perform_handshake(environ, sock)

        response = sock.all_sent.decode()
        assert "Sec-WebSocket-Protocol: binary\r\n" in response

    def test_no_protocol_header_when_not_requested(self):
        sock = FakeSocket()
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        environ = {"HTTP_SEC_WEBSOCKET_KEY": key}
        ws_frame.perform_handshake(environ, sock)

        response = sock.all_sent.decode()
        assert "Sec-WebSocket-Protocol" not in response

    def test_missing_key_raises(self):
        sock = FakeSocket()
        with pytest.raises(ValueError, match="Missing Sec-WebSocket-Key"):
            ws_frame.perform_handshake({}, sock)

    def test_empty_key_raises(self):
        sock = FakeSocket()
        with pytest.raises(ValueError):
            ws_frame.perform_handshake({"HTTP_SEC_WEBSOCKET_KEY": ""}, sock)


# ---------------------------------------------------------------------------
# Tests: _recv_exactly
# ---------------------------------------------------------------------------


class TestRecvExactly:
    def test_zero_bytes(self):
        sock = FakeSocket(b"anything")
        assert ws_frame._recv_exactly(sock, 0) == b""

    def test_reads_exact_amount(self):
        sock = FakeSocket(b"hello world")
        assert ws_frame._recv_exactly(sock, 5) == b"hello"

    def test_reads_across_multiple_recv_calls(self):
        """FakeSocket returns up to n bytes per recv; test partial reads."""
        sock = FakeSocket(b"hello")
        # Override recv to return 1 byte at a time
        sock.recv = lambda n: bytes([sock._recv_buf.pop(0)]) if sock._recv_buf else b""
        assert ws_frame._recv_exactly(sock, 5) == b"hello"

    def test_eof_returns_partial(self):
        sock = FakeSocket(b"hi")
        assert ws_frame._recv_exactly(sock, 10) == b"hi"

    def test_empty_socket(self):
        sock = FakeSocket(b"")
        assert ws_frame._recv_exactly(sock, 5) == b""


# ---------------------------------------------------------------------------
# Tests: recv_message
# ---------------------------------------------------------------------------


class TestRecvMessage:
    def test_text_frame(self):
        payload = b"hello"
        frame = _build_client_frame(payload, opcode=OP_TEXT)
        sock = FakeSocket(frame)
        result = ws_frame.recv_message(sock)
        assert result == "hello"

    def test_binary_frame(self):
        payload = b"\x00\x01\x02\xff"
        frame = _build_client_frame(payload, opcode=OP_BINARY)
        sock = FakeSocket(frame)
        result = ws_frame.recv_message(sock)
        assert result == b"\x00\x01\x02\xff"

    def test_close_frame_returns_none(self):
        frame = _build_client_frame(b"\x03\xe8", opcode=OP_CLOSE)
        sock = FakeSocket(frame)
        assert ws_frame.recv_message(sock) is None

    def test_empty_close_returns_none(self):
        frame = _build_client_frame(b"", opcode=OP_CLOSE)
        sock = FakeSocket(frame)
        assert ws_frame.recv_message(sock) is None

    def test_ping_auto_pongs(self):
        ping_payload = b"test"
        # Ping followed by a text frame
        ping_frame = _build_client_frame(ping_payload, opcode=OP_PING)
        text_frame = _build_client_frame(b"after", opcode=OP_TEXT)
        sock = FakeSocket(ping_frame + text_frame)
        result = ws_frame.recv_message(sock)
        assert result == "after"
        # Verify pong was sent
        pong_sent = sock.all_sent
        assert len(pong_sent) > 0
        # Parse pong frame: first byte should be 0x8A (fin + pong opcode)
        assert pong_sent[0] == 0x8A

    def test_pong_ignored(self):
        pong_frame = _build_client_frame(b"", opcode=OP_PONG)
        text_frame = _build_client_frame(b"data", opcode=OP_TEXT)
        sock = FakeSocket(pong_frame + text_frame)
        result = ws_frame.recv_message(sock)
        assert result == "data"

    def test_unmasked_frame_rejected(self):
        """Unmasked client frames must be rejected per RFC 6455 §5.1."""
        payload = b"test"
        frame = _build_client_frame(payload, opcode=OP_TEXT, mask=False)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="must be masked"):
            ws_frame.recv_message(sock)

    def test_medium_payload_126(self):
        """Payload length encoded as 16-bit (126-65535 bytes)."""
        payload = b"A" * 200
        frame = _build_client_frame(payload, opcode=OP_BINARY)
        sock = FakeSocket(frame)
        result = ws_frame.recv_message(sock)
        assert result == payload
        assert len(result) == 200

    def test_large_payload_65535(self):
        """Payload length at the 16-bit boundary."""
        payload = b"B" * 65535
        frame = _build_client_frame(payload, opcode=OP_BINARY)
        sock = FakeSocket(frame)
        result = ws_frame.recv_message(sock)
        assert len(result) == 65535

    def test_incomplete_header_returns_none(self):
        sock = FakeSocket(b"\x81")
        assert ws_frame.recv_message(sock) is None

    def test_incomplete_payload_returns_none(self):
        # Header says 10 bytes but only 3 available
        frame = _build_client_frame(b"abc", opcode=OP_TEXT)
        # Truncate after header + mask but before full payload
        header = frame[:6]  # 2 byte header + 4 byte mask key
        sock = FakeSocket(header + b"ab")  # only 2 of 3 payload bytes
        assert ws_frame.recv_message(sock) is None

    def test_empty_socket_returns_none(self):
        sock = FakeSocket(b"")
        assert ws_frame.recv_message(sock) is None

    def test_utf8_text(self):
        payload = "你好世界".encode()
        frame = _build_client_frame(payload, opcode=OP_TEXT)
        sock = FakeSocket(frame)
        result = ws_frame.recv_message(sock)
        assert result == "你好世界"

    def test_invalid_utf8_text_replaced(self):
        payload = b"\xff\xfe"
        frame = _build_client_frame(payload, opcode=OP_TEXT)
        sock = FakeSocket(frame)
        result = ws_frame.recv_message(sock)
        assert isinstance(result, str)  # replaced, not crashed


# ---------------------------------------------------------------------------
# Tests: send_message
# ---------------------------------------------------------------------------


class TestSendMessage:
    def test_send_text(self):
        sock = FakeSocket()
        ws_frame.send_message(sock, "hello")
        data = sock.all_sent
        # Text frame: fin=1, opcode=1 -> 0x81, length=5
        assert data[0] == 0x81
        assert data[1] == 5
        assert data[2:] == b"hello"

    def test_send_binary(self):
        sock = FakeSocket()
        ws_frame.send_message(sock, b"\x00\xff")
        data = sock.all_sent
        assert data[0] == 0x82  # binary opcode
        assert data[1] == 2
        assert data[2:] == b"\x00\xff"

    def test_send_empty_text(self):
        sock = FakeSocket()
        ws_frame.send_message(sock, "")
        data = sock.all_sent
        assert data == bytes([0x81, 0x00])

    def test_send_medium_payload(self):
        sock = FakeSocket()
        payload = "x" * 200
        ws_frame.send_message(sock, payload)
        data = sock.all_sent
        assert data[0] == 0x81
        assert data[1] == 126
        length = struct.unpack("!H", data[2:4])[0]
        assert length == 200

    def test_send_large_payload(self):
        sock = FakeSocket()
        payload = b"\xab" * 70000
        ws_frame.send_message(sock, payload)
        data = sock.all_sent
        assert data[0] == 0x82  # binary
        assert data[1] == 127
        length = struct.unpack("!Q", data[2:10])[0]
        assert length == 70000


# ---------------------------------------------------------------------------
# Tests: send_close
# ---------------------------------------------------------------------------


class TestSendClose:
    def test_normal_close(self):
        sock = FakeSocket()
        ws_frame.send_close(sock, 1000)
        data = sock.all_sent
        assert data[0] == 0x88  # close opcode
        assert data[1] == 2
        code = struct.unpack("!H", data[2:4])[0]
        assert code == 1000

    def test_close_with_reason(self):
        sock = FakeSocket()
        ws_frame.send_close(sock, 1011, "error")
        data = sock.all_sent
        assert data[0] == 0x88
        code = struct.unpack("!H", data[2:4])[0]
        assert code == 1011
        assert data[4:] == b"error"

    def test_custom_close_code(self):
        sock = FakeSocket()
        ws_frame.send_close(sock, 4001)
        data = sock.all_sent
        code = struct.unpack("!H", data[2:4])[0]
        assert code == 4001


# ---------------------------------------------------------------------------
# Tests: send_pong
# ---------------------------------------------------------------------------


class TestSendPong:
    def test_empty_pong(self):
        sock = FakeSocket()
        ws_frame.send_pong(sock)
        assert sock.all_sent == bytes([0x8A, 0x00])

    def test_pong_with_payload(self):
        sock = FakeSocket()
        ws_frame.send_pong(sock, b"ping data")
        data = sock.all_sent
        assert data[0] == 0x8A
        assert data[2:] == b"ping data"


# ---------------------------------------------------------------------------
# Tests: round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_text_round_trip(self):
        """Send a text message then read it back via masked frame."""
        payload = b"round trip test"
        frame = _build_client_frame(payload, opcode=OP_TEXT)
        sock = FakeSocket(frame)
        result = ws_frame.recv_message(sock)
        assert result == "round trip test"

    def test_binary_round_trip(self):
        payload = bytes(range(256))
        # 256 bytes needs 2-byte extended length
        frame = _build_client_frame(payload, opcode=OP_BINARY)
        sock = FakeSocket(frame)
        result = ws_frame.recv_message(sock)
        assert result == payload


# ---------------------------------------------------------------------------
# Tests: fragmentation (RFC 6455 continuation frames)
# ---------------------------------------------------------------------------


def _build_fragment_frame(payload: bytes, opcode: int, fin: bool, mask: bool = True) -> bytes:
    """Build a single frame with explicit FIN and opcode (for fragmentation tests)."""
    first = (0x80 if fin else 0x00) | opcode
    mask_bit = 0x80 if mask else 0x00
    length = len(payload)
    mask_key = b"\x37\xfa\x21\x3d" if mask else b""

    if length < 126:
        header = struct.pack("!BB", first, mask_bit | length)
    elif length < 65536:
        header = struct.pack("!BBH", first, mask_bit | 126, length)
    else:
        header = struct.pack("!BBQ", first, mask_bit | 127, length)

    masked_payload = _mask(mask_key, payload) if mask else payload
    return header + mask_key + masked_payload


class TestFragmentation:
    def test_text_two_fragments(self):
        """Text message split into two fragments."""
        f1 = _build_fragment_frame(b"hel", OP_TEXT, fin=False)
        f2 = _build_fragment_frame(b"lo", OP_CONT, fin=True)
        sock = FakeSocket(f1 + f2)
        assert ws_frame.recv_message(sock) == "hello"

    def test_binary_three_fragments(self):
        """Binary message split into three fragments."""
        f1 = _build_fragment_frame(b"\x00\x01", OP_BINARY, fin=False)
        f2 = _build_fragment_frame(b"\x02\x03", OP_CONT, fin=False)
        f3 = _build_fragment_frame(b"\x04\x05", OP_CONT, fin=True)
        sock = FakeSocket(f1 + f2 + f3)
        assert ws_frame.recv_message(sock) == b"\x00\x01\x02\x03\x04\x05"

    def test_ping_between_fragments(self):
        """Control frame (ping) interleaved within a fragmented message."""
        f1 = _build_fragment_frame(b"hel", OP_TEXT, fin=False)
        ping = _build_fragment_frame(b"check", OP_PING, fin=True)
        f2 = _build_fragment_frame(b"lo", OP_CONT, fin=True)
        sock = FakeSocket(f1 + ping + f2)
        result = ws_frame.recv_message(sock)
        assert result == "hello"
        # Verify pong was sent for the interleaved ping
        assert sock.all_sent[0] == 0x8A  # pong opcode

    def test_pong_between_fragments(self):
        """Unsolicited pong between fragments is silently ignored."""
        f1 = _build_fragment_frame(b"ab", OP_TEXT, fin=False)
        pong = _build_fragment_frame(b"", OP_PONG, fin=True)
        f2 = _build_fragment_frame(b"cd", OP_CONT, fin=True)
        sock = FakeSocket(f1 + pong + f2)
        assert ws_frame.recv_message(sock) == "abcd"

    def test_close_between_fragments_returns_none(self):
        """Close frame between fragments terminates the message."""
        f1 = _build_fragment_frame(b"ab", OP_TEXT, fin=False)
        close = _build_fragment_frame(b"\x03\xe8", OP_CLOSE, fin=True)
        sock = FakeSocket(f1 + close)
        assert ws_frame.recv_message(sock) is None

    def test_single_frame_fin_true(self):
        """Normal single-frame message (fin=True) still works."""
        frame = _build_fragment_frame(b"complete", OP_TEXT, fin=True)
        sock = FakeSocket(frame)
        assert ws_frame.recv_message(sock) == "complete"

    def test_utf8_fragmented(self):
        """UTF-8 text split across fragment boundaries."""
        full = "你好世界"
        raw = full.encode("utf-8")
        # Split in the middle of a multi-byte sequence is fine at the
        # frame level; reassembly joins bytes before decoding.
        f1 = _build_fragment_frame(raw[:6], OP_TEXT, fin=False)
        f2 = _build_fragment_frame(raw[6:], OP_CONT, fin=True)
        sock = FakeSocket(f1 + f2)
        assert ws_frame.recv_message(sock) == full

    def test_unexpected_cont_without_data_frame_returns_none(self):
        """OP_CONT received without a preceding data frame is invalid."""
        frame = _build_fragment_frame(b"stray", OP_CONT, fin=True)
        sock = FakeSocket(frame)
        assert ws_frame.recv_message(sock) is None


# ---------------------------------------------------------------------------
# Tests: RFC 6455 compliance - Finding 1 (Control frame payload limit)
# ---------------------------------------------------------------------------


class TestControlFramePayloadLimit:
    """RFC 6455 §5.5: Control frames payload must be <= 125 bytes."""

    def test_ping_payload_126_bytes_rejected(self):
        """PING with 126 bytes payload must be rejected."""
        payload = b"x" * 126
        frame = _build_client_frame(payload, opcode=OP_PING)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="exceeds 125 bytes"):
            ws_frame.recv_message(sock)

    def test_pong_payload_126_bytes_rejected(self):
        """PONG with 126 bytes payload must be rejected."""
        payload = b"x" * 126
        frame = _build_client_frame(payload, opcode=OP_PONG)
        sock = FakeSocket(frame)
        # PONG is ignored, so need a text frame after to complete reading
        text_frame = _build_client_frame(b"after", opcode=OP_TEXT)
        sock = FakeSocket(frame + text_frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="exceeds 125 bytes"):
            ws_frame.recv_message(sock)

    def test_close_payload_126_bytes_rejected(self):
        """CLOSE with 126 bytes payload must be rejected."""
        payload = b"\x03\xe8" + b"x" * 124  # 2 bytes status code + 124 bytes reason
        frame = _build_client_frame(payload, opcode=OP_CLOSE)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="exceeds 125 bytes"):
            ws_frame.recv_message(sock)

    def test_ping_payload_125_bytes_accepted(self):
        """PING with 125 bytes payload should be accepted (boundary value)."""
        payload = b"x" * 125
        ping_frame = _build_client_frame(payload, opcode=OP_PING)
        text_frame = _build_client_frame(b"after", opcode=OP_TEXT)
        sock = FakeSocket(ping_frame + text_frame)
        result = ws_frame.recv_message(sock)
        assert result == "after"


# ---------------------------------------------------------------------------
# Tests: RFC 6455 compliance - Finding 4 (Control frame FIN bit)
# ---------------------------------------------------------------------------


class TestControlFrameFinValidation:
    """RFC 6455 §5.5: Control frames MUST NOT be fragmented (FIN must be 1)."""

    def test_ping_fin_zero_rejected(self):
        """PING with FIN=0 must be rejected."""
        frame = _build_fragment_frame(b"test", OP_PING, fin=False)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="must not be fragmented"):
            ws_frame.recv_message(sock)

    def test_pong_fin_zero_rejected(self):
        """PONG with FIN=0 must be rejected."""
        frame = _build_fragment_frame(b"test", OP_PONG, fin=False)
        text_frame = _build_fragment_frame(b"after", OP_TEXT, fin=True)
        sock = FakeSocket(frame + text_frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="must not be fragmented"):
            ws_frame.recv_message(sock)

    def test_close_fin_zero_rejected(self):
        """CLOSE with FIN=0 must be rejected."""
        frame = _build_fragment_frame(b"\x03\xe8", OP_CLOSE, fin=False)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="must not be fragmented"):
            ws_frame.recv_message(sock)

    def test_ping_fin_one_accepted(self):
        """PING with FIN=1 should be accepted."""
        ping_frame = _build_fragment_frame(b"test", OP_PING, fin=True)
        text_frame = _build_fragment_frame(b"after", OP_TEXT, fin=True)
        sock = FakeSocket(ping_frame + text_frame)
        result = ws_frame.recv_message(sock)
        assert result == "after"


# ---------------------------------------------------------------------------
# Tests: RFC 6455 compliance - Finding 2 (Client frame masking)
# ---------------------------------------------------------------------------


class TestClientFrameMasking:
    """RFC 6455 §5.1/§5.2: Client frames must be masked."""

    def test_unmasked_text_frame_rejected(self):
        """Unmasked TEXT frame must be rejected."""
        frame = _build_client_frame(b"test", opcode=OP_TEXT, mask=False)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="must be masked"):
            ws_frame.recv_message(sock)

    def test_unmasked_binary_frame_rejected(self):
        """Unmasked BINARY frame must be rejected."""
        frame = _build_client_frame(b"\x00\x01", opcode=OP_BINARY, mask=False)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="must be masked"):
            ws_frame.recv_message(sock)

    def test_unmasked_cont_frame_rejected(self):
        """Unmasked CONT frame must be rejected."""
        f1 = _build_fragment_frame(b"hel", OP_TEXT, fin=False)
        f2 = _build_fragment_frame(b"lo", OP_CONT, fin=True, mask=False)
        sock = FakeSocket(f1 + f2)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="must be masked"):
            ws_frame.recv_message(sock)

    def test_unmasked_ping_frame_rejected(self):
        """Unmasked PING frame must be rejected."""
        frame = _build_client_frame(b"test", opcode=OP_PING, mask=False)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="must be masked"):
            ws_frame.recv_message(sock)


# ---------------------------------------------------------------------------
# Tests: RFC 6455 compliance - Finding 3 (Fragment reassembly boundary)
# ---------------------------------------------------------------------------


class TestFragmentReassemblyBoundary:
    """RFC 6455 §5.4: Fragment reassembly must not transiently exceed message size."""

    def test_fragment_exceeds_limit_before_append(self):
        """Total length check must happen BEFORE fragments.append."""
        # Create a fragmented message that would exceed max_message_size
        # We'll use a smaller limit for testing
        import os

        original_limit = os.environ.get("OPENACE_WS_MAX_MESSAGE_BYTES")

        try:
            # Set a small limit for testing
            os.environ["OPENACE_WS_MAX_MESSAGE_BYTES"] = "100"
            max_size = ws_frame.get_max_message_size()
            assert max_size == 100

            # First fragment: 80 bytes (within limit)
            f1 = _build_fragment_frame(b"x" * 80, OP_TEXT, fin=False)
            # Second fragment: 30 bytes (total would be 110, exceeding 100)
            f2 = _build_fragment_frame(b"y" * 30, OP_CONT, fin=True)
            sock = FakeSocket(f1 + f2)

            # Should reject before appending second fragment
            with pytest.raises(ws_frame.WebSocketMessageTooLarge):
                ws_frame.recv_message(sock)
        finally:
            # Restore original limit
            if original_limit is not None:
                os.environ["OPENACE_WS_MAX_MESSAGE_BYTES"] = original_limit
            else:
                os.environ.pop("OPENACE_WS_MAX_MESSAGE_BYTES", None)

    def test_new_data_frame_interrupts_message(self):
        """New data frame interrupting incomplete message must be rejected."""
        # Start a fragmented TEXT message
        f1 = _build_fragment_frame(b"hel", OP_TEXT, fin=False)
        # Send a new TEXT frame without completing the first message
        f2 = _build_fragment_frame(b"lo", OP_TEXT, fin=True)
        sock = FakeSocket(f1 + f2)

        with pytest.raises(ws_frame.WebSocketProtocolError, match="interrupts incomplete message"):
            ws_frame.recv_message(sock)


# ---------------------------------------------------------------------------
# Tests: RFC 6455 compliance - Finding 5 (Reserved opcodes)
# ---------------------------------------------------------------------------


class TestReservedOpcodes:
    """RFC 6455 §5.5: Reserved opcodes must be treated as protocol error."""

    def test_reserved_opcode_0x3_rejected(self):
        """Opcode 0x3 is reserved and must be rejected."""
        frame = _build_client_frame(b"test", opcode=0x3)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="Reserved opcode"):
            ws_frame.recv_message(sock)

    def test_reserved_opcode_0x7_rejected(self):
        """Opcode 0x7 is reserved and must be rejected."""
        frame = _build_client_frame(b"test", opcode=0x7)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="Reserved opcode"):
            ws_frame.recv_message(sock)

    def test_reserved_opcode_0xb_rejected(self):
        """Opcode 0xB is reserved and must be rejected."""
        frame = _build_client_frame(b"test", opcode=0xB)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="Reserved opcode"):
            ws_frame.recv_message(sock)

    def test_reserved_opcode_0xf_rejected(self):
        """Opcode 0xF is reserved and must be rejected."""
        frame = _build_client_frame(b"test", opcode=0xF)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="Reserved opcode"):
            ws_frame.recv_message(sock)


# ---------------------------------------------------------------------------
# Tests: RFC 6455 compliance - Finding 6 (CLOSE frame payload format)
# ---------------------------------------------------------------------------


class TestCloseFramePayloadFormat:
    """RFC 6455 §5.5.1: CLOSE frame payload must be empty or >= 2 bytes."""

    def test_close_payload_0_bytes_accepted(self):
        """CLOSE with 0 bytes payload is valid."""
        frame = _build_client_frame(b"", opcode=OP_CLOSE)
        sock = FakeSocket(frame)
        assert ws_frame.recv_message(sock) is None

    def test_close_payload_1_byte_rejected(self):
        """CLOSE with 1 byte payload must be rejected."""
        frame = _build_client_frame(b"x", opcode=OP_CLOSE)
        sock = FakeSocket(frame)
        with pytest.raises(ws_frame.WebSocketProtocolError, match="must be 0 or >="):
            ws_frame.recv_message(sock)

    def test_close_payload_2_bytes_accepted(self):
        """CLOSE with 2 bytes payload is valid (status code only)."""
        frame = _build_client_frame(b"\x03\xe8", opcode=OP_CLOSE)
        sock = FakeSocket(frame)
        assert ws_frame.recv_message(sock) is None

    def test_close_payload_125_bytes_accepted(self):
        """CLOSE with 125 bytes payload is valid (boundary value)."""
        # 2 bytes status code + 123 bytes reason
        payload = b"\x03\xe8" + b"x" * 123
        frame = _build_client_frame(payload, opcode=OP_CLOSE)
        sock = FakeSocket(frame)
        assert ws_frame.recv_message(sock) is None


# ---------------------------------------------------------------------------
# Tests: Exception classes
# ---------------------------------------------------------------------------


class TestExceptionClasses:
    """Test that custom exception classes are properly defined."""

    def test_websocket_message_too_large_is_value_error(self):
        """WebSocketMessageTooLarge should inherit from ValueError."""
        assert issubclass(ws_frame.WebSocketMessageTooLarge, ValueError)

    def test_websocket_protocol_error_is_value_error(self):
        """WebSocketProtocolError should inherit from ValueError."""
        assert issubclass(ws_frame.WebSocketProtocolError, ValueError)

    def test_websocket_message_too_large_instantiation(self):
        """WebSocketMessageTooLarge can be instantiated with message."""
        exc = ws_frame.WebSocketMessageTooLarge("test message")
        assert str(exc) == "test message"

    def test_websocket_protocol_error_instantiation(self):
        """WebSocketProtocolError can be instantiated with message."""
        exc = ws_frame.WebSocketProtocolError("test message")
        assert str(exc) == "test message"
