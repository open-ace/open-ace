"""
Open ACE - Usage Fixtures

Contract test fixtures for LLM usage parsing.

Issue #2184: Covers real protocol shapes, partial events, malformed events,
duplicate events, and missing usage responses.
"""

from __future__ import annotations

# ============================================================================
# OpenAI Chat Completions
# ============================================================================

# Regular response without cache
OPENAI_CHAT_REGULAR = {
    "id": "chatcmpl-123",
    "object": "chat.completion",
    "model": "gpt-4",
    "choices": [{"message": {"role": "assistant", "content": "Hello"}}],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150
    }
}

# Regular response with cache tokens
OPENAI_CHAT_REGULAR_WITH_CACHE = {
    "id": "chatcmpl-456",
    "object": "chat.completion",
    "model": "gpt-4",
    "choices": [{"message": {"role": "assistant", "content": "Hello"}}],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "prompt_tokens_details": {
            "cached_tokens": 20
        }
    }
}

# Streaming response - single usage chunk
OPENAI_CHAT_STREAM_CHUNKS = [
    b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n',
    b'data: {"choices": [{"delta": {"content": " world"}}]}\n\n',
    b'data: {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}\n\n',
    b'data: [DONE]\n\n',
]

# Streaming response with cache token
OPENAI_CHAT_STREAM_WITH_CACHE = [
    b'data: {"choices": [{"delta": {"content": "Hi"}}]}\n\n',
    b'data: {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 20}}}\n\n',
    b'data: [DONE]\n\n',
]

# Streaming response - multiple usage chunks (should take final)
OPENAI_CHAT_STREAM_MULTI_USAGE = [
    b'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}\n\n',
    b'data: {"usage": {"prompt_tokens": 50, "completion_tokens": 25}}\n\n',
    b'data: {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}\n\n',  # Final
    b'data: [DONE]\n\n',
]

# ============================================================================
# Anthropic Messages API
# ============================================================================

# Regular response with cache tokens
ANTHROPIC_MESSAGES_REGULAR = {
    "id": "msg-123",
    "type": "message",
    "model": "claude-3-opus",
    "content": [{"type": "text", "text": "Hello"}],
    "usage": {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 20,
        "cache_creation_input_tokens": 10
    }
}

# Regular response without cache
ANTHROPIC_MESSAGES_REGULAR_NO_CACHE = {
    "id": "msg-456",
    "type": "message",
    "model": "claude-3-sonnet",
    "content": [{"type": "text", "text": "Hello"}],
    "usage": {
        "input_tokens": 80,
        "output_tokens": 40
    }
}

# Streaming response - complete event sequence
ANTHROPIC_MESSAGES_STREAM_EVENTS = [
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg-123","model":"claude-3-opus","usage":{"input_tokens":100,"output_tokens":0,"cache_read_input_tokens":20,"cache_creation_input_tokens":10}}}\n\n',
    b'event: content_block_start\ndata: {"type":"content_block_start","index":0}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello"}}\n\n',
    b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" world"}}\n\n',
    b'event: message_delta\ndata: {"type":"message_delta","delta":{},"usage":{"output_tokens":30}}\n\n',
    b'event: message_delta\ndata: {"type":"message_delta","delta":{},"usage":{"output_tokens":20}}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
]

# Streaming response - only message_start (no message_delta)
ANTHROPIC_MESSAGES_STREAM_NO_DELTA = [
    b'event: message_start\ndata: {"type":"message_start","message":{"id":"msg-456","usage":{"input_tokens":100,"output_tokens":0}}}\n\n',
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
]

# Streaming response - interrupted (no message_stop)
ANTHROPIC_MESSAGES_STREAM_INTERRUPTED = [
    b'event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":100,"output_tokens":0}}}\n\n',
    b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":30}}\n\n',
    # Interrupted - no message_stop
]

# ============================================================================
# OpenAI Responses API
# ============================================================================

OPENAI_RESPONSES_API = {
    "id": "resp-123",
    "object": "response",
    "model": "gpt-4",
    "output": [{"type": "message", "content": "Hello"}],
    "usage": {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150
    }
}

# ============================================================================
# Gateway Converted (Anthropic to OpenAI)
# ============================================================================

GATEWAY_CONVERTED_RESPONSE = {
    "id": "chatcmpl-converted",
    "object": "chat.completion",
    "model": "claude-3-opus",
    "choices": [{"message": {"role": "assistant", "content": "Hello"}}],
    "usage": {
        "prompt_tokens": 100,  # Converted from input_tokens
        "completion_tokens": 50,  # Converted from output_tokens
        "total_tokens": 150,
        "prompt_tokens_details": {
            "cached_tokens": 20  # Converted from cache_read_input_tokens
        }
    }
}

# ============================================================================
# Malformed Events
# ============================================================================

# Usage field missing
MALFORMED_USAGE_MISSING_FIELD = {
    "id": "chatcmpl-err-1",
    "choices": [{"message": {"role": "assistant", "content": "Hello"}}]
}

# Usage field empty object
MALFORMED_USAGE_EMPTY = {
    "id": "chatcmpl-err-2",
    "usage": {}
}

# Usage with negative tokens
MALFORMED_USAGE_NEGATIVE = {
    "id": "chatcmpl-err-3",
    "usage": {
        "prompt_tokens": -10,
        "completion_tokens": -5
    }
}

# Usage with string values (should attempt conversion)
MALFORMED_USAGE_STRING_VALUES = {
    "id": "chatcmpl-err-4",
    "usage": {
        "prompt_tokens": "100",
        "completion_tokens": "50"
    }
}

# Usage with cache negative
MALFORMED_USAGE_CACHE_NEGATIVE = {
    "id": "msg-err-1",
    "usage": {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": -20
    }
}

# ============================================================================
# Boundary Cases
# ============================================================================

# Zero tokens (valid but meaningless)
USAGE_ZERO_TOKENS = {
    "id": "chatcmpl-zero",
    "usage": {
        "prompt_tokens": 0,
        "completion_tokens": 0
    }
}

# Large tokens (1 million level)
USAGE_LARGE_TOKENS = {
    "id": "chatcmpl-large",
    "usage": {
        "prompt_tokens": 1000000,
        "completion_tokens": 500000
    }
}

# Extremely large tokens (over limit, should be clamped)
USAGE_EXCEEDS_LIMIT = {
    "id": "chatcmpl-exceeds",
    "usage": {
        "prompt_tokens": 2000000000,  # 2 billion - over limit
        "completion_tokens": 1000000000  # 1 billion - at limit
    }
}

# ============================================================================
# Response Headers
# ============================================================================

OPENAI_RESPONSE_HEADERS = {
    "x-request-id": "req-abc123",
    "openai-organization": "org-xyz"
}

ANTHROPIC_RESPONSE_HEADERS = {
    "request-id": "req-def456"
}

# ============================================================================
# SSE Content Helpers
# ============================================================================

def make_openai_stream_content(chunks: list[bytes]) -> bytes:
    """Combine OpenAI SSE chunks into full content."""
    return b"".join(chunks)


def make_anthropic_stream_content(events: list[bytes]) -> bytes:
    """Combine Anthropic SSE events into full content."""
    return b"".join(events)


def make_openai_regular_content(data: dict) -> bytes:
    """Convert dict to JSON bytes for OpenAI regular response."""
    import json
    return json.dumps(data).encode('utf-8')


def make_anthropic_regular_content(data: dict) -> bytes:
    """Convert dict to JSON bytes for Anthropic regular response."""
    import json
    return json.dumps(data).encode('utf-8')