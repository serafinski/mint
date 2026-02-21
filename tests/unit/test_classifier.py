"""Unit tests for the capability classifier.

LLM-calling behaviour is covered by ``tests/integration/test_classifier.py``
with real DeepSeek calls.  These tests cover schema validation and the
classifier node's routing logic (patching ``classify_gap``, not the LLM).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.classifier import classifier_node, classify_gap
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
# classify_gap — LLM success and failure paths (mocking _get_llm)
# ---------------------------------------------------------------------------


def _make_mock_llm(response=None, raises=None):
    """Return a mock LLM chain (llm.with_structured_output().invoke() pattern)."""
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_llm.with_structured_output.return_value = mock_structured
    if raises is not None:
        mock_structured.invoke.side_effect = raises
    else:
        mock_structured.invoke.return_value = response
    return mock_llm


class TestClassifyGap:
    def _gap(self, description="Parse CSV file", query="parse CSV"):
        return GapItem(description=description, query=query)

    def test_success_returns_classified_gap(self):
        """Successful LLM call produces a ClassifiedGap with the right fields."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.95,
            rationale="Deterministic parse",
        )
        gap = self._gap()

        with patch(
            "src.orchestrator.classifier._get_llm",
            return_value=_make_mock_llm(mock_response),
        ):
            result = classify_gap(gap)

        assert isinstance(result, ClassifiedGap)
        assert result.classified_type == CapabilityType.MCP_TOOL
        assert result.confidence == 0.95
        assert result.description == gap.description
        assert result.query == gap.query

    def test_llm_failure_falls_back_to_mcp_tool(self):
        """When LLM raises any exception, classify_gap returns MCP_TOOL with confidence 0.3."""
        gap = self._gap()

        with patch(
            "src.orchestrator.classifier._get_llm",
            return_value=_make_mock_llm(raises=RuntimeError("API error")),
        ):
            result = classify_gap(gap)

        assert result.classified_type == CapabilityType.MCP_TOOL
        assert result.confidence == 0.3
        assert result.description == gap.description
        assert result.query == gap.query
        assert "LLM classification failed" in result.rationale

    def test_llm_failure_rationale_contains_error(self):
        """Fallback rationale includes the original exception message."""
        gap = self._gap()

        with patch(
            "src.orchestrator.classifier._get_llm",
            return_value=_make_mock_llm(raises=ValueError("rate limit exceeded")),
        ):
            result = classify_gap(gap)

        assert "rate limit exceeded" in result.rationale

    def test_uses_structured_output_with_classification_result(self):
        """classify_gap calls with_structured_output(ClassificationResult)."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.SKILL,
            confidence=0.8,
            rationale="Multi-step workflow",
        )
        gap = self._gap()
        mock_llm = _make_mock_llm(mock_response)

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_gap(gap)

        mock_llm.with_structured_output.assert_called_once_with(ClassificationResult)

    def test_invoke_called_without_explicit_callbacks(self):
        """classify_gap does not pass a per-node callbacks config to structured_llm.invoke.

        The pipeline-level LangFuse CallbackHandler propagates automatically via
        LangChain's contextvar inheritance — no per-node handler is needed.
        """
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="test",
        )
        gap = self._gap()
        mock_llm = _make_mock_llm(mock_response)

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_gap(gap)

        invoke_call = mock_llm.with_structured_output.return_value.invoke
        # invoke must be called with exactly the prompt string — no config kwarg
        args, kwargs = invoke_call.call_args
        assert "config" not in kwargs

    def test_gap_fields_preserved_on_success(self):
        """description and query from the GapItem are preserved in the result."""
        gap = GapItem(
            description="Run a multi-step ETL pipeline",
            query="ETL pipeline orchestration",
        )
        mock_response = ClassificationResult(
            classified_type=CapabilityType.AGENT,
            confidence=0.7,
            rationale="Needs planning",
        )

        with patch(
            "src.orchestrator.classifier._get_llm",
            return_value=_make_mock_llm(mock_response),
        ):
            result = classify_gap(gap)

        assert result.description == "Run a multi-step ETL pipeline"
        assert result.query == "ETL pipeline orchestration"


# ---------------------------------------------------------------------------
# Classifier node — routing logic (patches classify_gap, not the LLM)
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

        assert cmd.goto == "generate_mcp_tool"
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

    def test_fallback_classification_added_to_errors(self):
        """When classify_gap returns a fallback result, the node records it in state.errors."""
        state = TaskState(
            task_description="Test",
            gaps=[GapItem(description="Parse CSV", query="parse-csv")],
        )

        fallback = ClassifiedGap(
            description="Parse CSV",
            query="parse-csv",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.3,
            rationale="LLM classification failed (API error); defaulting to MCP_TOOL",
        )

        with patch("src.orchestrator.classifier.classify_gap", return_value=fallback):
            cmd = classifier_node(state)

        assert len(cmd.update.get("errors", [])) == 1
        assert "parse-csv" in cmd.update["errors"][0]

    def test_fallback_classification_still_produces_classified_gap(self):
        """A fallback classification is still included in classified_gaps."""
        state = TaskState(
            task_description="Test",
            gaps=[GapItem(description="Parse CSV", query="parse-csv")],
        )

        fallback = ClassifiedGap(
            description="Parse CSV",
            query="parse-csv",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.3,
            rationale="LLM classification failed (API error); defaulting to MCP_TOOL",
        )

        with patch("src.orchestrator.classifier.classify_gap", return_value=fallback):
            cmd = classifier_node(state)

        assert len(cmd.update["classified_gaps"]) == 1
        assert cmd.update["classified_gaps"][0].classified_type == CapabilityType.MCP_TOOL

    def test_preserves_existing_errors(self):
        """classifier_node propagates pre-existing errors from state."""
        state = TaskState(
            task_description="Test",
            gaps=[],
            errors=["pre-existing error from decomposition"],
        )
        cmd = classifier_node(state)

        assert "pre-existing error from decomposition" in cmd.update["errors"]

    def test_classify_gap_unhandled_exception_propagates(self):
        """Unexpected exceptions raised by classify_gap propagate from classifier_node.

        classify_gap itself has comprehensive exception handling and should not
        normally raise. This test documents the current behaviour for the edge
        case where an exception does escape classify_gap (e.g., a Pydantic
        validation failure during ClassifiedGap instantiation).
        """
        state = TaskState(
            task_description="Test",
            gaps=[GapItem(description="Parse CSV", query="parse-csv")],
        )

        with (
            patch(
                "src.orchestrator.classifier.classify_gap",
                side_effect=RuntimeError("unexpected internal error"),
            ),
            pytest.raises(RuntimeError, match="unexpected internal error"),
        ):
            classifier_node(state)

    def test_preserves_existing_classified_gaps(self):
        """classifier_node accumulates into existing classified_gaps, not replaces them."""
        existing = ClassifiedGap(
            description="Pre-classified gap from earlier pass",
            query="pre-existing",
            classified_type=CapabilityType.AGENT,
            confidence=0.8,
            rationale="Pre-existing",
        )
        state = TaskState(
            task_description="Incremental classification",
            gaps=[
                GapItem(description="New tool needed", query="new-tool"),
            ],
            classified_gaps=[existing],
        )

        new_result = ClassifiedGap(
            description="New tool needed",
            query="new-tool",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.95,
            rationale="Atomic operation",
        )

        with patch("src.orchestrator.classifier.classify_gap", return_value=new_result):
            cmd = classifier_node(state)

        classified = cmd.update["classified_gaps"]
        assert len(classified) == 2
        assert classified[0].query == "pre-existing"
        assert classified[1].query == "new-tool"
