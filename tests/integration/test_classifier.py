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

    def test_classify_skill_subtask_per_gap(self):
        """Per-gap classification (modern primary path) of a SKILL-shaped subtask.

        Replaces the deleted ``test_classify_skill`` (root-cause
        traced 2026-04-21):

        The deleted test asked the LEGACY ``classify_task`` wrapper
        to whole-task-classify the multi-step *"Build an inventory
        reorder pipeline ... compute days of stock ... sum total
        cost"* prompt as SKILL. That made sense in the **pre-Stage-2
        world** (commit ``cab813a``, 2026-04-13) where the classifier
        ran ONCE per task. Stage 2 pivoted to **per-gap
        classification** — multi-step tasks are now decomposed FIRST,
        then each subtask is independently classified via
        ``classify_gap``. In production the inventory-reorder task
        would be decomposed into three deterministic-compute gaps
        (parse, apply rules, sum), each correctly classifying as
        MCP_TOOL. There is no longer a "whole task is a SKILL"
        classification step in the production path for multi-step
        tasks.

        Empirical confirmation (2026-04-21 reproduction):
        ``classify_task`` on a SKILL-shaped prompt now returns either
        ``MCP_TOOL`` (compute-pipeline anti-pattern, classifier prompt
        explicitly excludes those) OR ``AGENT`` (multi-step + judgment
        + adaptation language, which the prompt rewards). The legacy
        wrapper has no robust SKILL niche left for non-trivial
        prompts. Coverage of the legacy single-gap fallback wrapper is
        already provided indirectly by ``test_classify_mcp_tool`` /
        ``test_classify_agent`` which use trivial single-purpose
        prompts that the wrapper still handles cleanly.
        """
        from src.orchestrator.classifier import classify_gap
        from src.schemas.task import GapItem

        # Parent task is procedural-with-domain-expertise. The
        # subtask is the "apply rubric" step — clearly SKILL because
        # the rubric application is per-team domain expertise, not
        # raw compute.
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
