"""Integration tests for the capability classifier.

These tests invoke the real LLM (DeepSeek) to verify that the classification
prompt produces correct, reproducible results with ``temperature=0``.

Requires DEEPSEEK_API_KEY environment variable to be set.
"""

from __future__ import annotations

import os

import pytest

from src.orchestrator.classifier import classify_gap
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap, GapItem

_has_api_key = bool(os.environ.get("DEEPSEEK_API_KEY"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _has_api_key, reason="DEEPSEEK_API_KEY not set"),
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
        """A clear atomic operation should classify as MCP_TOOL."""
        gap = GapItem(
            description="Parse a JSON file and extract specific fields",
            query="parse JSON file",
        )
        result = classify_gap(gap)

        assert isinstance(result, ClassifiedGap)
        assert result.classified_type == CapabilityType.MCP_TOOL
        assert result.confidence >= 0.7
        assert len(result.rationale) > 0

    def test_classify_skill(self):
        """A multi-step workflow should classify as SKILL."""
        gap = GapItem(
            description=(
                "Multi-step data cleaning pipeline for financial records"
                " including validation, normalization, and deduplication"
            ),
            query="financial data cleaning pipeline",
        )
        result = classify_gap(gap)

        assert isinstance(result, ClassifiedGap)
        assert result.classified_type == CapabilityType.SKILL
        assert result.confidence >= 0.7
        assert len(result.rationale) > 0

    def test_classify_agent(self):
        """An autonomous reasoning task should classify as AGENT."""
        gap = GapItem(
            description=(
                "Autonomous agent that investigates anomalies in"
                " system logs and adapts its search strategy"
                " based on findings"
            ),
            query="adaptive anomaly investigation agent",
        )
        result = classify_gap(gap)

        assert isinstance(result, ClassifiedGap)
        assert result.classified_type == CapabilityType.AGENT
        assert result.confidence >= 0.7
        assert len(result.rationale) > 0

    def test_confidence_above_threshold(self):
        """All clear-cut examples should have confidence >= 0.7."""
        clear_cut_gaps = [
            GapItem(
                description="Convert temperature between Celsius and Fahrenheit",
                query="convert temperature units",
            ),
            GapItem(
                description="ETL pipeline that extracts, transforms, and loads sales data",
                query="ETL sales data pipeline",
            ),
            GapItem(
                description="Autonomous monitoring agent that detects and responds to failures",
                query="autonomous system monitoring agent",
            ),
        ]
        for gap in clear_cut_gaps:
            result = classify_gap(gap)
            assert result.confidence >= 0.7, (
                f"Gap {gap.query!r} classified as"
                f" {result.classified_type} with low confidence"
                f" {result.confidence}: {result.rationale}"
            )
