"""Integration tests for the task-level capability classifier.

These tests invoke the real LLM (openai:gpt-5-mini) to verify that the
classification prompt produces correct, reproducible task-level classifications
with ``temperature=0``.

Requires OPENAI_API_KEY environment variable to be set.
"""

from __future__ import annotations

import os

import pytest

from src.orchestrator.classifier import classify_task
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap

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


class TestClassifyTaskLive:
    def test_classify_mcp_tool(self):
        """A clear atomic operation should classify as MCP_TOOL."""
        task = (
            "Parse a JSON configuration file and return a dictionary mapping "
            "section names to their key-value pairs."
        )
        result = classify_task(task, required_capabilities=["parse-json", "extract-sections"])

        assert isinstance(result, ClassifiedGap)
        assert result.classified_type == CapabilityType.MCP_TOOL
        assert result.confidence >= 0.7
        assert len(result.rationale) > 0

    def test_classify_skill(self):
        """A multi-step domain workflow should classify as SKILL."""
        task = (
            "Build an inventory reorder pipeline using an inventory CSV and "
            "a reorder rules YAML. Compute days of stock for each product, "
            "identify items below the reorder threshold, compute reorder "
            "quantities capped at max_order_value, and sum the total cost."
        )
        result = classify_task(
            task,
            required_capabilities=[
                "parse-inventory",
                "apply-reorder-rules",
                "compute-cost",
            ],
        )

        assert isinstance(result, ClassifiedGap)
        assert result.classified_type == CapabilityType.SKILL
        assert result.confidence >= 0.7
        assert len(result.rationale) > 0

    def test_classify_agent(self):
        """An open-ended investigation task should classify as AGENT."""
        task = (
            "Investigate a service degradation incident. Examine metrics "
            "around the onset time, identify which metric changed first "
            "(root cause), and determine the cascade chain by comparing "
            "onset times across metrics. Requires adaptive investigation "
            "where the path depends on intermediate findings."
        )
        result = classify_task(
            task,
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
        """All clear-cut task examples should have confidence >= 0.7."""
        clear_cut_tasks = [
            "Compute the SHA-256 hash of a string and return the hex digest.",
            (
                "Build an ETL pipeline that extracts sales data from CSV, "
                "transforms it with currency normalization and validation "
                "rules, and loads it into a cleaned output file."
            ),
            (
                "Autonomously monitor system logs, detect failure patterns, "
                "investigate their root causes by following leads across "
                "related log sources, and adapt the search strategy based on "
                "what is discovered."
            ),
        ]
        for task in clear_cut_tasks:
            result = classify_task(task, required_capabilities=[])
            assert result.confidence >= 0.7, (
                f"Task {task[:40]!r} classified as "
                f"{result.classified_type} with low confidence "
                f"{result.confidence}: {result.rationale}"
            )
