"""Unit tests for task decomposition schemas and node behaviour.

Integration tests with real DeepAgent calls are in
``tests/integration/test_decomposition.py``.
"""

from __future__ import annotations

import concurrent.futures
from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.decomposition import decomposition_node
from src.schemas.task import (
    DecompositionResult,
    SubTask,
    SubTaskItem,
    TaskState,
    TaskStatus,
)


class TestDecompositionResult:
    def test_valid_result(self):
        result = DecompositionResult(
            subtasks=[
                SubTaskItem(
                    description="Parse the CSV file",
                    required_capabilities=["parse CSV"],
                ),
                SubTaskItem(
                    description="Compute statistics",
                    required_capabilities=["compute mean", "compute std"],
                    depends_on=[0],
                ),
            ],
            reasoning="Split data loading from computation",
        )
        assert len(result.subtasks) == 2
        assert result.subtasks[1].depends_on == [0]

    def test_empty_subtasks_list(self):
        result = DecompositionResult(subtasks=[])
        assert result.subtasks == []

    def test_subtask_requires_capabilities(self):
        with pytest.raises(ValueError):
            SubTaskItem(description="Do something")  # missing required_capabilities

    def test_subtask_roundtrip(self):
        item = SubTaskItem(
            description="Test task",
            required_capabilities=["tool-a"],
            depends_on=[0, 1],
        )
        roundtripped = SubTaskItem.model_validate_json(item.model_dump_json())
        assert roundtripped.description == "Test task"
        assert roundtripped.depends_on == [0, 1]

    def test_subtask_empty_required_capabilities_rejected(self):
        """SubTaskItem with empty required_capabilities list is invalid (min_length=1)."""
        with pytest.raises(ValueError):
            SubTaskItem(description="Do something", required_capabilities=[])


# ---------------------------------------------------------------------------
# decomposition_node — behaviour (mocking the DeepAgent)
# ---------------------------------------------------------------------------


def _make_mock_pool(future_result=None, future_raises=None):
    """Return a (MockTPE class, future) pair for patching ThreadPoolExecutor.

    The executor is now used directly (not as a context manager), so the mock
    only needs to support: ``ThreadPoolExecutor(max_workers=1)`` → pool,
    ``pool.submit(...)`` → future, ``pool.shutdown(wait=False)``.
    """
    mock_future = MagicMock()
    if future_raises is not None:
        mock_future.result.side_effect = future_raises
    else:
        mock_future.result.return_value = future_result

    mock_pool = MagicMock()
    mock_pool.submit.return_value = mock_future

    mock_tpe_cls = MagicMock(return_value=mock_pool)

    return mock_tpe_cls, mock_future


