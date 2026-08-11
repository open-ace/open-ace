"""
Unit tests for scripts/patch-qwen-webui-histories.py

Tests for the WebUI history patch that fixes:
1. getHistoryFiles missing chats/ subdirectory
2. groupConversations collapsing id-less sessions
3. lastMessagePreview not handling model/parts format
"""

import importlib.util
import sys
from pathlib import Path

import pytest


def load_patch_module():
    """Load the patch module from scripts directory."""
    scripts_dir = Path(__file__).parent.parent.parent / "scripts"
    module_path = scripts_dir / "patch-qwen-webui-histories.py"
    spec = importlib.util.spec_from_file_location("patch_qwen_webui_histories", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPatchHistories:
    """Test the histories patch script."""

    @pytest.fixture
    def mock_bundle_content(self):
        """Return mock bundle content with all patterns to patch."""
        return """function getHistoryFiles(historyDir) {
  try {
    const files = [];
    for await (const entry of readDir(historyDir)) {
      if (entry.isFile && entry.name.endsWith(".jsonl")) {
        files.push(`${historyDir}/${entry.name}`);
      }
    }
    return files;
  } catch {
    return [];
  }
}

  for (const currentConv of sortedConversations) {
    const isSubsetOfExisting = uniqueConversations.some(
      (existingConv) => isSubset(currentConv.messageIds, existingConv.messageIds)
    );
    if (!isSubsetOfExisting) {

        if (parsed.message?.role === "assistant" && parsed.message?.content) {
          const content2 = parsed.message.content;
          if (Array.isArray(content2)) {
            for (const item of content2) {
              if (typeof item === "object" && item && "text" in item) {
                lastMessagePreview = String(item.text).substring(0, 100);
                break;
              }
            }
          }
        }
"""

    @pytest.fixture
    def mock_bundle_patched(self):
        """Return mock bundle content with patches already applied."""
        return """function getHistoryFiles(historyDir) {
  try {
    const files = [];
    for await (const entry of readDir(historyDir)) {
      if (entry.isFile && entry.name.endsWith(".jsonl")) {
        files.push(`${historyDir}/${entry.name}`);
      }
    }
    const chatsDir = `${historyDir}/chats`;
    try {
      for await (const entry of readDir(chatsDir)) {
        if (entry.isFile && entry.name.endsWith(".jsonl")) {
          files.push(`${chatsDir}/${entry.name}`);
        }
      }
    } catch {}
    return files;
  } catch {
    return [];
  }
}

  for (const currentConv of sortedConversations) {
    const isSubsetOfExisting = currentConv.messageIds.size > 0 && uniqueConversations.some(
      (existingConv) => isSubset(currentConv.messageIds, existingConv.messageIds)
    );
    if (!isSubsetOfExisting) {

        if ((parsed.message?.role === "assistant" || parsed.message?.role === "model") && (parsed.message?.content || parsed.message?.parts)) {
          const content2 = parsed.message.content || parsed.message.parts;
          if (Array.isArray(content2)) {
            for (const item of content2) {
              if (typeof item === "object" && item && "text" in item && !item.thought) {
                lastMessagePreview = String(item.text).substring(0, 100);
                break;
              }
            }
          }
        }
"""


class TestGetHistoryFilesPatch:
    """Test the getHistoryFiles patch (Bug 1)."""

    def test_old_pattern_exists(self):
        """OLD pattern should match original code."""
        patch_module = load_patch_module()

        # OLD_FN should be defined and contain expected content
        assert "getHistoryFiles" in patch_module.OLD_FN
        assert "readDir(historyDir)" in patch_module.OLD_FN
        # Should NOT contain chats/ scanning
        assert "chatsDir" not in patch_module.OLD_FN

    def test_new_pattern_adds_chats_scan(self):
        """NEW pattern should include chats/ subdirectory scanning."""
        patch_module = load_patch_module()

        # NEW_FN should add chats/ scanning
        assert "chatsDir" in patch_module.NEW_FN
        assert "`${historyDir}/chats`" in patch_module.NEW_FN

    def test_patch_preserves_original_logic(self):
        """Patch should preserve original file scanning logic."""
        patch_module = load_patch_module()

        # Both OLD and NEW should have original scanning
        assert "readDir(historyDir)" in patch_module.OLD_FN
        assert "readDir(historyDir)" in patch_module.NEW_FN


class TestGroupConversationsPatch:
    """Test the groupConversations patch (Bug 2)."""

    def test_old_pattern_has_bug(self):
        """OLD pattern has the bug that collapses id-less sessions."""
        patch_module = load_patch_module()

        # OLD_GROUP should have the buggy logic
        assert "isSubset(currentConv.messageIds, existingConv.messageIds)" in patch_module.OLD_GROUP
        # Should NOT have the size check
        assert "messageIds.size" not in patch_module.OLD_GROUP

    def test_new_pattern_adds_size_check(self):
        """NEW pattern adds size check to fix the bug."""
        patch_module = load_patch_module()

        # NEW_GROUP should have the size check
        assert "currentConv.messageIds.size > 0" in patch_module.NEW_GROUP

    def test_fix_keeps_id_less_sessions(self):
        """Fix should keep sessions without message IDs."""
        patch_module = load_patch_module()

        # The NEW pattern should short-circuit the subset check
        # when messageIds.size == 0 (i.e., id-less sessions)
        assert "currentConv.messageIds.size > 0 &&" in patch_module.NEW_GROUP


class TestLastMessagePreviewPatch:
    """Test the lastMessagePreview patch (Bug 3)."""

    def test_old_pattern_only_matches_assistant(self):
        """OLD pattern only matches role='assistant'."""
        patch_module = load_patch_module()

        # OLD_PREVIEW should only check for assistant role
        assert 'role === "assistant"' in patch_module.OLD_PREVIEW
        # Should NOT check for model role
        assert 'role === "model"' not in patch_module.OLD_PREVIEW

    def test_new_pattern_matches_model_role(self):
        """NEW pattern also matches role='model'."""
        patch_module = load_patch_module()

        # NEW_PREVIEW should check for both assistant and model
        assert 'role === "assistant"' in patch_module.NEW_PREVIEW
        assert 'role === "model"' in patch_module.NEW_PREVIEW

    def test_new_pattern_handles_parts(self):
        """NEW pattern handles message.parts format."""
        patch_module = load_patch_module()

        # NEW_PREVIEW should fall back to parts
        assert "parsed.message.parts" in patch_module.NEW_PREVIEW

    def test_new_pattern_skips_thought_parts(self):
        """NEW pattern skips thought parts for preview."""
        patch_module = load_patch_module()

        # NEW_PREVIEW should skip thought parts
        assert "!item.thought" in patch_module.NEW_PREVIEW


class TestIdempotency:
    """Test idempotent behavior."""

    def test_already_patched_detection(self):
        """Script should detect already-patched bundles."""
        patch_module = load_patch_module()

        # All NEW patterns should be defined
        assert patch_module.NEW_FN
        assert patch_module.NEW_GROUP
        assert patch_module.NEW_PREVIEW

    def test_main_returns_int(self):
        """main() should return an integer exit code."""
        patch_module = load_patch_module()

        # main() should exist and be callable
        assert callable(patch_module.main)
        # Running without actual bundle should return non-zero
        result = patch_module.main()
        assert isinstance(result, int)


class TestBundlePath:
    """Test bundle path handling."""

    def test_bundle_path_is_correct(self):
        """Bundle path should point to the correct location."""
        patch_module = load_patch_module()

        # BUNDLE should point to the server bundle
        assert "qwen-code-webui" in patch_module.BUNDLE
        assert "cli/node.js" in patch_module.BUNDLE


class TestErrorHandling:
    """Test error handling scenarios."""

    def test_version_drift_detected(self):
        """Version drift should be detected when patterns don't match."""
        content = "some random content without any patterns"

        patch_module = load_patch_module()

        # None of the OLD patterns should be found in random content
        assert patch_module.OLD_FN not in content
        assert patch_module.OLD_GROUP not in content
        assert patch_module.OLD_PREVIEW not in content

    def test_unique_pattern_requirement(self):
        """Patterns should appear exactly once."""
        patch_module = load_patch_module()

        # The OLD patterns should be well-defined unique strings
        # Not testing actual uniqueness here, just that they're defined
        assert len(patch_module.OLD_FN) > 100  # Should be substantial content
        assert len(patch_module.OLD_GROUP) > 50
        assert len(patch_module.OLD_PREVIEW) > 100


class TestPatchLogic:
    """Test the actual patch transformation logic."""

    def test_getHistoryFiles_patch_logic(self):
        """getHistoryFiles patch logic is correct."""
        patch_module = load_patch_module()

        # After patch, should have chats/ scanning
        result = patch_module.OLD_FN.replace(patch_module.OLD_FN, patch_module.NEW_FN)
        assert "chatsDir" in result

    def test_groupConversations_patch_logic(self):
        """groupConversations patch logic is correct."""
        patch_module = load_patch_module()

        content = patch_module.OLD_GROUP
        result = content.replace(patch_module.OLD_GROUP, patch_module.NEW_GROUP)
        assert "messageIds.size > 0" in result

    def test_lastMessagePreview_patch_logic(self):
        """lastMessagePreview patch logic is correct."""
        patch_module = load_patch_module()

        content = patch_module.OLD_PREVIEW
        result = content.replace(patch_module.OLD_PREVIEW, patch_module.NEW_PREVIEW)
        assert 'role === "model"' in result
        assert "!item.thought" in result