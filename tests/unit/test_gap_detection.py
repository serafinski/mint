"""Unit tests for the gap detection LangGraph node.

``gap_detection_node`` produces N enumerated ``GapItem``s, one per unmatched
required capability, so the downstream per-gap classifier can classify each
step independently within the context of the whole task. Per-capability
registry matching is preserved by populating ``state.matches`` for each match.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.orchestrator.gap_detection import gap_detection_node
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GapItem, MatchItem, TaskState, TaskStatus


def _make_descriptor(
    name: str = "test-tool",
    *,
    capability_type: CapabilityType = CapabilityType.MCP_TOOL,
    input_schema: dict[str, str] | None = None,
    output_schema: dict[str, str] | None = None,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name=name,
        description=f"A test capability: {name}",
        capability_type=capability_type,
        domain="testing",
        input_schema=input_schema or {},
        output_schema=output_schema or {},
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
# All gaps (no matches → one enumerated gap per unmatched capability)
# ---------------------------------------------------------------------------


def test_all_gaps_produces_one_enumerated_gap():
    registry = _make_registry(
        gaps=[("translate text to Klingon", "similarity 0.12 below threshold 0.3")],
    )
    state = TaskState(
        task_description="translate text from English into Klingon",
        required_capabilities=["translate text to Klingon"],
    )

    cmd = gap_detection_node(state, registry)

    assert cmd.goto == "classifier_node"
    # One enumerated gap per unmatched capability
    assert len(cmd.update["gaps"]) == 1
    assert len(cmd.update["matches"]) == 0
    gap = cmd.update["gaps"][0]
    assert isinstance(gap, GapItem)
    # Query is the unmatched capability name
    assert gap.query == "translate text to Klingon"
    assert gap.description == "translate text to Klingon"


def test_multiple_unmatched_capabilities_produce_n_enumerated_gaps():
    """Task with N unmatched capabilities produces N enumerated gaps, not 1."""
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

    assert len(cmd.update["gaps"]) == 3
    queries = [g.query for g in cmd.update["gaps"]]
    assert queries == ["capability-a", "capability-b", "capability-c"]


# ---------------------------------------------------------------------------
# Mixed: some matches, some unmatched → matches preserved, one gap per unmatched
# ---------------------------------------------------------------------------


def test_mixed_results_preserves_matches_and_produces_n_gaps():
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
    # One enumerated gap for the unmatched capability
    assert len(cmd.update["gaps"]) == 1
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
    registry.detect_gaps.assert_called_once_with(
        [], auxiliary_queries=[], domain=None, include_generic=True
    )


# ---------------------------------------------------------------------------
# Registry is called with correct queries
# ---------------------------------------------------------------------------


def test_passes_required_capabilities_to_registry():
    """Slugs are threaded to detect_gaps. When no subtasks carry descriptions
    (empty subtasks list, fallback path), auxiliary_queries is a list of Nones
    so single-query retrieval is preserved.
    """
    registry = _make_registry()
    state = TaskState(
        task_description="multi-step task",
        required_capabilities=["capability A", "capability B", "capability C"],
    )

    gap_detection_node(state, registry)

    registry.detect_gaps.assert_called_once_with(
        ["capability A", "capability B", "capability C"],
        auxiliary_queries=[None, None, None],
        domain=None,
        include_generic=True,
    )


# ---------------------------------------------------------------------------
# Dual-query retrieval wiring: gap_detection_node builds
# auxiliary_queries from state.subtasks descriptions
# ---------------------------------------------------------------------------


def test_dual_query_wires_subtask_descriptions_as_auxiliary():
    """When state.subtasks is populated, each slug's auxiliary is the
    description of the subtask whose required_capabilities contains it.
    """
    from src.schemas.task import SubTask

    registry = _make_registry()
    state = TaskState(
        task_description="T14 TSV aggregation",
        required_capabilities=["parse-files", "aggregate-spending"],
        subtasks=[
            SubTask(
                description="Parse the provided CSV and TSV into structured records.",
                required_capabilities=["parse-files"],
                depends_on=[],
                output_schema={"departments": "list[dict]", "transactions": "list[dict]"},
            ),
            SubTask(
                description="Aggregate upstream_data['transactions'] by department.",
                required_capabilities=["aggregate-spending"],
                depends_on=[0],
                output_schema={"department_count": "int", "department_spending": "dict"},
            ),
        ],
    )

    gap_detection_node(state, registry)

    # The call should have auxiliary_queries populated with each subtask's
    # description in the same order as the slugs.
    call = registry.detect_gaps.call_args
    assert call.args[0] == ["parse-files", "aggregate-spending"]
    aux = call.kwargs["auxiliary_queries"]
    assert aux == [
        "Parse the provided CSV and TSV into structured records.",
        "Aggregate upstream_data['transactions'] by department.",
    ]


def test_dual_query_missing_subtask_for_slug_uses_none_auxiliary():
    """A slug that isn't in any subtask's required_capabilities gets None."""
    from src.schemas.task import SubTask

    registry = _make_registry()
    state = TaskState(
        task_description="unusual decomposition",
        required_capabilities=["matched-slug", "orphan-slug"],
        subtasks=[
            SubTask(
                description="Handle the matched slug.",
                required_capabilities=["matched-slug"],
                depends_on=[],
                output_schema={"result": "dict"},
            ),
        ],
    )

    gap_detection_node(state, registry)

    aux = registry.detect_gaps.call_args.kwargs["auxiliary_queries"]
    assert aux == ["Handle the matched slug.", None]


def test_dual_query_fallback_path_empty_subtasks_all_none_auxiliary():
    """When decomposition failed (empty subtasks, fallback required_caps),
    every auxiliary is None — single-query behaviour preserved.
    """
    registry = _make_registry()
    state = TaskState(
        task_description="Parse this CSV.",
        required_capabilities=["Parse this CSV"],  # first-sentence fallback
        subtasks=[],  # empty — decomposition failed
    )

    gap_detection_node(state, registry)

    aux = registry.detect_gaps.call_args.kwargs["auxiliary_queries"]
    assert aux == [None]


def test_dual_query_multiple_slugs_in_one_subtask():
    """A subtask with multiple required_capabilities shares its description
    as the auxiliary for each slug.
    """
    from src.schemas.task import SubTask

    registry = _make_registry()
    state = TaskState(
        task_description="multi-cap subtask",
        required_capabilities=["cap-a", "cap-b"],
        subtasks=[
            SubTask(
                description="Shared description for multi-capability subtask.",
                required_capabilities=["cap-a", "cap-b"],
                depends_on=[],
                output_schema={"result": "dict"},
            ),
        ],
    )

    gap_detection_node(state, registry)

    aux = registry.detect_gaps.call_args.kwargs["auxiliary_queries"]
    assert aux == [
        "Shared description for multi-capability subtask.",
        "Shared description for multi-capability subtask.",
    ]


# ---------------------------------------------------------------------------
# Registry-match compatibility gate for decomposed subtasks
# ---------------------------------------------------------------------------


def test_incompatible_registry_match_demoted_to_gap():
    """A registry hit that cannot satisfy the subtask's declared produced
    keys must be demoted back into a gap so generation can fill it.
    """
    from src.schemas.task import SubTask

    cap = _make_descriptor(
        "sales-integration-report",
        output_schema={
            "summary_row_count": "int",
            "region_revenues": "dict",
            "regions_meeting_target": "int",
            "output_file": "str",
        },
    )
    registry = _make_registry(
        matches=[(cap, 0.70, "parse-sales-products-and-targets")],
    )
    state = TaskState(
        task_description="T11-style integration task",
        required_capabilities=["parse-sales-products-and-targets"],
        subtasks=[
            SubTask(
                description="Parse the sales CSV, products workbook, and targets CSV.",
                required_capabilities=["parse-sales-products-and-targets"],
                depends_on=[],
                output_schema={
                    "sales": "list[dict]",
                    "products": "list[dict]",
                    "targets": "list[dict]",
                },
            ),
        ],
    )

    cmd = gap_detection_node(state, registry)

    assert cmd.update["matches"] == []
    assert [g.query for g in cmd.update["gaps"]] == [
        "parse-sales-products-and-targets"
    ]


def test_compatible_registry_match_preserved_for_subtask():
    from src.schemas.task import SubTask

    cap = _make_descriptor(
        "csv-parser",
        output_schema={
            "sales": "list[dict]",
            "products": "list[dict]",
            "targets": "list[dict]",
            "extra": "dict",
        },
    )
    registry = _make_registry(
        matches=[(cap, 0.91, "parse-sales-products-and-targets")],
    )
    state = TaskState(
        task_description="parse files",
        required_capabilities=["parse-sales-products-and-targets"],
        subtasks=[
            SubTask(
                description="Parse the sales CSV, products workbook, and targets CSV.",
                required_capabilities=["parse-sales-products-and-targets"],
                depends_on=[],
                output_schema={
                    "sales": "list[dict]",
                    "products": "list[dict]",
                    "targets": "list[dict]",
                },
            ),
        ],
    )

    cmd = gap_detection_node(state, registry)

    assert len(cmd.update["matches"]) == 1
    assert cmd.update["matches"][0].capability.name == "csv-parser"
    assert cmd.update["gaps"] == []


def test_no_subtasks_preserves_direct_match_behavior():
    """Direct retrieval flows without decomposition must remain unchanged."""
    cap = _make_descriptor(
        "sales-integration-report",
        output_schema={
            "summary_row_count": "int",
            "region_revenues": "dict",
            "regions_meeting_target": "int",
            "output_file": "str",
        },
    )
    registry = _make_registry(
        matches=[(cap, 0.70, "parse-sales-products-and-targets")],
    )
    state = TaskState(
        task_description="direct retrieval",
        required_capabilities=["parse-sales-products-and-targets"],
        subtasks=[],
    )

    cmd = gap_detection_node(state, registry)

    assert len(cmd.update["matches"]) == 1
    assert cmd.update["matches"][0].capability.name == "sales-integration-report"
    assert cmd.update["gaps"] == []


def test_incompatible_registry_mcp_tool_input_shape_demoted_to_gap():
    from src.schemas.task import SubTask

    cap = _make_descriptor(
        "hash-rows-and-remove-exact-duplicates",
        input_schema={"csv_path": "str"},
        output_schema={
            "unique_rows": "list[dict]",
            "duplicate_count": "int",
        },
    )
    registry = _make_registry(
        matches=[(cap, 0.64, "hash-rows-and-remove-exact-duplicates")],
    )
    state = TaskState(
        task_description="T12-style cleaning task",
        required_capabilities=["hash-rows-and-remove-exact-duplicates"],
        subtasks=[
            SubTask(
                description="Deduplicate the cleaned upstream rows.",
                required_capabilities=["hash-rows-and-remove-exact-duplicates"],
                depends_on=[0],
                output_schema={
                    "unique_rows": "list[dict]",
                    "duplicate_count": "int",
                },
                requires_upstream_keys=["rows_after_range"],
            ),
        ],
    )

    cmd = gap_detection_node(state, registry)

    assert cmd.update["matches"] == []
    assert [g.query for g in cmd.update["gaps"]] == [
        "hash-rows-and-remove-exact-duplicates"
    ]


def test_compatible_registry_mcp_tool_input_shape_preserved_for_subtask():
    from src.schemas.task import SubTask

    cap = _make_descriptor(
        "hash-rows-and-remove-exact-duplicates",
        input_schema={"upstream_data": "dict[str, Any]"},
        output_schema={
            "unique_rows": "list[dict]",
            "duplicate_count": "int",
        },
    )
    registry = _make_registry(
        matches=[(cap, 0.82, "hash-rows-and-remove-exact-duplicates")],
    )
    state = TaskState(
        task_description="T12-style cleaning task",
        required_capabilities=["hash-rows-and-remove-exact-duplicates"],
        subtasks=[
            SubTask(
                description="Deduplicate the cleaned upstream rows.",
                required_capabilities=["hash-rows-and-remove-exact-duplicates"],
                depends_on=[0],
                output_schema={
                    "unique_rows": "list[dict]",
                    "duplicate_count": "int",
                },
                requires_upstream_keys=["rows_after_range"],
            ),
        ],
    )

    cmd = gap_detection_node(state, registry)

    assert len(cmd.update["matches"]) == 1
    assert cmd.update["matches"][0].capability.name == (
        "hash-rows-and-remove-exact-duplicates"
    )
    assert cmd.update["gaps"] == []


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
# MetricsStore is NOT touched by gap_detection
#
# Historical versions wrote synthetic InvocationRecords for every match
# (token=0, latency=0) so matches would not be pruned as idle. That inflated
# reuse counts without any real execution. Honest invocations are now recorded
# only after chain execution.
# ---------------------------------------------------------------------------


def test_metrics_store_is_not_called_for_matches():
    """gap_detection must NOT record fake invocations for matched capabilities."""
    cap = _make_descriptor("csv-parser")
    registry = _make_registry(matches=[(cap, 0.9, "parse CSV")])
    metrics_store = MagicMock()
    state = TaskState(
        task_description="parse CSV file",
        required_capabilities=["parse CSV"],
    )

    gap_detection_node(state, registry, metrics_store=metrics_store)

    metrics_store.record_invocation.assert_not_called()


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


def test_metrics_store_not_called_for_multiple_matches():
    """Even with multiple matches, no fake invocations are recorded."""
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

    cmd = gap_detection_node(state, registry, metrics_store=metrics_store)

    metrics_store.record_invocation.assert_not_called()
    # Matches still populate state.matches, just not the metrics store.
    assert len(cmd.update["matches"]) == 2