class TestDecompositionNode:
    """Unit tests for decomposition_node — all LLM/agent calls are mocked."""

    @pytest.fixture
    def state(self):
        return TaskState(task_description="Parse a CSV and compute statistics")

    def test_structured_response_populates_subtasks(self, state):
        """Valid DecompositionResult is converted into SubTask objects in state."""
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(
                    description="Parse CSV file",
                    required_capabilities=["parse CSV"],
                ),
                SubTaskItem(
                    description="Compute mean and std",
                    required_capabilities=["compute mean", "compute std"],
                    depends_on=[0],
                ),
            ],
            reasoning="Split I/O from computation",
        )
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.goto == "gap_detection"
        assert cmd.update["status"] == TaskStatus.DECOMPOSING
        subtasks = cmd.update["subtasks"]
        assert len(subtasks) == 2
        assert all(isinstance(s, SubTask) for s in subtasks)
        assert subtasks[0].description == "Parse CSV file"
        assert subtasks[1].depends_on == [0]

    def test_required_capabilities_aggregated_from_all_subtasks(self, state):
        """required_capabilities collects all capability strings across subtasks."""
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(description="A", required_capabilities=["cap-a", "cap-b"]),
                SubTaskItem(description="B", required_capabilities=["cap-c"]),
            ]
        )
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert set(cmd.update["required_capabilities"]) == {"cap-a", "cap-b", "cap-c"}

    def test_no_structured_response_falls_back_to_task_description(self, state):
        """If structured_response is None, falls back to whole task as one capability."""
        mock_tpe, _ = _make_mock_pool({"structured_response": None})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["subtasks"] == []
        assert cmd.update["required_capabilities"] == [state.task_description]
        errors = cmd.update["errors"]
        assert any("no structured response" in e.lower() for e in errors)

    def test_timeout_falls_back_and_records_error(self, state):
        """TimeoutError from the agent is caught and recorded in errors."""
        mock_tpe, _ = _make_mock_pool(
            future_raises=concurrent.futures.TimeoutError()
        )

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["subtasks"] == []
        assert cmd.update["required_capabilities"] == [state.task_description]
        errors = cmd.update["errors"]
        assert any("timed out" in e.lower() for e in errors)

    def test_arbitrary_exception_falls_back_and_records_error(self, state):
        """Any unexpected exception is caught and recorded in errors."""
        mock_tpe, _ = _make_mock_pool(
            future_raises=RuntimeError("Connection refused")
        )

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["subtasks"] == []
        assert cmd.update["required_capabilities"] == [state.task_description]
        errors = cmd.update["errors"]
        assert any("Connection refused" in e for e in errors)

    def test_status_always_decomposing(self, state):
        """Status is DECOMPOSING regardless of success or failure."""
        mock_tpe_ok, _ = _make_mock_pool({"structured_response": None})
        mock_tpe_err, _ = _make_mock_pool(future_raises=RuntimeError("err"))

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe_ok):
                cmd_ok = decomposition_node(state)
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe_err):
                cmd_err = decomposition_node(state)

        assert cmd_ok.update["status"] == TaskStatus.DECOMPOSING
        assert cmd_err.update["status"] == TaskStatus.DECOMPOSING

    def test_routes_to_gap_detection(self, state):
        """Node always routes to gap_detection."""
        mock_tpe, _ = _make_mock_pool({"structured_response": None})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.goto == "gap_detection"

    def test_existing_errors_preserved(self):
        """Errors already in state.errors are kept alongside new ones."""
        state = TaskState(
            task_description="Test",
            errors=["pre-existing error"],
        )
        mock_tpe, _ = _make_mock_pool(future_raises=RuntimeError("new error"))

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        errors = cmd.update["errors"]
        assert "pre-existing error" in errors
        assert any("new error" in e for e in errors)

    def test_empty_subtasks_list_falls_back_and_records_error(self, state):
        """Agent returns DecompositionResult with 0 subtasks → fallback + error recorded.

        Unlike structured_response=None (no response at all), a valid response with
        an empty subtasks list is a distinct failure mode — the agent succeeded
        structurally but produced no useful output.  An error must be recorded so
        operators can observe this in state.errors and in LangFuse.
        """
        structured = DecompositionResult(subtasks=[], reasoning="Task seems trivial")
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["subtasks"] == []
        assert cmd.update["required_capabilities"] == [state.task_description]
        errors = cmd.update["errors"]
        assert any("0 subtasks" in e for e in errors)

    # ------------------------------------------------------------------
    # Sticky decomposition_succeeded flag (Fix 2b)
    # ------------------------------------------------------------------
    # These tests verify the sticky failure flag added to TaskState so the
    # user-facing CLI and the evaluation harness can distinguish a real
    # success from a fallback-path "success" where the decomposer bailed out
    # and the pipeline produced whatever it could from a single-sentence
    # fallback query. See docs/perf_fixes_2026_04_14.md §2b.

    def test_success_sets_decomposition_succeeded_true(self, state):
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(description="A", required_capabilities=["cap-a"]),
            ]
        )
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["decomposition_succeeded"] is True

    def test_timeout_sets_decomposition_succeeded_false(self, state):
        mock_tpe, _ = _make_mock_pool(
            future_raises=concurrent.futures.TimeoutError()
        )

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["decomposition_succeeded"] is False

    def test_no_structured_response_sets_decomposition_succeeded_false(self, state):
        mock_tpe, _ = _make_mock_pool({"structured_response": None})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["decomposition_succeeded"] is False

    def test_empty_subtasks_sets_decomposition_succeeded_false(self, state):
        structured = DecompositionResult(subtasks=[], reasoning="trivial")
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["decomposition_succeeded"] is False

    def test_generic_exception_sets_decomposition_succeeded_false(self, state):
        mock_tpe, _ = _make_mock_pool(
            future_raises=RuntimeError("Connection refused")
        )

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["decomposition_succeeded"] is False

    def test_duplicate_required_capabilities_deduplicated(self, state):
        """Shared capability names across subtasks appear only once in required_capabilities."""
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(description="A", required_capabilities=["shared-cap", "cap-a"]),
                SubTaskItem(description="B", required_capabilities=["shared-cap", "cap-b"]),
            ]
        )
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        caps = cmd.update["required_capabilities"]
        assert caps.count("shared-cap") == 1
        assert set(caps) == {"shared-cap", "cap-a", "cap-b"}


