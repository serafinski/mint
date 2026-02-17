"""Unit tests for the capability classifier."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.classifier import (
    classifier_node,
    classify_gap,
)
from src.schemas.capability import CapabilityType
from src.schemas.task import (
    ClassificationResult,
    ClassifiedGap,
    GapItem,
    TaskState,
    TaskStatus,
)

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestClassificationResult:
    def test_valid_result(self):
        r = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.85,
            rationale="It's a deterministic function",
        )
        assert r.classified_type == CapabilityType.MCP_TOOL

    def test_confidence_bounds(self):
        with pytest.raises(ValueError):
            ClassificationResult(
                classified_type=CapabilityType.SKILL,
                confidence=1.5,
                rationale="oops",
            )

    def test_roundtrip(self):
        r = ClassificationResult(
            classified_type=CapabilityType.AGENT,
            confidence=0.6,
            rationale="needs planning",
        )
        roundtripped = ClassificationResult.model_validate_json(r.model_dump_json())
        assert roundtripped.classified_type == CapabilityType.AGENT


# ---------------------------------------------------------------------------
# classify_gap — LLM-based classification
# ---------------------------------------------------------------------------


def _mock_llm_chain(return_value):
    """Set up mock init_chat_model → with_structured_output → invoke chain."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    mock_structured.invoke.return_value = return_value
    return mock_llm, mock_structured


class TestClassifyGap:
    @pytest.fixture(autouse=True)
    def _reset_classifier_llm(self):
        """Reset the lazy-init LLM singleton so each test gets a fresh mock."""
        import src.orchestrator.classifier as mod

        mod._classifier_llm = None
        yield
        mod._classifier_llm = None

    def test_successful_classification(self):
        """LLM returns a valid ClassificationResult → ClassifiedGap produced."""
        result = ClassificationResult(
            classified_type=CapabilityType.SKILL,
            confidence=0.9,
            rationale="Multi-step data workflow",
        )
        mock_llm, _ = _mock_llm_chain(result)

        gap = GapItem(
            description="Data cleaning pipeline for records",
            query="data cleaning pipeline",
        )

        with patch("langchain.chat_models.init_chat_model", return_value=mock_llm):
            classified = classify_gap(gap)

        assert classified.classified_type == CapabilityType.SKILL
        assert classified.confidence == 0.9
        assert classified.rationale == "Multi-step data workflow"
        assert classified.description == gap.description
        assert classified.query == gap.query

    def test_temperature_zero(self):
        """init_chat_model is called with temperature=0 for determinism."""
        result = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.95,
            rationale="Stateless function",
        )
        mock_llm, _ = _mock_llm_chain(result)

        gap = GapItem(description="Parse JSON", query="parse JSON")

        with patch("langchain.chat_models.init_chat_model", return_value=mock_llm) as mock_init:
            classify_gap(gap)

        mock_init.assert_called_once_with("deepseek:deepseek-chat", temperature=0)

    def test_prompt_includes_gap_fields(self):
        """The prompt sent to the LLM contains the gap description and query."""
        result = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="Atomic operation",
        )
        mock_llm, mock_structured = _mock_llm_chain(result)

        gap = GapItem(
            description="Compute SHA-256 hash of a file",
            query="compute file hash",
        )

        with patch("langchain.chat_models.init_chat_model", return_value=mock_llm):
            classify_gap(gap)

        call_args = mock_structured.invoke.call_args
        prompt_sent = call_args[0][0]
        assert "Compute SHA-256 hash of a file" in prompt_sent
        assert "compute file hash" in prompt_sent

    def test_llm_failure_defaults_to_mcp_tool(self):
        """If the LLM call fails, default to MCP_TOOL with low confidence."""
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.side_effect = RuntimeError("API error")

        gap = GapItem(
            description="Something unusual",
            query="something unusual",
        )

        with patch("langchain.chat_models.init_chat_model", return_value=mock_llm):
            classified = classify_gap(gap)

        assert classified.classified_type == CapabilityType.MCP_TOOL
        assert classified.confidence == 0.3
        assert "failed" in classified.rationale.lower()

    def test_returns_classified_gap_type(self):
        """classify_gap always returns a ClassifiedGap instance."""
        result = ClassificationResult(
            classified_type=CapabilityType.AGENT,
            confidence=0.85,
            rationale="Requires planning",
        )
        mock_llm, _ = _mock_llm_chain(result)

        gap = GapItem(
            description="Autonomous investigation",
            query="autonomous investigation agent",
        )

        with patch("langchain.chat_models.init_chat_model", return_value=mock_llm):
            classified = classify_gap(gap)

        assert isinstance(classified, ClassifiedGap)


# ---------------------------------------------------------------------------
# Classifier node
# ---------------------------------------------------------------------------


class TestClassifierNode:
    def test_classifies_all_gaps(self):
        """Node classifies every gap and returns them in classified_gaps."""
        state = TaskState(
            task_description="Multi-part task",
            gaps=[
                GapItem(description="Parse CSV file", query="parse CSV"),
                GapItem(
                    description="Data cleaning workflow",
                    query="data cleaning workflow",
                ),
            ],
        )

        mock_results = [
            ClassifiedGap(
                description="Parse CSV file",
                query="parse CSV",
                classified_type=CapabilityType.MCP_TOOL,
                confidence=0.95,
                rationale="Atomic parse operation",
            ),
            ClassifiedGap(
                description="Data cleaning workflow",
                query="data cleaning workflow",
                classified_type=CapabilityType.SKILL,
                confidence=0.9,
                rationale="Multi-step workflow",
            ),
        ]

        with patch("src.orchestrator.classifier.classify_gap", side_effect=mock_results):
            cmd = classifier_node(state)

        assert cmd.goto == "__end__"
        classified = cmd.update["classified_gaps"]
        assert len(classified) == 2
        assert classified[0].classified_type == CapabilityType.MCP_TOOL
        assert classified[1].classified_type == CapabilityType.SKILL
        assert cmd.update["status"] == TaskStatus.CLASSIFYING

    def test_empty_gaps(self):
        state = TaskState(task_description="No gaps", gaps=[])
        cmd = classifier_node(state)

        assert cmd.update["classified_gaps"] == []
        assert cmd.update["status"] == TaskStatus.CLASSIFYING

    def test_single_gap(self):
        state = TaskState(
            task_description="Simple",
            gaps=[
                GapItem(
                    description="Compute hash of string",
                    query="compute hash",
                ),
            ],
        )

        mock_result = ClassifiedGap(
            description="Compute hash of string",
            query="compute hash",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.95,
            rationale="Stateless hash computation",
        )

        with patch("src.orchestrator.classifier.classify_gap", return_value=mock_result):
            cmd = classifier_node(state)

        classified = cmd.update["classified_gaps"]
        assert len(classified) == 1
        assert classified[0].classified_type == CapabilityType.MCP_TOOL

    def test_output_is_classified_gap_type(self):
        state = TaskState(
            task_description="Check types",
            gaps=[
                GapItem(description="Sort numbers", query="sort numbers"),
            ],
        )

        mock_result = ClassifiedGap(
            description="Sort numbers",
            query="sort numbers",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="Deterministic sort",
        )

        with patch("src.orchestrator.classifier.classify_gap", return_value=mock_result):
            cmd = classifier_node(state)

        for item in cmd.update["classified_gaps"]:
            assert isinstance(item, ClassifiedGap)
