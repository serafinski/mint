"""Unit tests for the per-gap capability classifier.

LLM-calling behaviour is covered by ``tests/integration/test_classifier.py``
with real OpenAI calls.  These tests cover schema validation and the
classifier node's routing logic (patching ``classify_gap``, not the LLM).

As of Stage 2 of the compositional refactor, ``classifier_node`` iterates
over ``state.gaps`` and calls ``classify_gap`` per gap. The legacy
``classify_task`` wrapper is retained for single-gap / whole-task callers
and is tested separately at the bottom of this file.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.classifier import (
    _normalize_capability_name,
    _slug_from_task,
    classifier_node,
    classify_gap,
    classify_task,
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
        """capability_name is a required field — missing it must raise."""
        with pytest.raises(ValueError):
            ClassificationResult(
                classified_type=CapabilityType.MCP_TOOL,
                confidence=0.9,
                rationale="test",
            )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# _slug_from_task — deterministic slug generation
# ---------------------------------------------------------------------------


class TestSlugFromTask:
    def test_deterministic(self):
        """Same task description always produces the same slug."""
        desc = "Parse a CSV file and count rows"
        assert _slug_from_task(desc) == _slug_from_task(desc)

    def test_different_tasks_different_slugs(self):
        """Different task descriptions produce different slugs."""
        a = _slug_from_task("Parse a CSV file")
        b = _slug_from_task("Train a machine-learning model")
        assert a != b

    def test_slug_format(self):
        """Slug is hyphen-separated lowercase with a short hash suffix."""
        slug = _slug_from_task("Parse a CSV file")
        assert slug.startswith("parse-a-csv-file")
        # 6-char hex suffix at the end
        assert len(slug.split("-")[-1]) == 6

    def test_empty_task_falls_back_to_task(self):
        """Empty or punctuation-only tasks fall back to 'task' as the base."""
        slug = _slug_from_task("...")
        assert slug.startswith("task-")


# ---------------------------------------------------------------------------
# _normalize_capability_name — LLM-chosen name validation + normalization
# ---------------------------------------------------------------------------


class TestNormalizeCapabilityName:
    """Regression tests for the LLM-chosen capability name pipeline.

    Before this change, capability names were derived from the first 40
    chars of the task description, producing ugly names like
    ``parse_this_xml_report_data_and_find_the__6b415a`` that:
    1. Were cosmetically poor (mid-word truncation)
    2. Risked registry over-matching on surface text (since the name was
       literally a task-description prefix)
    3. Confused the LLM in future tasks (same prefix = perceived reuse)

    The fix: classifier returns a semantic kebab-case name in its
    structured output. ``_normalize_capability_name`` validates and
    appends a hash suffix for collision safety.
    """

    def test_clean_kebab_case_preserved(self):
        """A well-formed LLM name is accepted and gets a hash suffix."""
        result = _normalize_capability_name(
            "xml-revenue-extractor", "Parse this XML"
        )
        assert result.startswith("xml-revenue-extractor-")
        assert len(result.split("-")[-1]) == 6

    def test_snake_case_converted_to_kebab(self):
        """LLM names with underscores are normalized to hyphens."""
        result = _normalize_capability_name(
            "xml_revenue_extractor", "task desc"
        )
        assert result.startswith("xml-revenue-extractor-")

    def test_mixed_separators_normalized(self):
        """Spaces, underscores, and other separators collapse to hyphens."""
        result = _normalize_capability_name(
            "XML revenue Extractor!", "task desc"
        )
        assert result.startswith("xml-revenue-extractor-")

    def test_leading_trailing_hyphens_stripped(self):
        result = _normalize_capability_name(
            "---xml-extractor---", "task desc"
        )
        assert result.startswith("xml-extractor-")
        assert not result.startswith("-")

    def test_uppercase_lowercased(self):
        result = _normalize_capability_name("XML-Extractor", "task desc")
        assert result.startswith("xml-extractor-")

    def test_deterministic_hash_suffix(self):
        """Same task description produces the same hash suffix (for retry idempotency)."""
        task = "Parse XML report data"
        a = _normalize_capability_name("xml-parser", task)
        b = _normalize_capability_name("xml-parser", task)
        assert a == b

    def test_different_tasks_different_suffixes(self):
        """Same capability name but different tasks get different suffixes."""
        a = _normalize_capability_name("xml-parser", "task one")
        b = _normalize_capability_name("xml-parser", "task two")
        assert a != b

    def test_empty_name_falls_back_to_slug(self):
        """Empty or whitespace-only LLM name falls back to _slug_from_task."""
        result = _normalize_capability_name("", "Parse this XML file")
        # Should get the ugly slug from task description
        assert result.startswith("parse-this-xml-file-")

    def test_whitespace_only_name_falls_back(self):
        result = _normalize_capability_name("   ", "Parse this XML file")
        assert result.startswith("parse-this-xml-file-")

    def test_punctuation_only_falls_back(self):
        result = _normalize_capability_name("!!!---!!!", "Parse this XML file")
        assert result.startswith("parse-this-xml-file-")

    def test_overly_long_name_truncated_at_hyphen_boundary(self):
        """Names over 40 chars are truncated at a hyphen boundary."""
        # 50-char name
        long_name = "a-very-long-capability-name-that-exceeds-the-limit"
        result = _normalize_capability_name(long_name, "task desc")
        # The part before the hash should be <= 40 chars and end cleanly
        base = "-".join(result.split("-")[:-1])
        assert len(base) <= 40
        assert not base.endswith("-")

    def test_all_caps_no_separators(self):
        result = _normalize_capability_name("EXTRACTOR", "task desc")
        assert result.startswith("extractor-")

    def test_numeric_digits_allowed(self):
        result = _normalize_capability_name(
            "top-5-extractor", "task desc"
        )
        assert result.startswith("top-5-extractor-")


# ---------------------------------------------------------------------------
# classify_task — LLM success and failure paths (mocking _get_llm)
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


class TestClassifyTask:
    def test_success_returns_classified_gap(self):
        """Successful LLM call produces a ClassifiedGap using the LLM's
        chosen capability name (with a hash suffix for collision safety)."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.95,
            rationale="Deterministic parse",
            capability_name="csv-row-column-summarizer",
        )
        task_desc = "Parse a CSV file and return row count"

        with patch(
            "src.orchestrator.classifier._get_llm",
            return_value=_make_mock_llm(mock_response),
        ):
            result = classify_task(task_desc, required_capabilities=["parse-csv"])

        assert isinstance(result, ClassifiedGap)
        assert result.classified_type == CapabilityType.MCP_TOOL
        assert result.confidence == 0.95
        assert task_desc[:40] in result.description
        # query uses the LLM's semantic name + hash suffix
        assert result.query.startswith("csv-row-column-summarizer-")
        assert len(result.query.split("-")[-1]) == 6

    def test_llm_failure_falls_back_to_mcp_tool(self):
        """When LLM raises any exception, classify_task returns MCP_TOOL with confidence 0.3."""
        task_desc = "Parse a CSV file"

        with patch(
            "src.orchestrator.classifier._get_llm",
            return_value=_make_mock_llm(raises=RuntimeError("API error")),
        ):
            result = classify_task(task_desc, required_capabilities=[])

        assert result.classified_type == CapabilityType.MCP_TOOL
        assert result.confidence == 0.3
        assert "LLM classification failed" in result.rationale

    def test_llm_failure_rationale_contains_error(self):
        """Fallback rationale includes the original exception message."""
        task_desc = "Parse a CSV file"

        with patch(
            "src.orchestrator.classifier._get_llm",
            return_value=_make_mock_llm(raises=ValueError("rate limit exceeded")),
        ):
            result = classify_task(task_desc, required_capabilities=[])

        assert "rate limit exceeded" in result.rationale

    def test_uses_structured_output_with_classification_result(self):
        """classify_task calls with_structured_output(ClassificationResult)."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.SKILL,
            confidence=0.8,
            rationale="Multi-step workflow",
            capability_name="multi-step-processor",
        )
        mock_llm = _make_mock_llm(mock_response)

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_task("Some task", required_capabilities=["cap-a"])

        mock_llm.with_structured_output.assert_called_once_with(ClassificationResult)

    def test_invoke_called_without_explicit_callbacks(self):
        """classify_task does not pass a per-node callbacks config to structured_llm.invoke.

        The pipeline-level LangFuse CallbackHandler propagates automatically via
        LangChain's contextvar inheritance — no per-node handler is needed.
        """
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="test",
            capability_name="test-cap",
        )
        mock_llm = _make_mock_llm(mock_response)

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_task("Some task", required_capabilities=[])

        invoke_call = mock_llm.with_structured_output.return_value.invoke
        args, kwargs = invoke_call.call_args
        assert "config" not in kwargs

    def test_required_capabilities_included_in_prompt(self):
        """classify_task passes required_capabilities into the prompt."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="Single function",
            capability_name="csv-parser",
        )
        mock_llm = _make_mock_llm(mock_response)

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_task(
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

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_task("Parse CSV", required_capabilities=[])

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

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            classify_task(long_desc, required_capabilities=[])

        invoke_call = mock_llm.with_structured_output.return_value.invoke
        prompt_text = invoke_call.call_args[0][0]
        # Task description (A x 1500) is included but not the full 5000
        assert "A" * 1500 in prompt_text
        assert "A" * 1501 not in prompt_text

    def test_task_description_truncated_in_classified_gap(self):
        """ClassifiedGap.description is capped at 500 chars."""
        mock_response = ClassificationResult(
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="test",
            capability_name="bulk-processor",
        )
        mock_llm = _make_mock_llm(mock_response)
        long_desc = "B" * 2000

        with patch("src.orchestrator.classifier._get_llm", return_value=mock_llm):
            result = classify_task(long_desc, required_capabilities=[])

        assert len(result.description) == 500


# ---------------------------------------------------------------------------
# Classifier node — routing logic (patches classify_task, not the LLM)
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
        assert cmd.update["classified_gaps"] == [] if "classified_gaps" in cmd.update else True
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

    def test_fallback_classification_added_to_errors(self):
        """When classify_gap returns a fallback result, node records it in state.errors."""
        state = TaskState(
            task_description="Parse CSV",
            required_capabilities=["parse-csv"],
            gaps=[GapItem(description="parse-csv", query="parse-csv")],
        )

        fallback = ClassifiedGap(
            description="parse-csv",
            query="parse-csv-abcdef",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.3,
            rationale="LLM classification failed (API error); defaulting to MCP_TOOL",
        )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            return_value=fallback,
        ):
            cmd = classifier_node(state)

        assert len(cmd.update.get("errors", [])) == 1
        assert "Classification fallback" in cmd.update["errors"][0]

    def test_fallback_classification_still_produces_classified_gap(self):
        """A fallback classification is still included in classified_gaps."""
        state = TaskState(
            task_description="Parse CSV",
            required_capabilities=["parse-csv"],
            gaps=[GapItem(description="parse-csv", query="parse-csv")],
        )

        fallback = ClassifiedGap(
            description="parse-csv",
            query="parse-csv-abcdef",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.3,
            rationale="LLM classification failed (API error); defaulting to MCP_TOOL",
        )

        with patch(
            "src.orchestrator.classifier.classify_gap",
            return_value=fallback,
        ):
            cmd = classifier_node(state)

        assert len(cmd.update["classified_gaps"]) == 1
        assert cmd.update["classified_gaps"][0].classified_type == CapabilityType.MCP_TOOL

    def test_preserves_existing_errors(self):
        """classifier_node propagates pre-existing errors from state."""
        state = TaskState(
            task_description="Test",
            required_capabilities=[],
            gaps=[],
            errors=["pre-existing error from decomposition"],
        )

        cmd = classifier_node(state)

        # No-op path still propagates errors — when gaps is empty the node
        # just sets status without touching errors, so they remain in state.
        # We verify via the update dict having no overwrite of errors field.
        assert "errors" not in cmd.update or "pre-existing error from decomposition" in cmd.update.get("errors", [])

    def test_classify_gap_unhandled_exception_propagates(self):
        """Unexpected exceptions raised by classify_gap propagate from classifier_node.

        classify_gap has comprehensive exception handling and should not
        normally raise. This test documents the current behaviour for the
        edge case where an exception does escape classify_gap.
        """
        state = TaskState(
            task_description="Test",
            required_capabilities=["cap"],
            gaps=[GapItem(description="cap", query="cap")],
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
# File-writer override — §12 finding #11 safety net
# ---------------------------------------------------------------------------


class TestEnforceFileWriterMcpTool:
    """Direct unit tests for the override helper."""

    def test_no_required_files_noop(self):
        from src.orchestrator.classifier import _enforce_file_writer_mcp_tool

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
        assert out == gaps  # identity — no mutation
        assert warnings == []

    def test_empty_gaps_noop(self):
        from src.orchestrator.classifier import _enforce_file_writer_mcp_tool

        out, warnings = _enforce_file_writer_mcp_tool([], ["output.csv"])
        assert out == []
        assert warnings == []

    def test_writer_classified_agent_gets_overridden(self):
        """The T16 drift case — parent-task bias folded writer into AGENT."""
        from src.orchestrator.classifier import _enforce_file_writer_mcp_tool

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
        assert "Step 12 finding #11 override" in out[1].rationale
        assert "Parent is agent-task" in out[1].rationale  # original preserved

    def test_writer_classified_skill_gets_overridden(self):
        from src.orchestrator.classifier import _enforce_file_writer_mcp_tool

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
        """The T11 happy path — writer correctly classified; don't touch."""
        from src.orchestrator.classifier import _enforce_file_writer_mcp_tool

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
        # Rationale unchanged — no override note added.
        assert "override" not in out[0].rationale.lower()

    def test_non_writer_gap_not_touched(self):
        """Non-writer gaps (any classification) are left alone."""
        from src.orchestrator.classifier import _enforce_file_writer_mcp_tool

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
        from src.orchestrator.classifier import _enforce_file_writer_mcp_tool

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


class TestClassifierNodeWritererOverride:
    """Integration-style — classifier_node wires the override correctly."""

    @patch("src.orchestrator.classifier.classify_gap")
    def test_override_fires_when_contract_declares_files(self, mock_classify):
        """End-to-end: classifier LLM routes writer to AGENT, override fixes it."""
        from src.schemas.task import OutputContract

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
            # No output_contract — override can't fire.
        )

        cmd = classifier_node(state)
        out = cmd.update["classified_gaps"]
        assert out[0].classified_type == CapabilityType.AGENT  # not overridden
