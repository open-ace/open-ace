"""
Task Type Inference for Efficiency Calculation.

This module provides task type classification based on tool_name patterns,
enabling task-specific efficiency thresholds.
"""

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """
    Task type classification for efficiency calculation.

    Different task types have different efficiency characteristics:
    - GENERAL: Default/fallback for unclassified tasks
    - CODE_GENERATION: Code generation tasks (higher output ratio expected)
    - DOCUMENT_ANALYSIS: Document analysis tasks (higher input ratio expected)
    - CONVERSATION: Chat/conversation tasks (balanced ratio)
    """

    GENERAL = "GENERAL"
    CODE_GENERATION = "CODE_GENERATION"
    DOCUMENT_ANALYSIS = "DOCUMENT_ANALYSIS"
    CONVERSATION = "CONVERSATION"


@dataclass
class InferenceResult:
    """Result of task type inference."""

    task_type: TaskType
    confidence: float  # 0-100
    matched_patterns: list[str]


class TaskTypeInferencer:
    """
    Infer task type from tool_name patterns.

    Inference Rules:
        - "*code*" or "*generate*" → CODE_GENERATION (100% confidence)
        - "*document*" or "*analyze*" → DOCUMENT_ANALYSIS (100% confidence)
        - "*chat*" or "*conversation*" → CONVERSATION (100% confidence)
        - Multiple pattern conflicts → GENERAL (50% confidence)
        - No match → GENERAL (100% confidence)

    Example usage:
        inferencer = TaskTypeInferencer()
        result = inferencer.infer("code_generator")
        assert result.task_type == TaskType.CODE_GENERATION
        assert result.confidence == 100.0
    """

    # Pattern to TaskType mapping
    # Each pattern is case-insensitive
    PATTERNS: dict[TaskType, list[str]] = {
        TaskType.CODE_GENERATION: ["code", "generate", "generator"],
        TaskType.DOCUMENT_ANALYSIS: ["document", "analyze", "analysis", "doc"],
        TaskType.CONVERSATION: ["chat", "conversation", "assistant", "bot"],
    }

    @classmethod
    def infer(cls, tool_name: str | None) -> InferenceResult:
        """
        Infer task type from tool_name.

        Args:
            tool_name: Tool name string (e.g., "code_generator", "document_analyzer")

        Returns:
            InferenceResult with task_type, confidence, and matched patterns
        """
        if not tool_name:
            return InferenceResult(
                task_type=TaskType.GENERAL,
                confidence=100.0,
                matched_patterns=[],
            )

        tool_name_lower = tool_name.lower()

        # Find all matching patterns
        matches: list[tuple[TaskType, str]] = []
        for task_type, patterns in cls.PATTERNS.items():
            for pattern in patterns:
                if pattern in tool_name_lower:
                    matches.append((task_type, pattern))

        # No matches → GENERAL with 100% confidence
        if not matches:
            return InferenceResult(
                task_type=TaskType.GENERAL,
                confidence=100.0,
                matched_patterns=[],
            )

        # Single task type matched → that type with 100% confidence
        unique_task_types: set[TaskType] = {m[0] for m in matches}
        if len(unique_task_types) == 1:
            task_type = matches[0][0]
            matched_patterns = [m[1] for m in matches]
            return InferenceResult(
                task_type=task_type,
                confidence=100.0,
                matched_patterns=matched_patterns,
            )

        # Multiple task types matched → conflict, fallback to GENERAL with 50% confidence
        matched_patterns = [m[1] for m in matches]
        logger.debug(
            "Task type inference conflict for tool_name=%s: matched patterns %s, "
            "falling back to GENERAL",
            tool_name,
            matched_patterns,
        )
        return InferenceResult(
            task_type=TaskType.GENERAL,
            confidence=50.0,
            matched_patterns=matched_patterns,
        )

    @classmethod
    def get_all_patterns(cls) -> dict[str, TaskType]:
        """
        Get all patterns mapped to their task types.

        Returns:
            Dict mapping pattern string to TaskType
        """
        result: dict[str, TaskType] = {}
        for task_type, patterns in cls.PATTERNS.items():
            for pattern in patterns:
                result[pattern] = task_type
        return result
