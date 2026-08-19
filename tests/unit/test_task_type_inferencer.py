"""Unit tests for TaskTypeInferencer module."""

import pytest

from app.modules.analytics.task_type_inferencer import InferenceResult, TaskType, TaskTypeInferencer


class TestTaskTypeInferencer:
    """Test TaskTypeInferencer."""

    def test_infer_code_generation(self):
        """Test inference for code generation tools."""
        result = TaskTypeInferencer.infer("code_generator")
        assert result.task_type == TaskType.CODE_GENERATION
        assert result.confidence == 100.0
        assert "code" in result.matched_patterns

    def test_infer_code_generation_generate(self):
        """Test inference with 'generate' pattern."""
        result = TaskTypeInferencer.infer("text_generator")
        assert result.task_type == TaskType.CODE_GENERATION
        assert result.confidence == 100.0
        assert "generator" in result.matched_patterns

    def test_infer_document_analysis(self):
        """Test inference for document analysis tools."""
        result = TaskTypeInferencer.infer("document_analyzer")
        assert result.task_type == TaskType.DOCUMENT_ANALYSIS
        assert result.confidence == 100.0
        assert "document" in result.matched_patterns

    def test_infer_document_analysis_analyze(self):
        """Test inference with 'analyze' pattern."""
        result = TaskTypeInferencer.infer("text_analyze_tool")
        assert result.task_type == TaskType.DOCUMENT_ANALYSIS
        assert result.confidence == 100.0
        assert "analyze" in result.matched_patterns

    def test_infer_conversation(self):
        """Test inference for chat/conversation tools."""
        result = TaskTypeInferencer.infer("chat_assistant")
        assert result.task_type == TaskType.CONVERSATION
        assert result.confidence == 100.0
        assert "chat" in result.matched_patterns

    def test_infer_conversation_bot(self):
        """Test inference with 'bot' pattern."""
        result = TaskTypeInferencer.infer("ai_bot")
        assert result.task_type == TaskType.CONVERSATION
        assert result.confidence == 100.0
        assert "bot" in result.matched_patterns

    def test_infer_general_unknown(self):
        """Test inference for unknown tools."""
        result = TaskTypeInferencer.infer("unknown_tool")
        assert result.task_type == TaskType.GENERAL
        assert result.confidence == 100.0
        assert len(result.matched_patterns) == 0

    def test_infer_general_empty(self):
        """Test inference for empty tool name."""
        result = TaskTypeInferencer.infer("")
        assert result.task_type == TaskType.GENERAL
        assert result.confidence == 100.0
        assert len(result.matched_patterns) == 0

    def test_infer_general_none(self):
        """Test inference for None tool name."""
        result = TaskTypeInferencer.infer(None)
        assert result.task_type == TaskType.GENERAL
        assert result.confidence == 100.0
        assert len(result.matched_patterns) == 0

    def test_infer_conflict_fallback(self):
        """Test inference with conflicting patterns."""
        # Tool name with both code and chat patterns
        result = TaskTypeInferencer.infer("code_chat_tool")
        assert result.task_type == TaskType.GENERAL
        assert result.confidence == 50.0
        assert len(result.matched_patterns) >= 2

    def test_case_insensitive(self):
        """Test inference is case insensitive."""
        result = TaskTypeInferencer.infer("CODE_GENERATOR")
        assert result.task_type == TaskType.CODE_GENERATION
        assert result.confidence == 100.0

    def test_get_all_patterns(self):
        """Test get_all_patterns returns expected structure."""
        patterns = TaskTypeInferencer.get_all_patterns()
        assert isinstance(patterns, dict)
        assert "code" in patterns
        assert patterns["code"] == TaskType.CODE_GENERATION
        assert "document" in patterns
        assert patterns["document"] == TaskType.DOCUMENT_ANALYSIS
        assert "chat" in patterns
        assert patterns["chat"] == TaskType.CONVERSATION


class TestTaskType:
    """Test TaskType enum."""

    def test_task_type_values(self):
        """Test TaskType enum values."""
        assert TaskType.GENERAL.value == "GENERAL"
        assert TaskType.CODE_GENERATION.value == "CODE_GENERATION"
        assert TaskType.DOCUMENT_ANALYSIS.value == "DOCUMENT_ANALYSIS"
        assert TaskType.CONVERSATION.value == "CONVERSATION"

    def test_task_type_from_string(self):
        """Test creating TaskType from string."""
        assert TaskType("GENERAL") == TaskType.GENERAL
        assert TaskType("CODE_GENERATION") == TaskType.CODE_GENERATION
        assert TaskType("DOCUMENT_ANALYSIS") == TaskType.DOCUMENT_ANALYSIS
        assert TaskType("CONVERSATION") == TaskType.CONVERSATION


class TestInferenceResult:
    """Test InferenceResult dataclass."""

    def test_inference_result_creation(self):
        """Test creating InferenceResult."""
        result = InferenceResult(
            task_type=TaskType.CODE_GENERATION,
            confidence=100.0,
            matched_patterns=["code"],
        )
        assert result.task_type == TaskType.CODE_GENERATION
        assert result.confidence == 100.0
        assert result.matched_patterns == ["code"]

    def test_inference_result_confidence_range(self):
        """Test confidence is in valid range."""
        # Valid confidence values
        for confidence in [0.0, 50.0, 100.0]:
            result = InferenceResult(
                task_type=TaskType.GENERAL,
                confidence=confidence,
                matched_patterns=[],
            )
            assert 0 <= result.confidence <= 100
