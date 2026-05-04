"""Integration tests for the per-gap capability classifier.

These tests invoke the real LLM (openai:gpt-5-mini) to verify that the
classification prompt produces correct, reproducible per-gap classifications
with ``temperature=0``.

Requires OPENAI_API_KEY environment variable to be set.
"""

from __future__ import annotations

import os

import pytest

from src.orchestrator.classifier import classify_gap
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap, GapItem

_has_api_key = bool(os.environ.get("OPENAI_API_KEY"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _has_api_key, reason="OPENAI_API_KEY not set"),
]


@pytest.fixture(autouse=True)
def _reset_classifier_llm():
    """Reset the lazy-init LLM singleton so each test starts clean."""
    import src.orchestrator.classifier as mod

    mod._classifier_llm = None
    yield
    mod._classifier_llm = None


class TestClassifyGapLive:
    def test_classify_mcp_tool(self):
        """A clear atomic gap should classify as MCP_TOOL."""
        task = (
            "Parse a JSON configuration file and return a dictionary mapping "
            "section names to their key-value pairs."
        )
        gap = GapItem(
            query="parse-json-sections",
            description=(
                "Parse the JSON configuration file and extract section names "
                "with their key-value pairs."
            ),
        )
        result = classify_gap(
            gap=gap,
            task_description=task,
            required_capabilities=["parse-json", "extract-sections"],
        )

        assert isinstance(result, ClassifiedGap)
        assert result.classified_type == CapabilityType.MCP_TOOL
        assert result.confidence >= 0.7
        assert len(result.rationale) > 0

    def test_classify_skill_subtask_per_gap(self):
        """A domain-procedure gap should classify as SKILL."""
        parent_task = (
            "Run a structured data quality review following our team's "
            "data-cleaning SOP: profile the dataset against our quality "
            "rubric, identify violations per the rule library, write a "
            "report using the QA template, and produce escalation "
            "recommendations per the approved patterns."
        )
        gap = GapItem(
            query="apply-team-quality-rubric",
            description=(
                "Walk the dataset through the team's quality rubric "
                "(completeness, consistency, validity) following the QA "
                "playbook's prescribed step ordering. For each rule, "
                "apply the team's standard severity-tagging convention "
                "and use the approved finding-template phrasing."
            ),
        )
        result = classify_gap(
            gap=gap,
            task_description=parent_task,
            required_capabilities=[
                "profile-against-rubric",
                "apply-team-quality-rubric",
                "write-qa-report",
            ],
        )

        assert result is not None, (
            "classify_gap returned None — three-attempt retry budget exhausted"
        )
        assert isinstance(result, ClassifiedGap)
        assert result.classified_type == CapabilityType.SKILL, (
            f"Expected SKILL for SKILL-shaped per-gap subtask, got "
            f"{result.classified_type} (confidence {result.confidence}). "
            f"Rationale: {result.rationale}"
        )
        assert result.confidence >= 0.7

    def test_classify_agent(self):
        """An open-ended investigation gap should classify as AGENT."""
        task = (
            "Investigate a service degradation incident. Examine metrics "
            "around the onset time, identify which metric changed first "
            "(root cause), and determine the cascade chain by comparing "
            "onset times across metrics. Requires adaptive investigation "
            "where the path depends on intermediate findings."
        )
        gap = GapItem(
            query="investigate-service-degradation",
            description=(
                "Investigate the service degradation, decide which metrics "
                "to inspect next based on intermediate findings, and identify "
                "the root-cause cascade."
            ),
        )
        result = classify_gap(
            gap=gap,
            task_description=task,
            required_capabilities=[
                "profile-metrics",
                "root-cause-analysis",
                "cascade-investigation",
            ],
        )

        assert isinstance(result, ClassifiedGap)
        assert result.classified_type == CapabilityType.AGENT
        assert result.confidence >= 0.7
        assert len(result.rationale) > 0

    def test_confidence_above_threshold(self):
        """All clear-cut gap examples should have confidence >= 0.7."""
        clear_cut_cases = [
            (
                "Compute the SHA-256 hash of a string and return the hex digest.",
                GapItem(
                    query="compute-sha256-hash",
                    description="Compute the SHA-256 hash and return the hex digest.",
                ),
                ["compute-sha256-hash"],
            ),
            (
                "Apply the team's invoice reconciliation SOP to flag mismatches "
                "using the approved policy thresholds.",
                GapItem(
                    query="apply-reconciliation-policy",
                    description=(
                        "Apply the approved invoice reconciliation policy "
                        "thresholds and flag mismatches using the team's SOP."
                    ),
                ),
                ["apply-reconciliation-policy"],
            ),
            (
                "Autonomously monitor system logs, detect failure patterns, "
                "investigate their root causes by following leads across "
                "related log sources, and adapt the search strategy based on "
                "what is discovered.",
                GapItem(
                    query="investigate-log-failures",
                    description=(
                        "Investigate related log sources, follow leads from "
                        "intermediate findings, and adapt the search strategy."
                    ),
                ),
                ["investigate-log-failures"],
            ),
        ]
        for task, gap, required_capabilities in clear_cut_cases:
            result = classify_gap(
                gap=gap,
                task_description=task,
                required_capabilities=required_capabilities,
            )
            assert result is not None
            assert result.confidence >= 0.7, (
                f"Gap {gap.query!r} classified as "
                f"{result.classified_type} with low confidence "
                f"{result.confidence}: {result.rationale}"
            )