# ---------------------------------------------------------------------------
# Circuit breaker tests (Fix 1)
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    """Tests for the two-stage decomposition circuit breaker."""

    @pytest.fixture
    def state(self):
        return TaskState(task_description="Complex task")

    def test_caps_under_warn_limit_pass_through_unchanged(self, state):
        """3 caps (below WARN_CAPS=5) pass through with no warning."""
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(description="A", required_capabilities=["c1", "c2", "c3"]),
            ]
        )
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        caps = cmd.update["required_capabilities"]
        assert len(caps) == 3
        assert set(caps) == {"c1", "c2", "c3"}

    def test_over_warn_limit_logs_warning_but_does_not_truncate(self, state):
        """6 caps triggers a warning but all 6 are returned."""
        cap_names = [f"cap-{i}" for i in range(6)]
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(description="A", required_capabilities=cap_names[:3]),
                SubTaskItem(description="B", required_capabilities=cap_names[3:]),
            ]
        )
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        caps = cmd.update["required_capabilities"]
        assert len(caps) == 6
        assert set(caps) == set(cap_names)

    def test_hard_truncation_at_max_caps(self, state):
        """10 caps triggers hard truncation to MAX_CAPS_HARD=8."""
        cap_names = [f"cap-{i}" for i in range(10)]
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(description="A", required_capabilities=cap_names[:5]),
                SubTaskItem(description="B", required_capabilities=cap_names[5:]),
            ]
        )
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                with patch("src.orchestrator.decomposition.emit_langfuse_event") as mock_emit:
                    cmd = decomposition_node(state)

        caps = cmd.update["required_capabilities"]
        assert len(caps) == 8
        # Verify the truncation event was emitted
        truncation_calls = [
            c for c in mock_emit.call_args_list
            if c[0][1] == "decomposition.hard_truncation"
        ]
        assert len(truncation_calls) == 1
        meta = truncation_calls[0][0][3]
        assert meta["original_count"] == 10
        assert meta["truncated_to"] == 8
        assert len(meta["dropped"]) == 2


# ---------------------------------------------------------------------------
# Chain coherence validation (Phase 2 Step 14)
# ---------------------------------------------------------------------------
#
# These tests cover the _validate_chain_coherence helper and its integration
# into decomposition_node. The motivating failure is the 2026-04-14 19:01
# test_mint TSV run where the decomposer produced a 3-subtask chain whose
# step 2 referenced an 'transactions' key no predecessor produced, causing
# a runtime crash. See docs/perf_fixes_2026_04_14.md §4.5 and
# docs/MASTER_PLAN_2026_04_13.md Phase 2 Step 14.


