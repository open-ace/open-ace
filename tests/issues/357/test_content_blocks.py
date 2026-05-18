"""
Test for Issue #357: extract_content_blocks_from_entry function

Tests the structured content extraction from Qwen JSONL entries.
"""

import sys
from pathlib import Path

# Add scripts directory to path
scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir.parent))

from scripts.fetch_qwen import extract_content_blocks_from_entry


def test_empty_entry():
    """Test with empty or invalid entry."""
    # Empty dict
    result = extract_content_blocks_from_entry({})
    assert result == [], f"Expected empty list for empty entry, got {result}"

    # No type field
    result = extract_content_blocks_from_entry({"uuid": "abc123"})
    assert result == [], f"Expected empty list for no type, got {result}"

    # Invalid type
    result = extract_content_blocks_from_entry({"type": "unknown"})
    assert result == [], f"Expected empty list for unknown type, got {result}"

    print("✓ test_empty_entry passed")


def test_user_message_text():
    """Test user message with text parts."""
    entry = {
        "type": "user",
        "uuid": "user-uuid-001",
        "message": {
            "parts": [
                {"text": "Hello, how are you?"}
            ]
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 1, f"Expected 1 block, got {len(result)}"
    assert result[0]["type"] == "text"
    assert result[0]["text"] == "Hello, how are you?"

    print("✓ test_user_message_text passed")


def test_user_message_multiple_parts():
    """Test user message with multiple parts (text + image)."""
    entry = {
        "type": "user",
        "uuid": "user-uuid-002",
        "message": {
            "parts": [
                {"text": "Check this image:"},
                {"type": "image", "url": "http://example.com/img.png"}
            ]
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 2, f"Expected 2 blocks, got {len(result)}"
    assert result[0]["type"] == "text"
    assert result[0]["text"] == "Check this image:"
    assert result[1]["type"] == "text"
    assert result[1]["text"] == "[Image content]"

    print("✓ test_user_message_multiple_parts passed")


def test_assistant_message_text_only():
    """Test assistant message with only text."""
    entry = {
        "type": "assistant",
        "uuid": "assistant-uuid-001",
        "message": {
            "parts": [
                {"text": "I can help you with that."}
            ]
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 1
    assert result[0]["type"] == "text"
    assert result[0]["text"] == "I can help you with that."

    print("✓ test_assistant_message_text_only passed")


def test_assistant_message_with_thinking():
    """Test assistant message with thinking block."""
    entry = {
        "type": "assistant",
        "uuid": "assistant-uuid-002",
        "message": {
            "parts": [
                {"thought": True, "text": "Let me think about this..."},
                {"text": "Here's my answer."}
            ]
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 2, f"Expected 2 blocks, got {len(result)}"
    assert result[0]["type"] == "thinking"
    assert result[0]["thinking"] == "Let me think about this..."
    assert result[1]["type"] == "text"
    assert result[1]["text"] == "Here's my answer."

    print("✓ test_assistant_message_with_thinking passed")


def test_assistant_message_with_function_call():
    """Test assistant message with functionCall (tool_use)."""
    entry = {
        "type": "assistant",
        "uuid": "assistant-uuid-003",
        "message": {
            "parts": [
                {"text": "Let me check the file."},
                {"functionCall": {"name": "read_file", "args": {"path": "/tmp/test.py"}}}
            ]
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 2, f"Expected 2 blocks, got {len(result)}"
    assert result[0]["type"] == "text"
    assert result[1]["type"] == "tool_use"
    assert result[1]["name"] == "read_file"
    assert result[1]["input"]["path"] == "/tmp/test.py"
    assert result[1]["id"] == "assistant-uuid-003-1"  # uuid + idx (functionCall is at idx=1)

    print("✓ test_assistant_message_with_function_call passed")


def test_assistant_message_multiple_function_calls():
    """Test assistant message with multiple functionCalls."""
    entry = {
        "type": "assistant",
        "uuid": "assistant-uuid-004",
        "message": {
            "parts": [
                {"functionCall": {"name": "read_file", "args": {"path": "a.py"}}},
                {"functionCall": {"name": "write_file", "args": {"path": "b.py", "content": "test"}}}
            ]
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 2
    assert result[0]["type"] == "tool_use"
    assert result[0]["name"] == "read_file"
    assert result[0]["id"] == "assistant-uuid-004-0"
    assert result[1]["type"] == "tool_use"
    assert result[1]["name"] == "write_file"
    assert result[1]["id"] == "assistant-uuid-004-1"

    print("✓ test_assistant_message_multiple_function_calls passed")


def test_tool_result_message_with_correct_id_linking():
    """Test tool result message with correct ID linking using function_call_indices."""
    # Scenario: assistant message has functionCall at idx=1
    # tool_result should link to the correct tool_use.id
    entry = {
        "type": "tool_result",
        "uuid": "tool-result-001",
        "parentUuid": "assistant-uuid-003",  # Links to the assistant message
        "message": {
            "parts": [
                {"type": "tool", "name": "read_file", "content": "File contents here..."}
            ]
        }
    }
    # function_call_indices tells us that functionCall is at idx=1 in parent assistant
    function_call_indices = {"tool-result-001": 1}
    result = extract_content_blocks_from_entry(entry, function_call_indices)
    assert len(result) == 1, f"Expected 1 block, got {len(result)}"
    assert result[0]["type"] == "tool_result"
    # Now tool_use_id correctly matches the tool_use.id = "assistant-uuid-003-1"
    assert result[0]["tool_use_id"] == "assistant-uuid-003-1"
    assert result[0]["content"] == "File contents here..."

    print("✓ test_tool_result_message_with_correct_id_linking passed")


def test_tool_result_message_without_function_call_indices():
    """Test tool result message fallback when function_call_indices is not provided."""
    entry = {
        "type": "tool_result",
        "uuid": "tool-result-001",
        "parentUuid": "assistant-uuid-003",
        "message": {
            "parts": [
                {"type": "tool", "name": "read_file", "content": "File contents here..."}
            ]
        }
    }
    # Without function_call_indices, uses fallback (may not match exactly)
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 1
    assert result[0]["type"] == "tool_result"
    assert result[0]["tool_use_id"] == "assistant-uuid-003-0"  # Fallback format

    print("✓ test_tool_result_message_without_function_call_indices passed")


def test_tool_result_with_dict_content():
    """Test tool result with dict content (should be JSON stringified)."""
    entry = {
        "type": "tool_result",
        "uuid": "tool-result-002",
        "parentUuid": "assistant-uuid-005",
        "message": {
            "parts": [
                {"type": "tool", "name": "list_files", "content": {"files": ["a.py", "b.py"]}}
            ]
        }
    }
    # functionCall is at idx=0 in parent (simplified scenario)
    function_call_indices = {"tool-result-002": 0}
    result = extract_content_blocks_from_entry(entry, function_call_indices)
    assert len(result) == 1
    assert result[0]["type"] == "tool_result"
    assert result[0]["tool_use_id"] == "assistant-uuid-005-0"
    assert '{"files": ["a.py", "b.py"]}' in result[0]["content"]

    print("✓ test_tool_result_with_dict_content passed")


def test_empty_text_skipped():
    """Test that empty text blocks are skipped."""
    entry = {
        "type": "assistant",
        "uuid": "assistant-uuid-006",
        "message": {
            "parts": [
                {"text": ""},  # Empty text - should be skipped
                {"text": "Valid text"}
            ]
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 1, f"Expected 1 block (empty skipped), got {len(result)}"
    assert result[0]["text"] == "Valid text"

    print("✓ test_empty_text_skipped passed")


def test_empty_thinking_skipped():
    """Test that empty thinking blocks are skipped."""
    entry = {
        "type": "assistant",
        "uuid": "assistant-uuid-007",
        "message": {
            "parts": [
                {"thought": True, "text": ""},  # Empty thinking - should be skipped
                {"text": "Result"}
            ]
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 1
    assert result[0]["type"] == "text"

    print("✓ test_empty_thinking_skipped passed")


def test_no_message_field():
    """Test entry without message field."""
    entry = {
        "type": "assistant",
        "uuid": "assistant-uuid-008"
    }
    result = extract_content_blocks_from_entry(entry)
    assert result == []

    print("✓ test_no_message_field passed")


def test_no_parts_field():
    """Test message without parts field."""
    entry = {
        "type": "assistant",
        "uuid": "assistant-uuid-009",
        "message": {}
    }
    result = extract_content_blocks_from_entry(entry)
    assert result == []

    print("✓ test_no_parts_field passed")


def test_non_dict_parts():
    """Test message with non-dict parts."""
    entry = {
        "type": "assistant",
        "uuid": "assistant-uuid-010",
        "message": {
            "parts": ["string part", 123, None]  # Non-dict elements
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert result == [], f"Expected empty list for non-dict parts, got {result}"

    print("✓ test_non_dict_parts passed")


def test_document_placeholder():
    """Test document placeholder."""
    entry = {
        "type": "user",
        "uuid": "user-uuid-003",
        "message": {
            "parts": [
                {"type": "document", "name": "report.pdf"}
            ]
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 1
    assert result[0]["type"] == "text"
    assert result[0]["text"] == "[Document content]"

    print("✓ test_document_placeholder passed")


def test_missing_uuid_fallback():
    """Test fallback when uuid is missing."""
    entry = {
        "type": "assistant",
        "message": {
            "parts": [
                {"functionCall": {"name": "test_tool", "args": {}}}
            ]
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 1
    assert result[0]["type"] == "tool_use"
    assert result[0]["id"] == "unknown-0"  # Fallback uuid

    print("✓ test_missing_uuid_fallback passed")


def test_missing_parent_uuid_fallback():
    """Test fallback when parentUuid is missing for tool_result."""
    entry = {
        "type": "tool_result",
        "uuid": "tool-result-003",
        "message": {
            "parts": [
                {"type": "tool", "name": "test", "content": "result"}
            ]
        }
    }
    result = extract_content_blocks_from_entry(entry)
    assert len(result) == 1
    assert result[0]["type"] == "tool_result"
    assert result[0]["tool_use_id"] == "unknown-0"  # Fallback: {unknown}-{idx}

    print("✓ test_missing_parent_uuid_fallback passed")


def run_all_tests():
    """Run all tests."""
    print("\n=== Testing extract_content_blocks_from_entry ===\n")

    test_empty_entry()
    test_user_message_text()
    test_user_message_multiple_parts()
    test_assistant_message_text_only()
    test_assistant_message_with_thinking()
    test_assistant_message_with_function_call()
    test_assistant_message_multiple_function_calls()
    test_tool_result_message_with_correct_id_linking()
    test_tool_result_message_without_function_call_indices()
    test_tool_result_with_dict_content()
    test_empty_text_skipped()
    test_empty_thinking_skipped()
    test_no_message_field()
    test_no_parts_field()
    test_non_dict_parts()
    test_document_placeholder()
    test_missing_uuid_fallback()
    test_missing_parent_uuid_fallback()

    print("\n=== All tests passed! ===\n")


if __name__ == "__main__":
    run_all_tests()