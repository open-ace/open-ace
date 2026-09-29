"""
Unit tests for scripts/shared/qwen_context.py

Tests for is_qwen_system_context() which identifies Qwen CLI system-context
messages that should not be surfaced as user chat messages.

Issue #3337: Tests for strip_qwen_system_envelopes() which strips system
envelopes while preserving real user text.
"""

# Import from scripts/shared path
import sys
from pathlib import Path

import pytest

scripts_shared = Path(__file__).parent.parent.parent / "scripts" / "shared"
sys.path.insert(0, str(scripts_shared.parent))

from qwen_context import is_qwen_system_context, strip_qwen_system_envelopes


class TestSystemContextMarkers:
    """Test detection of all 4 system-context markers."""

    def test_platform_tool_limits_marker(self):
        """[Platform Tool Limits] marker should be detected."""
        content = "[Platform Tool Limits]\n- Tool: bash\n- Limits: some limits"
        assert is_qwen_system_context(content) is True

    def test_platform_tool_limits_within_longer_text(self):
        """Marker within longer text should still be detected."""
        content = "Some prefix text\n[Platform Tool Limits]\nMore text"
        assert is_qwen_system_context(content) is True

    def test_startup_context_marker(self):
        """'This is the Qwen Code. We are setting up the context' marker."""
        content = "This is the Qwen Code. We are setting up the context for our chat."
        assert is_qwen_system_context(content) is True

    def test_memory_directory_marker(self):
        """'Memory directory:' marker should be detected."""
        content = "Memory directory: /home/user/.qwen/memories"
        assert is_qwen_system_context(content) is True

    def test_managed_memory_two_directories_marker(self):
        """'Managed memory has TWO directories' marker should be detected."""
        content = "Managed memory has TWO directories for persistence."
        assert is_qwen_system_context(content) is True


class TestPhaseHeaderRegex:
    """Test Phase Header regex matching."""

    def test_phase_header_with_em_dash(self):
        """Phase header with em-dash (—) should match."""
        content = "## Phase 1 — Initial Setup\nSome instructions"
        assert is_qwen_system_context(content) is True

    def test_phase_header_with_hyphen(self):
        """Phase header with hyphen (-) should match."""
        content = "## Phase 2 - Processing\nSome instructions"
        assert is_qwen_system_context(content) is True

    def test_phase_header_various_numbers(self):
        """Different phase numbers should all match."""
        for phase_num in [1, 2, 3, 10, 99]:
            content = f"## Phase {phase_num} — Some Phase\nInstructions"
            assert is_qwen_system_context(content) is True

    def test_phase_header_case_insensitive(self):
        """Phase header matching should be case insensitive."""
        content = "## PHASE 1 — Setup\nInstructions"
        assert is_qwen_system_context(content) is True


class TestStartupReminderRegex:
    """Test startup-reminder regex matching."""

    def test_startup_reminder_with_system_tag(self):
        """Startup reminder with <system-reminder> tag should match."""
        content = "<system-reminder>\nThe current date is: 2026-08-11\nThis is the Qwen Code"
        assert is_qwen_system_context(content) is True

    def test_startup_reminder_without_qwen_sentence(self):
        """Issue #3337: Startup reminder without 'This is the Qwen Code' should now match."""
        # With the fix, date reminders no longer require the Qwen Code sentence
        content = "<system-reminder>\nThe current date is: 2026-08-11\nSome other content"
        assert is_qwen_system_context(content) is True

    def test_startup_reminder_whitespace_variations(self):
        """Startup reminder should handle whitespace variations."""
        content = "<system-reminder>   The current date is: 2026-08-11\nThis is the Qwen Code"
        assert is_qwen_system_context(content) is True


class TestEdgeCases:
    """Test edge cases and defensive handling."""

    def test_none_input(self):
        """None input should return False."""
        assert is_qwen_system_context(None) is False

    def test_empty_string(self):
        """Empty string should return False."""
        assert is_qwen_system_context("") is False

    def test_whitespace_only(self):
        """Whitespace-only string should return False."""
        assert is_qwen_system_context("   \n\t  ") is False

    def test_non_string_input_integer(self):
        """Non-string input (integer) should return False."""
        assert is_qwen_system_context(12345) is False

    def test_non_string_input_list(self):
        """Non-string input (list) should return False."""
        assert is_qwen_system_context(["some", "content"]) is False

    def test_non_string_input_dict(self):
        """Non-string input (dict) should return False."""
        assert is_qwen_system_context({"key": "value"}) is False

    def test_none_like_false_values(self):
        """False-like values that aren't strings should return False."""
        assert is_qwen_system_context(0) is False
        assert is_qwen_system_context(False) is False


class TestNormalUserMessages:
    """Test that normal user messages are NOT flagged as system context."""

    def test_simple_user_message(self):
        """Simple user message should not be flagged."""
        assert is_qwen_system_context("Hello, world!") is False

    def test_code_snippet(self):
        """Code snippet should not be flagged."""
        content = "```python\ndef hello():\n    print('world')\n```"
        assert is_qwen_system_context(content) is False

    def test_question(self):
        """Question should not be flagged."""
        assert is_qwen_system_context("Can you help me fix this bug?") is False

    def test_file_path(self):
        """File path should not be flagged."""
        assert is_qwen_system_context("Please check /home/user/file.py") is False

    def test_markdown_text(self):
        """Markdown text should not be flagged."""
        content = "# Heading\n\nSome **bold** and *italic* text."
        assert is_qwen_system_context(content) is False

    def test_mixed_case_without_markers(self):
        """Mixed case without system markers should not be flagged."""
        content = "This is NOT the Qwen Code system message"
        assert is_qwen_system_context(content) is False

    def test_partial_marker_not_matched(self):
        """Partial marker text should not match (exact substring required)."""
        # "[Platform Tool" without full marker should not match
        content = "[Platform Tool is a great feature"
        assert is_qwen_system_context(content) is False