class TestCollectTransitiveUpstreamKeys:
    """Unit tests for the DAG walker that collects transitive predecessor keys."""

    def test_single_direct_predecessor(self):
        from src.orchestrator.decomposition import _collect_transitive_upstream_keys

        subtasks = [
            SubTask(
                description="Root",
                required_capabilities=["root"],
                depends_on=[],
                output_schema={"a": "int", "b": "str"},
            ),
            SubTask(
                description="Child",
                required_capabilities=["child"],
                depends_on=[0],
                output_schema={"c": "int"},
            ),
        ]
        keys = _collect_transitive_upstream_keys(subtasks, 1)
        assert keys == {"a", "b"}

    def test_transitive_two_hops(self):
        from src.orchestrator.decomposition import _collect_transitive_upstream_keys

        subtasks = [
            SubTask(
                description="Grandparent",
                required_capabilities=["gp"],
                depends_on=[],
                output_schema={"g1": "int"},
            ),
            SubTask(
                description="Parent",
                required_capabilities=["p"],
                depends_on=[0],
                output_schema={"p1": "str"},
            ),
            SubTask(
                description="Child",
                required_capabilities=["c"],
                depends_on=[1],
                output_schema={"c1": "bool"},
            ),
        ]
        keys = _collect_transitive_upstream_keys(subtasks, 2)
        assert keys == {"g1", "p1"}

    def test_multiple_predecessors_merged(self):
        from src.orchestrator.decomposition import _collect_transitive_upstream_keys

        subtasks = [
            SubTask(
                description="A",
                required_capabilities=["a"],
                depends_on=[],
                output_schema={"x": "int"},
            ),
            SubTask(
                description="B",
                required_capabilities=["b"],
                depends_on=[],
                output_schema={"y": "str"},
            ),
            SubTask(
                description="C",
                required_capabilities=["c"],
                depends_on=[0, 1],
                output_schema={"z": "bool"},
            ),
        ]
        keys = _collect_transitive_upstream_keys(subtasks, 2)
        assert keys == {"x", "y"}

    def test_root_has_no_upstream(self):
        from src.orchestrator.decomposition import _collect_transitive_upstream_keys

        subtasks = [
            SubTask(
                description="Root",
                required_capabilities=["root"],
                depends_on=[],
                output_schema={"a": "int"},
            ),
        ]
        assert _collect_transitive_upstream_keys(subtasks, 0) == set()

    def test_invalid_index_returns_empty(self):
        from src.orchestrator.decomposition import _collect_transitive_upstream_keys

        assert _collect_transitive_upstream_keys([], 0) == set()

    def test_cycle_does_not_loop_forever(self):
        """Pathological case: a subtask depending on itself or a cycle."""
        from src.orchestrator.decomposition import _collect_transitive_upstream_keys

        subtasks = [
            SubTask(
                description="A",
                required_capabilities=["a"],
                depends_on=[1],  # cycle: 0 → 1 → 0
                output_schema={"x": "int"},
            ),
            SubTask(
                description="B",
                required_capabilities=["b"],
                depends_on=[0],
                output_schema={"y": "str"},
            ),
        ]
        # Should terminate and return the union of both output_schemas.
        keys = _collect_transitive_upstream_keys(subtasks, 1)
        assert keys == {"x", "y"}


