"""Unit tests for the per-gap capability classifier.

These tests cover schema validation, mocked LLM success/failure paths, and
the classifier node's routing logic.

``classifier_node`` iterates over ``state.gaps`` and calls ``classify_gap``
per gap.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.classifier import (
    MAX_CLASSIFICATION_ATTEMPTS,
    _enforce_file_writer_mcp_tool,
    classifier_node,
    classify_gap,
)
from src.schemas.capability import CapabilityType
from src.schemas.task import (
    ClassificationResult,
    ClassifiedGap,
    GapItem,
    OutputContract,
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
            capability_name="csv-row-counter",
        )
        assert r.classified_type == CapabilityType.MCP_TOOL
        assert r.capability_name == "csv-row-counter"

    def test_confidence_bounds(self):
        with pytest.raises(ValueError):
            ClassificationResult(
                classified_type=CapabilityType.SKILL,
                confidence=1.5,
                rationale="oops",
                capability_name="some-name",
            )

    def test_roundtrip(self):
        r = ClassificationResult(
            classified_type=CapabilityType.AGENT,
            confidence=0.6,
            rationale="needs planning",
            capability_name="exploratory-analyzer",
        )
        roundtripped = ClassificationResult.model_validate_json(r.model_dump_json())
        assert roundtripped.classified_type == CapabilityType.AGENT
        assert roundtripped.capability_name == "exploratory-analyzer"

    def test_capability_name_required(self):
        """capability_name is required; missing it must raise."""
        with pytest.raises(ValueError):
            ClassificationResult(
                classified_type=CapabilityType.MCP_TOOL,
                confidence=0.9,
                rationale="test",
            )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# classify_gap: LLM success and failure paths (mocking _get_llm)
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
    def test_success_returns_classified_gap_with_original_gap_query(self):
        """Successful LLM call preserves the original gap query for composition."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.95,
            rationale="Deterministic parse",
            capability_name="csv-row-column-summarizer",
        )
        gap = GapItem(description="Parse the CSV input", query="parse-csv")

        with patch(
            "src.orchestrator.classifier._get_llm",
            return_value=_make_mock_llm(mock_response),
        ):
            result = classify_gap(
                gap,
                task_description="Parse a CSV file and return row count",
                required_capabilities=["parse-csv"],
            )

        assert isinstance(result, ClassifiedGap)
        assert result.classified_type == CapabilityType.MCP_TOOL
        assert result.confidence == 0.95
        assert result.description == "Parse the CSV input"
        assert result.query == "parse-csv"

    def test_llm_failure_returns_none_after_retry_budget(self):
        """When all attempts fail, classify_gap returns None."""
        gap = GapItem(description="Parse CSV", query="parse-csv")

        with patch(
            "src.orchestrator.classifier._get_llm",
            return_value=_make_mock_llm(raises=RuntimeError("API error")),
        ):
            result = classify_gap(gap, "Parse a CSV file", required_capabilities=[])

        assert result is None

    def test_llm_failure_retries_max_attempts(self):
        """Transient classifier failures consume the configured retry budget."""
        gap = GapItem(description="Parse CSV", query="parse-csv")
        mock_llm = _make_mock_llm(raises=RuntimeError("rate limit exceeded"))

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            result = classify_gap(gap, "Parse a CSV file", required_capabilities=[])

        assert result is None
        invoke = mock_llm.with_structured_output.return_value.invoke
        assert invoke.call_count == MAX_CLASSIFICATION_ATTEMPTS

    def test_uses_structured_output_with_classification_result(self):
        """classify_gap calls with_structured_output(ClassificationResult)."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.SKILL,
            confidence=0.8,
            rationale="Multi-step workflow",
            capability_name="multi-step-processor",
        )
        mock_llm = _make_mock_llm(mock_response)
        gap = GapItem(description="Some capability", query="some-cap")

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_gap(gap, "Some task", required_capabilities=["cap-a"])

        mock_llm.with_structured_output.assert_called_once_with(ClassificationResult)

    def test_invoke_called_without_explicit_callbacks(self):
        """classify_gap does not pass a per-node callbacks config to structured_llm.invoke.

        The pipeline-level LangFuse CallbackHandler propagates automatically via
        LangChain's contextvar inheritance, so no per-node handler is needed.
        """
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="test",
            capability_name="test-cap",
        )
        mock_llm = _make_mock_llm(mock_response)
        gap = GapItem(description="Some capability", query="some-cap")

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_gap(gap, "Some task", required_capabilities=[])

        invoke_call = mock_llm.with_structured_output.return_value.invoke
        args, kwargs = invoke_call.call_args
        assert "config" not in kwargs

    def test_required_capabilities_included_in_prompt(self):
        """classify_gap passes required_capabilities into the prompt."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="Single function",
            capability_name="csv-parser",
        )
        mock_llm = _make_mock_llm(mock_response)
        gap = GapItem(description="Parse CSV", query="parse-csv")

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_gap(
                gap,
                "Parse CSV",
                required_capabilities=["parse-csv", "count-rows", "extract-columns"],
            )

        invoke_call = mock_llm.with_structured_output.return_value.invoke
        prompt_text = invoke_call.call_args[0][0]
        assert "parse-csv" in prompt_text
        assert "count-rows" in prompt_text
        assert "extract-columns" in prompt_text

    def test_empty_required_capabilities_shown_as_none(self):
        """Empty required_capabilities list renders as '(none identified)'."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="test",
            capability_name="csv-parser",
        )
        mock_llm = _make_mock_llm(mock_response)
        gap = GapItem(description="Parse CSV", query="parse-csv")

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_gap(gap, "Parse CSV", required_capabilities=[])

        invoke_call = mock_llm.with_structured_output.return_value.invoke
        prompt_text = invoke_call.call_args[0][0]
        assert "(none identified)" in prompt_text

    def test_task_description_truncated_in_prompt(self):
        """Very long task descriptions are truncated to 1500 chars in the prompt."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="test",
            capability_name="long-task-processor",
        )
        mock_llm = _make_mock_llm(mock_response)
        long_desc = "A" * 5000
        gap = GapItem(description="long capability", query="long-cap")

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_gap(gap, long_desc, required_capabilities=[])

        invoke_call = mock_llm.with_structured_output.return_value.invoke
        prompt_text = invoke_call.call_args[0][0]
        # Task description (A x 1500) is included but not the full 5000
        assert "A" * 1500 in prompt_text
        assert "A" * 1501 not in prompt_text

    def test_gap_description_truncated_in_classified_gap(self):
        """ClassifiedGap.description is capped at 500 chars."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="test",
            capability_name="bulk-processor",
        )
        mock_llm = _make_mock_llm(mock_response)
        gap = GapItem(description="B" * 2000, query="bulk-processing")

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            result = classify_gap(gap, "Bulk process", required_capabilities=[])

        assert len(result.description) == 500


# ---------------------------------------------------------------------------
# Classifier node: routing logic (patches classify_gap, not the LLM)
# ---------------------------------------------------------------------------


class TestClassifierNode:
    def test_classifies_each_gap(self):
        """Node calls classify_gap once per gap and collects the results."""
        state = TaskState(
            task_description="Parse a CSV file and summarise",
            required_capabilities=["parse-csv", "summarise"],
            gaps=[
                GapItem(description="parse-csv", query="parse-csv"),
                GapItem(description="summarise", query="summarise"),
            ],
        )

        def _mock_classify(gap, task_description, required_capabilities):
            return ClassifiedGap(
                description=gap.description,
                query=f"{gap.query}-abcdef",
                classified_type=CapabilityType.MCP_TOOL,
                confidence=0.95,
                rationale="Atomic operation",
            )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            side_effect=_mock_classify,
        ) as mock_classify:
            cmd = classifier_node(state)

        assert mock_classify.call_count == 2
        assert cmd.goto == "generate_mcp_tool"
        classified = cmd.update["classified_gaps"]
        assert len(classified) == 2
        assert {c.query for c in classified} == {"parse-csv-abcdef", "summarise-abcdef"}
        assert cmd.update["status"] == TaskStatus.CLASSIFYING

    def test_classify_gap_receives_gap_task_description_and_required_capabilities(self):
        """classifier_node calls classify_gap with gap + task context + required caps."""
        state = TaskState(
            task_description="Multi-part task description",
            required_capabilities=["cap-a", "cap-b", "cap-c"],
            gaps=[GapItem(description="cap-a", query="cap-a")],
        )

        captured: dict = {}

        def _mock_classify(gap, task_description, required_capabilities):
            captured["gap_query"] = gap.query
            captured["task_description"] = task_description
            captured["required_capabilities"] = required_capabilities
            return ClassifiedGap(
                description=gap.description,
                query="slug",
                classified_type=CapabilityType.MCP_TOOL,
                confidence=0.9,
                rationale="test",
            )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            side_effect=_mock_classify,
        ):
            classifier_node(state)

        assert captured["gap_query"] == "cap-a"
        assert captured["task_description"] == "Multi-part task description"
        assert captured["required_capabilities"] == ["cap-a", "cap-b", "cap-c"]

    def test_mixed_type_gaps_produce_mixed_classifications(self):
        """Two gaps in the same task can get different classified types."""
        state = TaskState(
            task_description="Parse CSV then investigate anomalies",
            required_capabilities=["parse-csv", "investigate"],
            gaps=[
                GapItem(description="parse-csv", query="parse-csv"),
                GapItem(description="investigate", query="investigate"),
            ],
        )

        def _mock_classify(gap, task_description, required_capabilities):
            if gap.query == "parse-csv":
                return ClassifiedGap(
                    description=gap.description,
                    query="csv-parser-1",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.95,
                    rationale="deterministic",
                )
            return ClassifiedGap(
                description=gap.description,
                query="investigator-1",
                classified_type=CapabilityType.AGENT,
                confidence=0.9,
                rationale="adaptive",
            )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            side_effect=_mock_classify,
        ):
            cmd = classifier_node(state)

        classified = cmd.update["classified_gaps"]
        assert len(classified) == 2
        types = {c.classified_type for c in classified}
        assert types == {CapabilityType.MCP_TOOL, CapabilityType.AGENT}

    def test_empty_gaps_is_noop(self):
        """When state.gaps is empty, classifier skips the LLM call."""
        state = TaskState(
            task_description="All matched",
            required_capabilities=["existing"],
            gaps=[],
        )

        with patch("src.orchestrator.classifier.classify_gap") as mock_classify:
            cmd = classifier_node(state)

        mock_classify.assert_not_called()
        # No-op early-return path in classifier_node: the Command update
        # contains only `status`; classified_gaps is NOT emitted, so the
        # pre-existing state.classified_gaps (empty by default) stays intact.
        assert "classified_gaps" not in cmd.update
        assert cmd.update["status"] == TaskStatus.CLASSIFYING
        assert cmd.goto == "generate_mcp_tool"

    def test_single_gap_produces_single_classification(self):
        """A single gap produces exactly one classified gap."""
        state = TaskState(
            task_description="Compute hash of string",
            required_capabilities=["compute-hash"],
            gaps=[GapItem(description="compute-hash", query="compute-hash")],
        )

        mock_result = ClassifiedGap(
            description="compute-hash",
            query="compute-hash-of-string-abcdef",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.95,
            rationale="Stateless hash computation",
        )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            return_value=mock_result,
        ):
            cmd = classifier_node(state)

        classified = cmd.update["classified_gaps"]
        assert len(classified) == 1
        assert classified[0].classified_type == CapabilityType.MCP_TOOL

    def test_output_is_classified_gap_type(self):
        state = TaskState(
            task_description="Sort numbers",
            required_capabilities=["sort"],
            gaps=[GapItem(description="sort", query="sort")],
        )

        mock_result = ClassifiedGap(
            description="sort",
            query="sort-numbers-abcdef",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="Deterministic sort",
        )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            return_value=mock_result,
        ):
            cmd = classifier_node(state)

        for item in cmd.update["classified_gaps"]:
            assert isinstance(item, ClassifiedGap)

    def test_classification_failure_sets_sticky_flag(self):
        """classifier_node marks the task failed when a gap cannot be classified."""
        state = TaskState(
            task_description="Parse CSV",
            required_capabilities=["parse-csv"],
            gaps=[GapItem(description="parse-csv", query="parse-csv")],
        )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            return_value=None,  # None = all retries exhausted.
        ):
            cmd = classifier_node(state)

        # No silent MCP_TOOL classification inserted.
        assert cmd.update["classified_gaps"] == []
        # Sticky flag flipped.
        assert cmd.update["classification_succeeded"] is False
        # Error marker present.
        errors = cmd.update.get("errors", [])
        assert any(
            "CLASSIFICATION_FAILED" in e or "Classification failed for gap" in e
            for e in errors
        )

    def test_partial_classification_failure_flips_sticky_flag(self):
        """Mixed outcome: one gap classifies successfully, one fails retries.
        Sticky flag still flips because any classification failure fails the task.
        """
        state = TaskState(
            task_description="Parse CSV and summarize",
            required_capabilities=["parse-csv", "summarize"],
            gaps=[
                GapItem(description="parse-csv", query="parse-csv"),
                GapItem(description="summarize", query="summarize"),
            ],
        )

        success = ClassifiedGap(
            description="parse-csv",
            query="parse-csv-abcdef",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.95,
            rationale="Deterministic CSV parsing",
        )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            side_effect=[success, None],  # first ok, second fails all retries
        ):
            cmd = classifier_node(state)

        # The successful one is still registered.
        assert len(cmd.update["classified_gaps"]) == 1
        assert cmd.update["classified_gaps"][0].query == "parse-csv-abcdef"
        # Sticky flag flipped because ONE gap failed.
        assert cmd.update["classification_succeeded"] is False

    def test_preserves_existing_errors(self):
        """classifier_node does not clobber pre-existing errors.

        When ``state.gaps`` is empty, the node takes the no-op early-return
        branch (classifier.py lines 401-407) and emits only ``{"status": ...}``.
        It does NOT include ``errors`` in the Command update, which means
        LangGraph keeps whatever ``state.errors`` held before the node ran,
        i.e. the decomposition error stays on the task. This test locks in
        that exact non-overwrite contract.
        """
        state = TaskState(
            task_description="Test",
            required_capabilities=[],
            gaps=[],
            errors=["pre-existing error from decomposition"],
        )

        cmd = classifier_node(state)

        # No-op branch does not touch errors; the key is absent from the
        # Command update, so pre-existing errors on state survive untouched.
        assert "errors" not in cmd.update

    def test_classify_gap_keyboard_interrupt_propagates(self):
        """KeyboardInterrupt (BaseException, not Exception) escapes the retry loop.

        ``classify_gap``'s retry loop catches ``Exception``: every normal
        failure is swallowed and converted to ``None`` after
        ``MAX_CLASSIFICATION_ATTEMPTS``. This is the intentional
        fail-closed-without-raising contract.

        However, ``BaseException`` subclasses like ``KeyboardInterrupt``,
        ``SystemExit``, and ``GeneratorExit`` deliberately bypass that net so
        Ctrl-C and process-level shutdown still work during a pipeline run.
        This test locks in that design: a BaseException raised inside
        ``classify_gap`` propagates up through ``classifier_node`` without
        being masked.
        """
        state = TaskState(
            task_description="Test",
            required_capabilities=["cap"],
            gaps=[GapItem(description="cap", query="cap")],
        )

        with (
            patch(
                "src.orchestrator.classifier.classify_gap",
                side_effect=KeyboardInterrupt("user pressed ctrl-c"),
            ),
            pytest.raises(KeyboardInterrupt, match="user pressed ctrl-c"),
        ):
            classifier_node(state)

    def test_preserves_existing_classified_gaps(self):
        """classifier_node accumulates into existing classified_gaps, not replaces them.

        When a gap in state.gaps shares its description with an existing
        classified gap (retry scenario), the existing entry is preserved and
        the gap is skipped; new gaps get classified and appended.
        """
        existing = ClassifiedGap(
            description="Pre-classified gap from earlier pass",
            query="pre-existing",
            classified_type=CapabilityType.AGENT,
            confidence=0.8,
            rationale="Pre-existing",
        )
        state = TaskState(
            task_description="Incremental classification",
            required_capabilities=["new-tool"],
            gaps=[GapItem(description="new-tool", query="new-tool")],
            classified_gaps=[existing],
        )

        new_result = ClassifiedGap(
            description="new-tool",
            query="new-tool-abcdef",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.95,
            rationale="Atomic operation",
        )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            return_value=new_result,
        ):
            cmd = classifier_node(state)

        classified = cmd.update["classified_gaps"]
        assert len(classified) == 2
        assert classified[0].query == "pre-existing"
        assert classified[1].classified_type == CapabilityType.MCP_TOOL

    def test_skill_classification(self):
        """A gap classified as SKILL produces a SKILL-typed classified gap."""
        state = TaskState(
            task_description="Multi-step data cleaning pipeline",
            required_capabilities=["pipeline"],
            gaps=[GapItem(description="pipeline", query="pipeline")],
        )

        mock_result = ClassifiedGap(
            description="pipeline",
            query="multi-step-data-cleaning-abcdef",
            classified_type=CapabilityType.SKILL,
            confidence=0.9,
            rationale="Prescribed workflow with domain rules",
        )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            return_value=mock_result,
        ):
            cmd = classifier_node(state)

        assert cmd.update["classified_gaps"][0].classified_type == CapabilityType.SKILL

    def test_agent_classification(self):
        """A gap classified as AGENT produces an AGENT-typed classified gap."""
        state = TaskState(
            task_description="Investigate cascading failures",
            required_capabilities=["investigation"],
            gaps=[GapItem(description="investigation", query="investigation")],
        )

        mock_result = ClassifiedGap(
            description="investigation",
            query="investigate-cascading-failures-abcdef",
            classified_type=CapabilityType.AGENT,
            confidence=0.95,
            rationale="Adaptive reasoning required",
        )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            return_value=mock_result,
        ):
            cmd = classifier_node(state)

        assert cmd.update["classified_gaps"][0].classified_type == CapabilityType.AGENT

    def test_empty_gaps_does_not_append_classified(self):
        """When gaps is empty, classified_gaps is NOT modified."""
        existing = ClassifiedGap(
            description="earlier",
            query="earlier",
            classified_type=CapabilityType.SKILL,
            confidence=0.8,
            rationale="prior",
        )
        state = TaskState(
            task_description="All matched",
            required_capabilities=["existing"],
            gaps=[],
            classified_gaps=[existing],
        )

        cmd = classifier_node(state)

        # No-op path: classified_gaps not in update (or unchanged)
        assert "classified_gaps" not in cmd.update


# ---------------------------------------------------------------------------
# File-writer override: structural safety net
# ---------------------------------------------------------------------------


class TestEnforceFileWriterMcpTool:
    """Direct unit tests for the override helper."""

    def test_no_required_files_noop(self):
        gaps = [
            ClassifiedGap(
                query="write-xlsx-file",
                description="write-xlsx-file",
                classified_type=CapabilityType.AGENT,
                confidence=0.9,
                rationale="AGENT bias",
            ),
        ]
        out, warnings = _enforce_file_writer_mcp_tool(gaps, [])
        assert out == gaps  # identity, no mutation
        assert warnings == []

    def test_empty_gaps_noop(self):
        out, warnings = _enforce_file_writer_mcp_tool([], ["output.csv"])
        assert out == []
        assert warnings == []

    def test_writer_classified_agent_gets_overridden(self):
        """Parent-task bias can fold a writer gap into AGENT."""
        gaps = [
            ClassifiedGap(
                query="incident-triage-agent",
                description="incident-triage-agent",
                classified_type=CapabilityType.AGENT,
                confidence=0.95,
                rationale="Parent is agent-task",
            ),
            ClassifiedGap(
                query="write-markdown-report",
                description="write-markdown-report",
                classified_type=CapabilityType.AGENT,
                confidence=0.9,
                rationale="Parent is agent-task",
            ),
        ]
        out, warnings = _enforce_file_writer_mcp_tool(gaps, ["report.md"])
        assert len(warnings) == 1
        assert "write-markdown-report" in warnings[0]
        assert "mcp_tool" in warnings[0]
        # Investigator stays AGENT.
        assert out[0].classified_type == CapabilityType.AGENT
        # Writer overridden to MCP_TOOL.
        assert out[1].classified_type == CapabilityType.MCP_TOOL
        # Rationale carries the override audit trail.
        assert "[file-writer override]" in out[1].rationale
        assert "Parent is agent-task" in out[1].rationale  # original preserved

    def test_writer_classified_skill_gets_overridden(self):
        gaps = [
            ClassifiedGap(
                query="write-csv-file",
                description="write-csv-file",
                classified_type=CapabilityType.SKILL,
                confidence=0.8,
                rationale="Parent is skill-task",
            ),
        ]
        out, warnings = _enforce_file_writer_mcp_tool(gaps, ["output.csv"])
        assert len(warnings) == 1
        assert out[0].classified_type == CapabilityType.MCP_TOOL

    def test_writer_already_mcp_tool_noop(self):
        """A writer already classified as MCP_TOOL is left unchanged."""
        gaps = [
            ClassifiedGap(
                query="write-xlsx-file",
                description="write-xlsx-file",
                classified_type=CapabilityType.MCP_TOOL,
                confidence=0.95,
                rationale="Deterministic file I/O",
            ),
        ]
        out, warnings = _enforce_file_writer_mcp_tool(gaps, ["output.xlsx"])
        assert warnings == []
        assert out[0].classified_type == CapabilityType.MCP_TOOL
        # Rationale unchanged; no override note added.
        assert "override" not in out[0].rationale.lower()

    def test_non_writer_gap_not_touched(self):
        """Non-writer gaps (any classification) are left alone."""
        gaps = [
            ClassifiedGap(
                query="parse-sales-csv",
                description="parse-sales-csv",
                classified_type=CapabilityType.MCP_TOOL,
                confidence=0.95,
                rationale="Deterministic parsing",
            ),
            ClassifiedGap(
                query="summarize-findings",
                description="summarize-findings",
                classified_type=CapabilityType.SKILL,
                confidence=0.8,
                rationale="Domain expertise",
            ),
        ]
        out, warnings = _enforce_file_writer_mcp_tool(gaps, ["output.csv"])
        assert warnings == []
        assert out[0].classified_type == CapabilityType.MCP_TOOL
        assert out[1].classified_type == CapabilityType.SKILL

    def test_case_insensitive_write_prefix(self):
        gaps = [
            ClassifiedGap(
                query="WRITE-XLSX-FILE",  # uppercase
                description="WRITE-XLSX-FILE",
                classified_type=CapabilityType.AGENT,
                confidence=0.9,
                rationale="AGENT bias",
            ),
        ]
        out, warnings = _enforce_file_writer_mcp_tool(gaps, ["output.xlsx"])
        assert len(warnings) == 1
        assert out[0].classified_type == CapabilityType.MCP_TOOL


class TestClassifierNodeWriterOverride:
    """classifier_node wires the file-writer override correctly."""

    @patch("src.orchestrator.classifier.classify_gap")
    def test_override_fires_when_contract_declares_files(self, mock_classify):
        """End-to-end: classifier LLM routes writer to AGENT, override fixes it."""
        mock_classify.side_effect = [
            ClassifiedGap(
                query="incident-triage-agent",
                description="incident-triage-agent",
                classified_type=CapabilityType.AGENT,
                confidence=0.95,
                rationale="Parent is agent-task",
            ),
            ClassifiedGap(
                query="write-markdown-report",
                description="write-markdown-report",
                classified_type=CapabilityType.AGENT,
                confidence=0.9,
                rationale="Parent is agent-task",
            ),
        ]
        state = TaskState(
            task_description="Investigate and write report.md",
            required_capabilities=["incident-triage-agent", "write-markdown-report"],
            gaps=[
                GapItem(query="incident-triage-agent", description="incident-triage-agent"),
                GapItem(query="write-markdown-report", description="write-markdown-report"),
            ],
            classified_gaps=[],
            output_contract=OutputContract(required_files=["report.md"]),
        )

        cmd = classifier_node(state)
        out = cmd.update["classified_gaps"]
        assert len(out) == 2
        assert out[0].classified_type == CapabilityType.AGENT  # investigator
        assert out[1].classified_type == CapabilityType.MCP_TOOL  # writer overridden
        # Warning surfaced in errors.
        errors = cmd.update.get("errors", [])
        assert any("write-markdown-report" in e for e in errors)

    @patch("src.orchestrator.classifier.classify_gap")
    def test_no_override_when_contract_has_no_files(self, mock_classify):
        """Without required_files, the override is inert."""
        mock_classify.return_value = ClassifiedGap(
            query="write-csv-file",
            description="write-csv-file",
            classified_type=CapabilityType.AGENT,
            confidence=0.9,
            rationale="AGENT bias",
        )
        state = TaskState(
            task_description="Some task without file output",
            required_capabilities=["write-csv-file"],
            gaps=[GapItem(query="write-csv-file", description="write-csv-file")],
            classified_gaps=[],
            # No output_contract, so the override cannot fire.
        )

        cmd = classifier_node(state)
        out = cmd.update["classified_gaps"]
        assert out[0].classified_type == CapabilityType.AGENT  # not overridden
