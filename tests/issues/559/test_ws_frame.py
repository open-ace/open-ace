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

    def test_unmasked_frame(self):
        """Server may receive unmasked frames from misbehaving clients."""
        payload = b"test"
        frame = _build_client_frame(payload, opcode=OP_TEXT, mask=False)
        sock = FakeSocket(frame)
        result = ws_frame.recv_message(sock)
        assert result == "test"

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
        """Send a text message then read it back via unmasked frame."""
        payload = b"round trip test"
        frame = struct.pack("!BB", 0x81, len(payload)) + payload
        sock = FakeSocket(frame)
        result = ws_frame.recv_message(sock)
        assert result == "round trip test"

    def test_binary_round_trip(self):
        payload = bytes(range(256))
        # 256 bytes needs 2-byte extended length
        frame = struct.pack("!BBH", 0x82, 126, len(payload)) + payload
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