class TestValidateChainCoherence:
    """Unit tests for the coherence validator."""

    def test_single_subtask_chain_trivially_coherent(self):
        from src.orchestrator.decomposition import _validate_chain_coherence

        subtasks = [
            SubTask(
                description="Parse CSV and return row count.",
                required_capabilities=["parse-csv"],
                depends_on=[],
                output_schema={"row_count": "int"},
            ),
        ]
        assert _validate_chain_coherence(subtasks) == []

    def test_coherent_two_step_chain(self):
        """Step 1 produces 'rows'; step 2 declares it requires 'rows'."""
        from src.orchestrator.decomposition import _validate_chain_coherence

        subtasks = [
            SubTask(
                description="Parse the CSV and return rows.",
                required_capabilities=["parse-csv"],
                depends_on=[],
                output_schema={"rows": "list[dict]"},
            ),
            SubTask(
                description="Aggregate the upstream rows by category.",
                required_capabilities=["aggregate"],
                depends_on=[0],
                output_schema={"aggregated": "dict"},
                requires_upstream_keys=["rows"],
            ),
        ]
        assert _validate_chain_coherence(subtasks) == []

    def test_t14_tsv_incoherent_chain_is_flagged(self):
        """T14 TSV pattern: subtask 1 declares 'transactions' but no predecessor produces it."""
        from src.orchestrator.decomposition import _validate_chain_coherence

        subtasks = [
            SubTask(
                description="Parse the departments CSV.",
                required_capabilities=["parse-csv-file"],
                depends_on=[],
                output_schema={"departments": "list[dict]"},
            ),
            SubTask(
                description="Aggregate spending per department.",
                required_capabilities=["parse-tsv-and-aggregate-by-department"],
                depends_on=[0],
                output_schema={"department_spending": "dict[str, float]"},
                requires_upstream_keys=["departments", "transactions"],
            ),
            SubTask(
                description="Format the final result dict.",
                required_capabilities=["format-final-result-dict"],
                depends_on=[1],
                output_schema={"department_count": "int", "department_spending": "dict[str, float]"},
                requires_upstream_keys=["department_spending"],
            ),
        ]
        violations = _validate_chain_coherence(subtasks)
        assert len(violations) == 1
        assert "transactions" in violations[0]
        assert "1" in violations[0]  # subtask index
        assert "parse-tsv-and-aggregate-by-department" in violations[0]

    def test_transitive_coherent_chain_passes(self):
        """Step 3 declares a requirement produced by step 1 (grandparent via step 2)."""
        from src.orchestrator.decomposition import _validate_chain_coherence

        subtasks = [
            SubTask(
                description="Parse CSV.",
                required_capabilities=["parse"],
                depends_on=[],
                output_schema={"raw_data": "list[dict]"},
            ),
            SubTask(
                description="Normalize the data.",
                required_capabilities=["normalize"],
                depends_on=[0],
                output_schema={"normalized": "list[dict]"},
                requires_upstream_keys=["raw_data"],
            ),
            SubTask(
                description="Generate a report.",
                required_capabilities=["report"],
                depends_on=[1],
                output_schema={"report": "str"},
                requires_upstream_keys=["raw_data", "normalized"],
            ),
        ]
        # 'raw_data' comes from step 0 (transitive via step 1), 'normalized'
        # comes from step 1. Both available.
        assert _validate_chain_coherence(subtasks) == []

    def test_empty_requires_upstream_keys_trivially_passes(self):
        """A subtask with no upstream requirements needs no validation."""
        from src.orchestrator.decomposition import _validate_chain_coherence

        subtasks = [
            SubTask(
                description="Step A produces output.",
                required_capabilities=["a"],
                depends_on=[],
                output_schema={"a_out": "int"},
            ),
            SubTask(
                description="Step B runs independently and returns its own output.",
                required_capabilities=["b"],
                depends_on=[0],
                output_schema={"b_out": "str"},
                # Intentionally empty — step B doesn't consume from upstream.
                requires_upstream_keys=[],
            ),
        ]
        assert _validate_chain_coherence(subtasks) == []

    def test_root_subtask_with_requires_upstream_keys_is_flagged(self):
        """A root subtask (no depends_on) cannot legally declare upstream keys."""
        from src.orchestrator.decomposition import _validate_chain_coherence

        subtasks = [
            SubTask(
                description="Root step that nonsensically claims an upstream key.",
                required_capabilities=["bad-root"],
                depends_on=[],
                output_schema={"out": "int"},
                requires_upstream_keys=["nonexistent"],
            ),
            SubTask(
                description="Downstream step.",
                required_capabilities=["downstream"],
                depends_on=[0],
                output_schema={"final": "int"},
                requires_upstream_keys=["out"],
            ),
        ]
        violations = _validate_chain_coherence(subtasks)
        assert len(violations) == 1
        assert "Root subtasks must not require upstream keys" in violations[0]

    def test_nested_key_access_uses_top_level_only(self):
        """T04 regression: nested accesses list the top-level key, not the nested field.

        The decomposer reads upstream_data['rules']['min_days_of_stock'] at
        runtime, but requires_upstream_keys lists 'rules' only. The
        predecessor declares 'rules' in its output_schema, so the chain is
        coherent — nested fields are opaque to the contract by design.
        """
        from src.orchestrator.decomposition import _validate_chain_coherence

        subtasks = [
            SubTask(
                description="Parse the inventory CSV and reorder rules YAML.",
                required_capabilities=["parse-inventory-and-rules"],
                depends_on=[],
                output_schema={
                    "inventory": "list[dict]",
                    "rules": "dict",
                },
            ),
            SubTask(
                description="Compute per-product days_of_stock and flag below-threshold items.",
                required_capabilities=["compute-days-of-stock-and-flag"],
                depends_on=[0],
                output_schema={
                    "reorder_candidates": "list[dict]",
                    "product_count": "int",
                },
                requires_upstream_keys=["inventory", "rules"],
            ),
        ]
        assert _validate_chain_coherence(subtasks) == []

    def test_aggregation_with_own_outputs_not_false_flagged(self):
        """A subtask can produce output keys that happen to share names with
        domain concepts from upstream without the validator confusing them —
        the structured field only tracks explicit ``requires_upstream_keys``,
        not prose. Regression guard for the false-positive class the prose-
        parsing predecessor of this check produced.
        """
        from src.orchestrator.decomposition import _validate_chain_coherence

        subtasks = [
            SubTask(
                description="Parse departments CSV and transactions TSV.",
                required_capabilities=["parse-csv-and-tsv"],
                depends_on=[],
                output_schema={
                    "departments": "list[dict]",
                    "transactions": "list[dict]",
                },
            ),
            SubTask(
                description="Aggregate transactions by department.",
                required_capabilities=["aggregate-spending-by-department"],
                depends_on=[0],
                output_schema={
                    "department_count": "int",
                    "department_spending": "dict[str, float]",
                },
                requires_upstream_keys=["transactions"],
            ),
        ]
        assert _validate_chain_coherence(subtasks) == []


