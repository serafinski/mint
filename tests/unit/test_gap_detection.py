"""Unit tests for the gap detection LangGraph node."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.orchestrator.gap_detection import gap_detection_node
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GapItem, MatchItem, TaskState, TaskStatus


def _make_descriptor(name: str = "test-tool") -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name=name,
        description=f"A test capability: {name}",
        capability_type=CapabilityType.MCP_TOOL,
        domain="testing",
    )


def _make_registry(
    *,
    matches: list[tuple[CapabilityDescriptor, float, str]] | None = None,
    gaps: list[tuple[str, str]] | None = None,
) -> MagicMock:
    registry = MagicMock()
    registry.detect_gaps.return_value = (matches or [], gaps or [])
    return registry


# ---------------------------------------------------------------------------
# All matches (no gaps)
# ---------------------------------------------------------------------------


def test_all_matches_populates_matches_empty_gaps():
    cap = _make_descriptor("fibonacci")
    registry = _make_registry(
        matches=[(cap, 0.95, "compute fibonacci")],
    )
    state = TaskState(
        task_description="compute fibonacci",
        required_capabilities=["compute fibonacci"],
    )

    cmd = gap_detection_node(state, registry)

    assert cmd.goto == "classifier_node"
    assert cmd.update["status"] == TaskStatus.DETECTING_GAPS
    assert len(cmd.update["matches"]) == 1
    assert len(cmd.update["gaps"]) == 0
    match = cmd.update["matches"][0]
    assert isinstance(match, MatchItem)
    assert match.capability.name == "fibonacci"
    assert match.score == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# All gaps (no matches)
# ---------------------------------------------------------------------------


def test_all_gaps_populates_gaps_empty_matches():
    registry = _make_registry(
        gaps=[("translate text to Klingon", "similarity 0.12 below threshold 0.3")],
    )
    state = TaskState(
        task_description="translate Klingon",
        required_capabilities=["translate text to Klingon"],
    )

    cmd = gap_detection_node(state, registry)

    assert cmd.goto == "classifier_node"
    assert len(cmd.update["gaps"]) == 1
    assert len(cmd.update["matches"]) == 0
    gap = cmd.update["gaps"][0]
    assert isinstance(gap, GapItem)
    assert gap.query == "translate text to Klingon"
    assert gap.description == "translate text to Klingon"


# ---------------------------------------------------------------------------
# Mixed (some matches, some gaps)
# ---------------------------------------------------------------------------


def test_mixed_results():
    cap = _make_descriptor("csv-parser")
    registry = _make_registry(
        matches=[(cap, 0.88, "parse CSV file")],
        gaps=[("train ML model", "similarity 0.05 below threshold 0.3")],
    )
    state = TaskState(
        task_description="parse CSV and train model",
        required_capabilities=["parse CSV file", "train ML model"],
    )

    cmd = gap_detection_node(state, registry)

    assert len(cmd.update["matches"]) == 1
    assert len(cmd.update["gaps"]) == 1
    assert cmd.update["matches"][0].capability.name == "csv-parser"
    assert cmd.update["gaps"][0].query == "train ML model"


# ---------------------------------------------------------------------------
# Empty required capabilities
# ---------------------------------------------------------------------------


def test_empty_required_capabilities():
    registry = _make_registry()
    state = TaskState(task_description="do nothing", required_capabilities=[])

    cmd = gap_detection_node(state, registry)

    assert cmd.goto == "classifier_node"
    assert cmd.update["matches"] == []
    assert cmd.update["gaps"] == []
    registry.detect_gaps.assert_called_once_with([])


# ---------------------------------------------------------------------------
# Registry is called with correct queries
# ---------------------------------------------------------------------------


def test_passes_required_capabilities_to_registry():
    registry = _make_registry()
    state = TaskState(
        task_description="multi-step task",
        required_capabilities=["capability A", "capability B", "capability C"],
    )

    gap_detection_node(state, registry)

    registry.detect_gaps.assert_called_once_with(["capability A", "capability B", "capability C"])


# ---------------------------------------------------------------------------
# Always routes to classifier_node regardless of gap/match ratio
# ---------------------------------------------------------------------------


def test_routes_to_classifier_when_only_matches():
    cap = _make_descriptor()
    registry = _make_registry(matches=[(cap, 0.9, "q")])
    state = TaskState(task_description="t", required_capabilities=["q"])
    cmd = gap_detection_node(state, registry)
    assert cmd.goto == "classifier_node"


def test_routes_to_classifier_when_only_gaps():
    registry = _make_registry(gaps=[("q", "reason")])
    state = TaskState(task_description="t", required_capabilities=["q"])
    cmd = gap_detection_node(state, registry)
    assert cmd.goto == "classifier_node"