class TestRealWorldExamples:
    """Test against real-world system context examples."""

    def test_full_platform_tool_limits_block(self):
        """Full Platform Tool Limits block should match."""
        content = """[Platform Tool Limits]

Tool: computer_use__click
Max: 50 calls per session
Remaining: 45"""
        assert is_qwen_system_context(content) is True

    def test_full_startup_context(self):
        """Full startup context should match."""
        content = """This is the Qwen Code. We are setting up the context for our chat.
Today's date is: Tuesday, August 11, 2026.
My operating system is: linux"""
        assert is_qwen_system_context(content) is True

    def test_memory_instructions(self):
        """Memory management instructions should match."""
        content = """Memory directory: /home/user/.qwen/memories

Managed memory has TWO directories for persistence.
- USER memory: cross-project knowledge
- PROJECT memory: this-project-only context"""
        assert is_qwen_system_context(content) is True


class TestStripSystemEnvelopes:
    """Issue #3337: Tests for strip_qwen_system_envelopes()."""

    def test_pure_date_reminder(self):
        """Pure date reminder should return empty string."""
        content = """<system-reminder>
The current date is: Friday, September 4, 2026.
Note: This is the authoritative current date.
</system-reminder>"""
        assert strip_qwen_system_envelopes(content) == ""

    def test_date_reminder_with_chinese_user_text(self):
        """Date reminder + Chinese user text should return only user text."""
        content = """<system-reminder>
The current date is: Friday, September 4, 2026.
Note: This is the authoritative current date.
</system-reminder>

帮我修复这个 bug"""
        assert strip_qwen_system_envelopes(content) == "帮我修复这个 bug"

    def test_date_reminder_with_english_user_text(self):
        """Date reminder + English user text should return only user text."""
        content = """<system-reminder>
The current date is: Friday, September 4, 2026.
</system-reminder>

Fix this bug for me"""
        assert strip_qwen_system_envelopes(content) == "Fix this bug for me"

    def test_multiple_envelopes_with_user_text(self):
        """Multiple date envelopes should all be stripped."""
        content = """<system-reminder>
The current date is: Friday, September 4, 2026.
</system-reminder>

<system-reminder>
The current date is: Monday, September 7, 2026.
</system-reminder>

User text here"""
        assert strip_qwen_system_envelopes(content) == "User text here"

    def test_normal_user_message_unchanged(self):
        """Normal user message should be unchanged."""
        content = "帮我修复这个 bug"
        assert strip_qwen_system_envelopes(content) == "帮我修复这个 bug"

    def test_user_quoting_reminder_tag_not_stripped(self):
        """User message containing reminder tag should not be stripped."""
        content = "我看到有个 <system-reminder> 标签，这是什么意思？"
        # This should not match the date reminder pattern, so return unchanged
        assert strip_qwen_system_envelopes(content) == content

    def test_non_date_envelope_not_stripped(self):
        """Non-date system-reminder envelope should not be stripped."""
        content = """<system-reminder>
Some other system message
</system-reminder>

User text"""
        # Not a date reminder, should return unchanged
        assert strip_qwen_system_envelopes(content) == content

    def test_empty_string(self):
        """Empty string should return empty string."""
        assert strip_qwen_system_envelopes("") == ""

    def test_whitespace_only(self):
        """Whitespace-only string should return empty string."""
        assert strip_qwen_system_envelopes("   \n\t  ") == ""

    def test_none_input(self):
        """None input should return empty string."""
        assert strip_qwen_system_envelopes(None) == ""

    def test_non_string_input(self):
        """Non-string input should return the input unchanged."""
        assert strip_qwen_system_envelopes(12345) == 12345
        assert strip_qwen_system_envelopes(["some", "list"]) == ["some", "list"]


class TestStripSystemEnvelopesEdgeCases:
    """Edge case tests for strip_qwen_system_envelopes()."""

    def test_envelope_with_extra_whitespace(self):
        """Envelope with extra whitespace should be stripped."""
        content = """<system-reminder>   The current date is: 2026-09-04   </system-reminder>

User text"""
        assert strip_qwen_system_envelopes(content) == "User text"

    def test_user_text_with_newlines_preserved(self):
        """User text with newlines should be preserved."""
        content = """<system-reminder>
The current date is: 2026-09-04
</system-reminder>

Line 1
Line 2
Line 3"""
        result = strip_qwen_system_envelopes(content)
        assert result == "Line 1\nLine 2\nLine 3"

    def test_envelope_middle_of_text_not_stripped(self):
        """Envelope in the middle of user text should NOT be stripped."""
        content = """User text before

<system-reminder>
The current date is: 2026-09-04
</system-reminder>

User text after"""
        # The stripping only happens at the START, so the envelope in the middle
        # is NOT stripped. This is by design to avoid removing user content.
        assert strip_qwen_system_envelopes(content) == content
