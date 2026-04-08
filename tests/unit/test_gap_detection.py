"""Unit tests for the gap detection LangGraph node.

Under task-level classification, gap_detection_node produces AT MOST ONE
placeholder ``GapItem`` — not N enumerated gaps. Per-capability registry
matching is preserved (populating ``state.matches`` for each match), only
the gap-output format changes.
"""

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
# All matches (no gaps → empty gap list, classifier becomes no-op)
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
    assert cmd.update["gaps"] == []
    match = cmd.update["matches"][0]
    assert isinstance(match, MatchItem)
    assert match.capability.name == "fibonacci"
    assert match.score == pytest.approx(0.95)


# ---------------------------------------------------------------------------
# All gaps (no matches → single placeholder gap, not N enumerated)
# ---------------------------------------------------------------------------


def test_all_gaps_produces_single_placeholder_gap():
    registry = _make_registry(
        gaps=[("translate text to Klingon", "similarity 0.12 below threshold 0.3")],
    )
    state = TaskState(
        task_description="translate text from English into Klingon",
        required_capabilities=["translate text to Klingon"],
    )

    cmd = gap_detection_node(state, registry)

    assert cmd.goto == "classifier_node"
    # Exactly one placeholder gap, regardless of how many raw gaps there were
    assert len(cmd.update["gaps"]) == 1
    assert len(cmd.update["matches"]) == 0
    gap = cmd.update["gaps"][0]
    assert isinstance(gap, GapItem)
    # Placeholder query
    assert gap.query == "task-level"
    # Description is (truncated) task description
    assert gap.description.startswith("translate text from English into Klingon")


def test_multiple_unmatched_capabilities_still_produce_one_placeholder():
    """Task with N unmatched capabilities produces ONE placeholder gap, not N."""
    registry = _make_registry(
        gaps=[
            ("capability-a", "below threshold"),
            ("capability-b", "below threshold"),
            ("capability-c", "below threshold"),
        ],
    )
    state = TaskState(
        task_description="multi-step task requiring three missing caps",
        required_capabilities=["capability-a", "capability-b", "capability-c"],
    )

    cmd = gap_detection_node(state, registry)

    assert len(cmd.update["gaps"]) == 1
    assert cmd.update["gaps"][0].query == "task-level"


# ---------------------------------------------------------------------------
# Mixed: some matches, some unmatched → matches preserved, one placeholder
# ---------------------------------------------------------------------------


def test_mixed_results_preserves_matches_and_produces_single_gap():
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

    # Per-capability match still recorded
    assert len(cmd.update["matches"]) == 1
    assert cmd.update["matches"][0].capability.name == "csv-parser"
    # Exactly one placeholder gap (not one per unmatched capability)
    assert len(cmd.update["gaps"]) == 1
    assert cmd.update["gaps"][0].query == "task-level"


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


# ---------------------------------------------------------------------------
# Error preservation
# ---------------------------------------------------------------------------


def test_preserves_existing_errors():
    """Errors accumulated by earlier nodes must be carried through."""
    registry = _make_registry(gaps=[("q", "reason")])
    state = TaskState(
        task_description="t",
        required_capabilities=["q"],
        errors=["decomposition timeout", "partial result"],
    )

    cmd = gap_detection_node(state, registry)

    assert cmd.update["errors"] == ["decomposition timeout", "partial result"]


def test_empty_errors_when_none_prior():
    registry = _make_registry()
    state = TaskState(task_description="t", required_capabilities=[])

    cmd = gap_detection_node(state, registry)

    assert cmd.update["errors"] == []


# ---------------------------------------------------------------------------
# MetricsStore invocation tracking (Fix 3)
# ---------------------------------------------------------------------------


def test_records_match_invocations_when_metrics_store_provided():
    """Matched capabilities should be recorded as invocations in MetricsStore."""
    cap = _make_descriptor("csv-parser")
    registry = _make_registry(matches=[(cap, 0.9, "parse CSV")])
    metrics_store = MagicMock()
    state = TaskState(
        task_description="parse CSV file",
        required_capabilities=["parse CSV"],
    )

    gap_detection_node(state, registry, metrics_store=metrics_store)

    metrics_store.record_invocation.assert_called_once()
    record = metrics_store.record_invocation.call_args[0][0]
    assert record.capability_id == "csv-parser"
    assert record.success is True
    assert record.token_cost == 0
    assert record.latency_ms == 0.0


def test_no_metrics_store_does_not_crash():
    """metrics_store=None should run without error (backwards-compat)."""
    cap = _make_descriptor("csv-parser")
    registry = _make_registry(matches=[(cap, 0.9, "parse CSV")])
    state = TaskState(
        task_description="parse CSV file",
        required_capabilities=["parse CSV"],
    )

    cmd = gap_detection_node(state, registry, metrics_store=None)
    assert cmd.goto == "classifier_node"


def test_gaps_not_recorded_as_invocations():
    """Gaps (no registry match) must not produce invocation records."""
    registry = _make_registry(gaps=[("train ML model", "no match")])
    metrics_store = MagicMock()
    state = TaskState(
        task_description="train model",
        required_capabilities=["train ML model"],
    )

    gap_detection_node(state, registry, metrics_store=metrics_store)

    metrics_store.record_invocation.assert_not_called()


def test_records_multiple_match_invocations():
    """When multiple capabilities match, each produces a distinct invocation."""
    cap_a = _make_descriptor("parser")
    cap_b = _make_descriptor("validator")
    registry = _make_registry(
        matches=[
            (cap_a, 0.9, "parse"),
            (cap_b, 0.85, "validate"),
        ],
    )
    metrics_store = MagicMock()
    state = TaskState(
        task_description="parse and validate",
        required_capabilities=["parse", "validate"],
    )

    gap_detection_node(state, registry, metrics_store=metrics_store)

    assert metrics_store.record_invocation.call_count == 2
    recorded_ids = {
        call.args[0].capability_id
        for call in metrics_store.record_invocation.call_args_list
    }
    assert recorded_ids == {"parser", "validator"}