class TestDecompositionNodeCoherenceIntegration:
    """Integration test: coherence failure in decomposition_node sets sticky flag."""

    @pytest.fixture
    def state(self):
        return TaskState(task_description="Parse two files and aggregate")

    def test_incoherent_chain_sets_decomposition_succeeded_false(self, state):
        """A decomposer output with an incoherent chain trips the sticky flag."""
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(
                    description="Parse the CSV file into a list of departments.",
                    required_capabilities=["parse-csv"],
                    depends_on=[],
                    output_schema={"departments": "list[dict]"},
                ),
                SubTaskItem(
                    description="Aggregate total spending by department.",
                    required_capabilities=["aggregate"],
                    depends_on=[0],
                    output_schema={"result": "dict"},
                    requires_upstream_keys=["departments", "transactions"],
                ),
            ]
        )
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        # Subtasks are still returned (fail-open)
        assert len(cmd.update["subtasks"]) == 2
        # But the sticky flag is False
        assert cmd.update["decomposition_succeeded"] is False
        # And an error message is in the errors list
        errors = cmd.update["errors"]
        assert any(
            "coherence" in e.lower() and "transactions" in e for e in errors
        )

    def test_coherent_chain_keeps_decomposition_succeeded_true(self, state):
        """A coherent decomposition leaves the sticky flag True."""
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(
                    description="Parse both files.",
                    required_capabilities=["parse"],
                    depends_on=[],
                    output_schema={"a": "list", "b": "list"},
                ),
                SubTaskItem(
                    description="Combine the upstream 'a' and 'b' lists.",
                    required_capabilities=["combine"],
                    depends_on=[0],
                    output_schema={"result": "dict"},
                    requires_upstream_keys=["a", "b"],
                ),
            ]
        )
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["decomposition_succeeded"] is True
        # No coherence violation messages
        errors = cmd.update["errors"]
        assert not any("coherence" in e.lower() for e in errors)
