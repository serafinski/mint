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
    OutputContract,
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

    def test_no_structured_response_fails_closed_after_retries(self, state):
        """§12 finding #15: 3x retries with None structured_response → fail-closed.

        No single-sentence fallback; decomposition_succeeded=False flips so
        terminal callers classify this as a generation_failed run.
        """
        from src.orchestrator.decomposition import MAX_DECOMPOSITION_ATTEMPTS

        mock_tpe, _ = _make_mock_pool({"structured_response": None})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["subtasks"] == []
        assert cmd.update["required_capabilities"] == []  # no fallback
        assert cmd.update["decomposition_succeeded"] is False
        errors = cmd.update["errors"]
        # One "no structured response" error per attempt.
        no_resp_errs = [e for e in errors if "no structured response" in e.lower()]
        assert len(no_resp_errs) == MAX_DECOMPOSITION_ATTEMPTS

    def test_timeout_fails_closed_after_retries(self, state):
        """§12 finding #15: TimeoutError on all attempts → fail-closed.

        Under the new retry + fail-closed contract (MAX_DECOMPOSITION_ATTEMPTS=3):
        - Every attempt times out → errors list has 3 timeout entries
        - No single-sentence fallback (required_capabilities stays empty)
        - decomposition_succeeded=False sticky flag flips
        - terminal callers detect the flag as a generation failure
        """
        from src.orchestrator.decomposition import MAX_DECOMPOSITION_ATTEMPTS

        mock_tpe, _ = _make_mock_pool(
            future_raises=concurrent.futures.TimeoutError()
        )

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["subtasks"] == []
        # No fallback — required_capabilities stays empty instead of
        # degrading to the single-sentence path.
        assert cmd.update["required_capabilities"] == []
        assert cmd.update["decomposition_succeeded"] is False
        errors = cmd.update["errors"]
        # One "timed out" error per attempt.
        timeout_errs = [e for e in errors if "timed out" in e.lower()]
        assert len(timeout_errs) == MAX_DECOMPOSITION_ATTEMPTS

    def test_arbitrary_exception_fails_closed_after_retries(self, state):
        """§12 finding #15: Exception on all attempts → fail-closed."""
        from src.orchestrator.decomposition import MAX_DECOMPOSITION_ATTEMPTS

        mock_tpe, _ = _make_mock_pool(
            future_raises=RuntimeError("Connection refused")
        )

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["subtasks"] == []
        assert cmd.update["required_capabilities"] == []  # no fallback
        assert cmd.update["decomposition_succeeded"] is False
        errors = cmd.update["errors"]
        # One "Connection refused" per attempt.
        match_errs = [e for e in errors if "Connection refused" in e]
        assert len(match_errs) == MAX_DECOMPOSITION_ATTEMPTS

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

    def test_empty_subtasks_list_fails_closed_after_retries(self, state):
        """§12 finding #15: 0 subtasks on every attempt → fail-closed.

        A valid structured response with an empty subtasks list is treated
        as a retryable failure. After MAX_DECOMPOSITION_ATTEMPTS consecutive
        empty responses, the node fails closed with no single-sentence
        fallback.
        """
        from src.orchestrator.decomposition import MAX_DECOMPOSITION_ATTEMPTS

        structured = DecompositionResult(subtasks=[], reasoning="Task seems trivial")
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["subtasks"] == []
        assert cmd.update["required_capabilities"] == []  # no fallback
        assert cmd.update["decomposition_succeeded"] is False
        errors = cmd.update["errors"]
        # One "empty subtask list" error per attempt.
        empty_errs = [e for e in errors if "empty subtask list" in e]
        assert len(empty_errs) == MAX_DECOMPOSITION_ATTEMPTS

    # ------------------------------------------------------------------
    # Sticky decomposition_succeeded flag (Fix 2b)
    # ------------------------------------------------------------------
    # These tests verify the sticky failure flag added to TaskState so terminal
    # callers can distinguish a real success from a fallback-path "success"
    # where the decomposer bailed out
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
# eval TSV run where the decomposer produced a 3-subtask chain whose
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


class TestCollectTransitiveKeyProducers:
    """Unit tests for the producer-attribution helper used by collision detection."""

    def test_empty_depends_on(self):
        from src.orchestrator.decomposition import _collect_transitive_key_producers

        subtasks = [
            SubTask(
                description="Root.",
                required_capabilities=["root"],
                depends_on=[],
                output_schema={"x": "int"},
            ),
        ]
        # Root subtask has no transitive predecessors → empty producer map.
        assert _collect_transitive_key_producers(subtasks, 0) == {}

    def test_single_predecessor_single_key(self):
        from src.orchestrator.decomposition import _collect_transitive_key_producers

        subtasks = [
            SubTask(
                description="A.",
                required_capabilities=["a"],
                depends_on=[],
                output_schema={"records": "list[dict]"},
            ),
            SubTask(
                description="B.",
                required_capabilities=["b"],
                depends_on=[0],
                output_schema={"summary": "dict"},
            ),
        ]
        producers = _collect_transitive_key_producers(subtasks, 1)
        assert producers == {"records": [0]}

    def test_two_predecessors_disjoint_keys(self):
        from src.orchestrator.decomposition import _collect_transitive_key_producers

        subtasks = [
            SubTask(
                description="A.",
                required_capabilities=["a"],
                depends_on=[],
                output_schema={"alpha": "int"},
            ),
            SubTask(
                description="B.",
                required_capabilities=["b"],
                depends_on=[],
                output_schema={"beta": "str"},
            ),
            SubTask(
                description="C.",
                required_capabilities=["c"],
                depends_on=[0, 1],
                output_schema={"final": "dict"},
            ),
        ]
        producers = _collect_transitive_key_producers(subtasks, 2)
        assert producers == {"alpha": [0], "beta": [1]}

    def test_two_predecessors_colliding_key(self):
        """The critical case: two transitive predecessors produce the same
        top-level key. The producer list must contain both indices in
        sorted order so downstream dedup logic is deterministic.
        """
        from src.orchestrator.decomposition import _collect_transitive_key_producers

        subtasks = [
            SubTask(
                description="Parses sales CSV.",
                required_capabilities=["parse-sales"],
                depends_on=[],
                output_schema={"records": "list[dict]"},
            ),
            SubTask(
                description="Parses inventory CSV.",
                required_capabilities=["parse-inventory"],
                depends_on=[],
                output_schema={"records": "list[dict]"},
            ),
            SubTask(
                description="Merge.",
                required_capabilities=["merge"],
                depends_on=[0, 1],
                output_schema={"merged": "list"},
            ),
        ]
        producers = _collect_transitive_key_producers(subtasks, 2)
        assert producers == {"records": [0, 1]}

    def test_transitive_walk_collects_grandparent_producers(self):
        """Step 3 depends on step 2; step 2 depends on steps 0 and 1. The
        producers map for step 3 must include the grandparent contributions.
        """
        from src.orchestrator.decomposition import _collect_transitive_key_producers

        subtasks = [
            SubTask(
                description="A.",
                required_capabilities=["a"],
                depends_on=[],
                output_schema={"alpha": "int"},
            ),
            SubTask(
                description="B.",
                required_capabilities=["b"],
                depends_on=[],
                output_schema={"beta": "str"},
            ),
            SubTask(
                description="C.",
                required_capabilities=["c"],
                depends_on=[0, 1],
                output_schema={"gamma": "dict"},
            ),
            SubTask(
                description="D.",
                required_capabilities=["d"],
                depends_on=[2],
                output_schema={"delta": "list"},
            ),
        ]
        producers = _collect_transitive_key_producers(subtasks, 3)
        assert producers == {"alpha": [0], "beta": [1], "gamma": [2]}

    def test_cycle_protection(self):
        """Cyclic depends_on (pathological decomposition) must not infinite-loop.
        The visited set short-circuits the walk the first time it re-enters
        an already-visited index.
        """
        from src.orchestrator.decomposition import _collect_transitive_key_producers

        subtasks = [
            SubTask(
                description="A.",
                required_capabilities=["a"],
                depends_on=[1],  # cycle: A depends on B
                output_schema={"x": "int"},
            ),
            SubTask(
                description="B.",
                required_capabilities=["b"],
                depends_on=[0],  # cycle: B depends on A
                output_schema={"y": "int"},
            ),
        ]
        producers = _collect_transitive_key_producers(subtasks, 0)
        # Walks A's deps → B, collects y; walks B's deps → A, collects x;
        # tries to revisit B but hit the visited set → halts.
        assert set(producers.keys()) == {"x", "y"}


class TestDetectUpstreamKeyCollisions:
    """Unit tests for the collision detector (Option A, soft warnings only).

    See ``docs/MASTER_PLAN_2026_04_13.md`` §14.10 for the full rationale.
    """

    def test_empty_and_single_subtask_chains(self):
        from src.orchestrator.decomposition import _detect_upstream_key_collisions

        # Empty chain → no collisions possible
        assert _detect_upstream_key_collisions([]) == []

        # Single subtask → no predecessors, no collisions possible
        single = [
            SubTask(
                description="Only.",
                required_capabilities=["only"],
                depends_on=[],
                output_schema={"x": "int"},
            ),
        ]
        assert _detect_upstream_key_collisions(single) == []

    def test_no_collisions_disjoint_output_schemas(self):
        """Chain where every predecessor's output_schema uses distinct key
        names produces no collision warnings.
        """
        from src.orchestrator.decomposition import _detect_upstream_key_collisions

        subtasks = [
            SubTask(
                description="Parse sales.",
                required_capabilities=["parse-sales"],
                depends_on=[],
                output_schema={"sales": "list[dict]"},
            ),
            SubTask(
                description="Parse inventory.",
                required_capabilities=["parse-inventory"],
                depends_on=[],
                output_schema={"inventory": "list[dict]"},
            ),
            SubTask(
                description="Merge sales and inventory.",
                required_capabilities=["merge"],
                depends_on=[0, 1],
                output_schema={"merged": "list"},
                requires_upstream_keys=["sales", "inventory"],
            ),
        ]
        assert _detect_upstream_key_collisions(subtasks) == []

    def test_canonical_collision_two_parsers_same_key(self):
        """Two parsers both produce ``records`` → downstream merge silently
        loses one of them at runtime. Must be flagged.
        """
        from src.orchestrator.decomposition import _detect_upstream_key_collisions

        subtasks = [
            SubTask(
                description="Parse sales CSV.",
                required_capabilities=["parse-sales-csv"],
                depends_on=[],
                output_schema={"records": "list[dict] — sales rows"},
            ),
            SubTask(
                description="Parse inventory CSV.",
                required_capabilities=["parse-inventory-csv"],
                depends_on=[],
                output_schema={"records": "list[dict] — inventory rows"},
            ),
            SubTask(
                description="Analyze.",
                required_capabilities=["analyze"],
                depends_on=[0, 1],
                output_schema={"report": "dict"},
                requires_upstream_keys=["records"],
            ),
        ]
        warnings = _detect_upstream_key_collisions(subtasks)
        assert len(warnings) == 1
        assert "'records'" in warnings[0]
        assert "[0, 1]" in warnings[0]
        assert "last-write-wins" in warnings[0]
        assert "2" in warnings[0]  # reported at subtask index 2

    def test_collision_reported_at_first_successor_only(self):
        """If two successors both have the colliding predecessors in their
        transitive set, the collision is reported at the earliest one only
        (dedup by (key, producer_indices) fingerprint).
        """
        from src.orchestrator.decomposition import _detect_upstream_key_collisions

        subtasks = [
            SubTask(
                description="A produces X.",
                required_capabilities=["a"],
                depends_on=[],
                output_schema={"x": "int"},
            ),
            SubTask(
                description="B also produces X.",
                required_capabilities=["b"],
                depends_on=[],
                output_schema={"x": "int"},
            ),
            SubTask(
                description="C depends on A and B.",
                required_capabilities=["c"],
                depends_on=[0, 1],
                output_schema={"c_out": "dict"},
                requires_upstream_keys=["x"],
            ),
            SubTask(
                description="D depends on C (transitively on A, B).",
                required_capabilities=["d"],
                depends_on=[2],
                output_schema={"d_out": "dict"},
                requires_upstream_keys=["c_out"],
            ),
        ]
        warnings = _detect_upstream_key_collisions(subtasks)
        # Only ONE warning — the collision fingerprint (('x', (0, 1)))
        # is already seen by the time we walk D's predecessors.
        assert len(warnings) == 1
        # And it must be reported at subtask 2, not subtask 3.
        assert "Subtask 2" in warnings[0]
        assert "Subtask 3" not in warnings[0]

    def test_multiple_distinct_collisions_all_reported(self):
        """Two different colliding keys (different producer sets) must both
        be reported.
        """
        from src.orchestrator.decomposition import _detect_upstream_key_collisions

        subtasks = [
            SubTask(
                description="A.",
                required_capabilities=["a"],
                depends_on=[],
                output_schema={"records": "list", "metadata": "dict"},
            ),
            SubTask(
                description="B.",
                required_capabilities=["b"],
                depends_on=[],
                output_schema={"records": "list", "metadata": "dict"},
            ),
            SubTask(
                description="Merge.",
                required_capabilities=["merge"],
                depends_on=[0, 1],
                output_schema={"result": "dict"},
                requires_upstream_keys=["records", "metadata"],
            ),
        ]
        warnings = _detect_upstream_key_collisions(subtasks)
        # A single warning message for subtask 2 that lists BOTH colliding keys.
        assert len(warnings) == 1
        assert "'records'" in warnings[0]
        assert "'metadata'" in warnings[0]

    def test_successor_own_output_not_a_collision_with_predecessor(self):
        """A key that appears in BOTH a predecessor's output_schema AND the
        successor's own output_schema is not a collision — the successor's
        own output is never merged into its own ``upstream_data``.
        """
        from src.orchestrator.decomposition import _detect_upstream_key_collisions

        subtasks = [
            SubTask(
                description="Parse.",
                required_capabilities=["parse"],
                depends_on=[],
                output_schema={"records": "list"},
            ),
            SubTask(
                description="Transform and republish under same key.",
                required_capabilities=["transform"],
                depends_on=[0],
                output_schema={"records": "list"},  # same name, but own output
                requires_upstream_keys=["records"],
            ),
        ]
        # The successor's own output_schema is NOT part of its transitive
        # predecessor set — the walker starts from depends_on, not from
        # subtask_index itself. So no collision.
        assert _detect_upstream_key_collisions(subtasks) == []

    def test_collision_across_transitive_predecessors_not_direct_deps(self):
        """Collision between two grandparent producers must still be
        detected when the successor only directly depends on a middle step.
        """
        from src.orchestrator.decomposition import _detect_upstream_key_collisions

        subtasks = [
            SubTask(
                description="A produces X.",
                required_capabilities=["a"],
                depends_on=[],
                output_schema={"x": "int"},
            ),
            SubTask(
                description="B also produces X.",
                required_capabilities=["b"],
                depends_on=[],
                output_schema={"x": "int"},
            ),
            SubTask(
                description="C depends on A and B, produces Y.",
                required_capabilities=["c"],
                depends_on=[0, 1],
                output_schema={"y": "dict"},
                requires_upstream_keys=["x"],
            ),
            SubTask(
                description="D depends on C only, but transitively on A & B.",
                required_capabilities=["d"],
                depends_on=[2],
                output_schema={"final": "dict"},
                requires_upstream_keys=["y"],
            ),
        ]
        warnings = _detect_upstream_key_collisions(subtasks)
        # C sees the collision first (its direct depends_on is [0, 1]).
        # D also transitively sees it but is deduplicated.
        assert len(warnings) == 1
        assert "Subtask 2" in warnings[0]

    def test_collisions_do_not_trip_hard_violations(self):
        """The validator (hard violations) and the collision detector (soft
        warnings) are orthogonal. A chain that has only collisions but no
        missing-key violations must pass ``_validate_chain_coherence`` and
        only be flagged by ``_detect_upstream_key_collisions``.
        """
        from src.orchestrator.decomposition import (
            _detect_upstream_key_collisions,
            _validate_chain_coherence,
        )

        subtasks = [
            SubTask(
                description="A produces X.",
                required_capabilities=["a"],
                depends_on=[],
                output_schema={"x": "int"},
            ),
            SubTask(
                description="B also produces X.",
                required_capabilities=["b"],
                depends_on=[],
                output_schema={"x": "int"},
            ),
            SubTask(
                description="C requires X — satisfied by SOME producer.",
                required_capabilities=["c"],
                depends_on=[0, 1],
                output_schema={"y": "dict"},
                requires_upstream_keys=["x"],
            ),
        ]
        # Hard violations: none (x has a producer — in fact, two).
        assert _validate_chain_coherence(subtasks) == []
        # Soft warnings: one (the collision).
        warnings = _detect_upstream_key_collisions(subtasks)
        assert len(warnings) == 1


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

    def test_upstream_key_collision_surfaces_warning_without_tripping_flag(self, state):
        """Option A (per master plan §14.10): upstream key collisions are
        surfaced as warnings in ``state.errors`` but DO NOT trip the sticky
        ``decomposition_succeeded`` flag. The chain may still work at
        runtime if the successor happens to want the last-writing
        predecessor's value, so we don't want the yellow DECOMPOSITION
        FAILED banner — just a log trail and an errors-list entry for
        post-mortem visibility.
        """
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(
                    description="Parse sales CSV into records.",
                    required_capabilities=["parse-sales"],
                    depends_on=[],
                    output_schema={"records": "list[dict] — sales rows"},
                ),
                SubTaskItem(
                    description="Parse inventory CSV into records.",
                    required_capabilities=["parse-inventory"],
                    depends_on=[],
                    output_schema={"records": "list[dict] — inventory rows"},
                ),
                SubTaskItem(
                    description="Analyze upstream records.",
                    required_capabilities=["analyze"],
                    depends_on=[0, 1],
                    output_schema={"report": "dict"},
                    requires_upstream_keys=["records"],
                ),
            ]
        )
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        # The chain has a real collision — step 2's transitive predecessors
        # 0 and 1 both produce 'records'. But there's no HARD violation:
        # step 2 declares requires_upstream_keys=['records'], and 'records'
        # IS produced by the predecessor set (twice).
        assert cmd.update["decomposition_succeeded"] is True

        # The collision is reported as a soft warning in errors.
        errors = cmd.update["errors"]
        warning_entries = [
            e for e in errors
            if "coherence warning" in e.lower() and "'records'" in e
        ]
        assert len(warning_entries) == 1, (
            f"expected exactly one collision warning in errors; got: {errors}"
        )

        # The warning message must cite last-write-wins so a reader
        # understands the runtime consequence.
        assert "last-write-wins" in warning_entries[0]


class TestValidateOutputContractCoverage:
    """Hard validator for task-level required_keys coverage."""

    def test_empty_required_keys_is_trivially_satisfied(self):
        from src.orchestrator.decomposition import _validate_output_contract_coverage

        subtasks = [
            SubTask(
                description="Return stats.",
                required_capabilities=["stats"],
                depends_on=[],
                output_schema={"row_count": "int"},
            ),
        ]
        assert _validate_output_contract_coverage(subtasks, []) == []

    def test_union_of_subtask_keys_satisfies_contract(self):
        from src.orchestrator.decomposition import _validate_output_contract_coverage

        subtasks = [
            SubTask(
                description="Profile the dataset.",
                required_capabilities=["profile"],
                depends_on=[],
                output_schema={"n_rows": "int", "n_columns": "int"},
            ),
            SubTask(
                description="Summarize issues.",
                required_capabilities=["summarize-issues"],
                depends_on=[0],
                output_schema={
                    "quality_issues_found": "list[str]",
                    "issue_categories": "list[str]",
                },
                requires_upstream_keys=["n_rows", "n_columns"],
            ),
        ]
        assert _validate_output_contract_coverage(
            subtasks,
            ["n_rows", "n_columns", "quality_issues_found", "issue_categories"],
        ) == []

    def test_missing_required_keys_are_flagged(self):
        from src.orchestrator.decomposition import _validate_output_contract_coverage

        subtasks = [
            SubTask(
                description="Request the dataset file or a corrected path.",
                required_capabilities=["request-missing-file-or-path"],
                depends_on=[],
                output_schema={
                    "file_path_confirmed": "str",
                    "file_upload_provided": "bool",
                },
            ),
        ]
        violations = _validate_output_contract_coverage(
            subtasks,
            ["n_rows", "n_columns", "quality_issues_found", "issue_categories"],
        )
        assert len(violations) == 1
        assert "Missing keys" in violations[0]
        assert "n_rows" in violations[0]
        assert "issue_categories" in violations[0]


class TestDecompositionNodeOutputContractCoverage:
    def test_uncovered_required_keys_set_decomposition_succeeded_false(self):
        state = TaskState(
            task_description="Investigate the dataset and report quality issues.",
            output_contract=OutputContract(
                required_keys=[
                    "n_rows",
                    "n_columns",
                    "quality_issues_found",
                    "issue_categories",
                ],
            ),
        )
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(
                    description="Request the dataset file or a corrected path.",
                    required_capabilities=["request-missing-file-or-path"],
                    output_schema={
                        "file_path_confirmed": "str",
                        "file_upload_provided": "bool",
                    },
                ),
            ],
            reasoning="Need the file before the investigation can proceed.",
        )
        mock_tpe, _ = _make_mock_pool({"structured_response": structured})

        with patch("src.orchestrator.decomposition._get_agent"):
            with patch("concurrent.futures.ThreadPoolExecutor", mock_tpe):
                cmd = decomposition_node(state)

        assert cmd.update["decomposition_succeeded"] is False
        assert any(
            "Output contract coverage violation:" in err
            for err in cmd.update["errors"]
        )


# ---------------------------------------------------------------------------
# File output writer step (Phase 3 Step 12 — Option B)
# ---------------------------------------------------------------------------


class TestEnsureFileWriterLastStep:
    """Unit tests for ``_ensure_file_writer_last_step`` per §12 finding #10."""

    def test_empty_required_files_noop(self):
        from src.orchestrator.decomposition import _ensure_file_writer_last_step
        from src.schemas.task import SubTask

        subtasks = [
            SubTask(
                description="Parse CSV and return row count",
                required_capabilities=["parse-csv"],
                depends_on=[],
                output_schema={"row_count": "int"},
            ),
        ]
        result = _ensure_file_writer_last_step(subtasks, [])
        assert result == subtasks

    def test_empty_subtasks_noop(self):
        from src.orchestrator.decomposition import _ensure_file_writer_last_step

        result = _ensure_file_writer_last_step([], ["output.csv"])
        assert result == []

    def test_decomposer_compliance_is_noop(self):
        """When the last subtask already mentions the filename, no-op."""
        from src.orchestrator.decomposition import _ensure_file_writer_last_step
        from src.schemas.task import SubTask

        subtasks = [
            SubTask(
                description="Aggregate revenue per region",
                required_capabilities=["aggregate-revenue"],
                depends_on=[],
                output_schema={"region_revenues": "dict"},
            ),
            SubTask(
                description="Write the per-region summary to output.xlsx as Excel",
                required_capabilities=["write-xlsx-file"],
                depends_on=[0],
                output_schema={"output_file": "str"},
            ),
        ]
        result = _ensure_file_writer_last_step(subtasks, ["output.xlsx"])
        assert result == subtasks  # no append

    def test_decomposer_non_compliance_appends_writer(self):
        """When the last subtask doesn't mention the file, append a writer."""
        from src.orchestrator.decomposition import _ensure_file_writer_last_step
        from src.schemas.task import SubTask

        subtasks = [
            SubTask(
                description="Aggregate revenue per region and return a dict",
                required_capabilities=["aggregate-revenue"],
                depends_on=[],
                output_schema={
                    "region_revenues": "dict — region → total revenue",
                    "summary_row_count": "int",
                },
            ),
        ]
        result = _ensure_file_writer_last_step(subtasks, ["output.xlsx"])
        assert len(result) == 2
        writer = result[-1]
        assert "output.xlsx" in writer.description
        assert "excel" in writer.description.lower()
        assert writer.required_capabilities == ["write-xlsx-file"]
        assert writer.depends_on == [0]
        assert writer.output_schema == {"output_file": "str — path of the written file"}
        assert set(writer.requires_upstream_keys) == {"region_revenues", "summary_row_count"}

    def test_multi_file_writers_run_in_parallel_off_original(self):
        """Multiple required files — every writer depends on the ORIGINAL
        predecessor in parallel (NOT chained writer-after-writer).

        Regression guard for §12 finding #13: an earlier version chained
        writers so that writer 2's ``depends_on=[writer_1]`` and
        ``requires_upstream_keys=['output_file']``. That broke multi-file
        end-to-end — writer 2 only saw the filepath string of writer 1's
        output, not the original data, and could not serialize its own
        format. Empirically untested at the time because every authored
        eval task asks for at most one file.

        Correct shape: writer 1 reads original data → writes file A.
        Writer 2 reads SAME original data → writes file B (in different
        format). Topologically parallel.
        """
        from src.orchestrator.decomposition import _ensure_file_writer_last_step
        from src.schemas.task import SubTask

        subtasks = [
            SubTask(
                description="Summarize the incident report",
                required_capabilities=["summarize-incident"],
                depends_on=[],
                output_schema={
                    "summary": "str",
                    "metrics_table": "list[dict]",
                },
            ),
        ]
        result = _ensure_file_writer_last_step(
            subtasks, ["output.csv", "report.md"],
        )
        assert len(result) == 3
        # Both writers depend on the original last step (index 0), in parallel.
        assert result[1].depends_on == [0]
        assert result[2].depends_on == [0]  # NOT [1] — that was the bug
        # Both writers read the SAME original predecessor keys (so each
        # has access to the actual data, not just a sibling writer's path).
        assert set(result[1].requires_upstream_keys) == {"summary", "metrics_table"}
        assert set(result[2].requires_upstream_keys) == {"summary", "metrics_table"}
        # Each writer targets its own file + format.
        assert "output.csv" in result[1].description
        assert result[1].required_capabilities == ["write-csv-file"]
        assert "report.md" in result[2].description
        assert result[2].required_capabilities == ["write-md-file"]

    def test_case_insensitive_filename_match(self):
        """Decomposer may produce descriptions with different casing."""
        from src.orchestrator.decomposition import _ensure_file_writer_last_step
        from src.schemas.task import SubTask

        subtasks = [
            SubTask(
                description="Write the SUMMARY to OUTPUT.XLSX as an Excel workbook",
                required_capabilities=["write-xlsx-file"],
                depends_on=[],
                output_schema={"output_file": "str"},
            ),
        ]
        result = _ensure_file_writer_last_step(subtasks, ["output.xlsx"])
        assert result == subtasks  # case-insensitive match succeeds → no-op

    def test_markdown_extension_maps_to_format_label(self):
        from src.orchestrator.decomposition import _ensure_file_writer_last_step
        from src.schemas.task import SubTask

        subtasks = [
            SubTask(
                description="Summarize",
                required_capabilities=["summarize"],
                depends_on=[],
                output_schema={"summary": "str"},
            ),
        ]
        result = _ensure_file_writer_last_step(subtasks, ["report.md"])
        assert "Markdown" in result[-1].description

    def test_unknown_extension_uppercase_fallback(self):
        from src.orchestrator.decomposition import _ensure_file_writer_last_step
        from src.schemas.task import SubTask

        subtasks = [
            SubTask(
                description="Summarize",
                required_capabilities=["summarize"],
                depends_on=[],
                output_schema={"summary": "str"},
            ),
        ]
        result = _ensure_file_writer_last_step(subtasks, ["report.xyz"])
        # Unknown extension → required_capabilities uses ext slug; description
        # uses uppercased slug for the format label. No crash.
        assert result[-1].required_capabilities == ["write-xyz-file"]
        assert "XYZ" in result[-1].description


# ---------------------------------------------------------------------------
# Security audit finding #12, layer (A) — depends_on index validator (2026-04-20)
# ---------------------------------------------------------------------------


class TestValidateDependsOnIndices:
    """Decomposition-layer DAG validator. Pre-fix, invalid `depends_on`
    indices and cycles flowed through to the execution layer and caused
    IndexError / phantom-execution / silent-linearisation. Post-fix they
    flip ``decomposition_succeeded=False`` and trigger the 3x retry
    budget, matching the §12 finding #15 strict-gate pattern.
    """

    def _subtask(self, i: int, depends_on: list[int] | None = None) -> SubTask:
        return SubTask(
            description=f"subtask {i}",
            required_capabilities=[f"cap-{i}"],
            depends_on=depends_on or [],
        )

    def test_valid_dag_returns_empty_violations(self):
        from src.orchestrator.decomposition import _validate_depends_on_indices

        subtasks = [
            self._subtask(0),
            self._subtask(1, depends_on=[0]),
            self._subtask(2, depends_on=[0, 1]),
        ]
        assert _validate_depends_on_indices(subtasks) == []

    def test_empty_and_single_subtask_are_trivially_valid(self):
        from src.orchestrator.decomposition import _validate_depends_on_indices

        assert _validate_depends_on_indices([]) == []
        assert _validate_depends_on_indices([self._subtask(0)]) == []

    def test_out_of_range_positive_index(self):
        """Reporter's repro (a): ``depends_on=[99]`` on a 3-subtask chain."""
        from src.orchestrator.decomposition import _validate_depends_on_indices

        subtasks = [
            self._subtask(0),
            self._subtask(1),
            self._subtask(2, depends_on=[99]),
        ]
        violations = _validate_depends_on_indices(subtasks)
        assert violations
        assert any("out-of-range" in v and "99" in v for v in violations)

    def test_negative_index(self):
        """Reporter's repro (b): ``depends_on=[-1]`` phantom-executes."""
        from src.orchestrator.decomposition import _validate_depends_on_indices

        subtasks = [
            self._subtask(0),
            self._subtask(1, depends_on=[-1]),
        ]
        violations = _validate_depends_on_indices(subtasks)
        assert violations
        assert any("out-of-range" in v and "-1" in v for v in violations)

    def test_self_loop(self):
        from src.orchestrator.decomposition import _validate_depends_on_indices

        subtasks = [
            self._subtask(0),
            self._subtask(1, depends_on=[1]),  # self-reference
        ]
        violations = _validate_depends_on_indices(subtasks)
        assert violations
        assert any("self-loop" in v for v in violations)

    def test_two_subtask_cycle(self):
        """Reporter's repro (c): 0↔1 mutual dependency."""
        from src.orchestrator.decomposition import _validate_depends_on_indices

        subtasks = [
            self._subtask(0, depends_on=[1]),
            self._subtask(1, depends_on=[0]),
        ]
        violations = _validate_depends_on_indices(subtasks)
        assert violations
        assert any("cycle" in v.lower() for v in violations)

    def test_three_subtask_cycle(self):
        """Larger cycles also caught: 0 → 1 → 2 → 0."""
        from src.orchestrator.decomposition import _validate_depends_on_indices

        subtasks = [
            self._subtask(0, depends_on=[2]),
            self._subtask(1, depends_on=[0]),
            self._subtask(2, depends_on=[1]),
        ]
        violations = _validate_depends_on_indices(subtasks)
        assert violations
        assert any("cycle" in v.lower() for v in violations)

    def test_diamond_dag_is_valid(self):
        """Non-cyclic diamond (0 → 1, 0 → 2, 1+2 → 3) must pass."""
        from src.orchestrator.decomposition import _validate_depends_on_indices

        subtasks = [
            self._subtask(0),
            self._subtask(1, depends_on=[0]),
            self._subtask(2, depends_on=[0]),
            self._subtask(3, depends_on=[1, 2]),
        ]
        assert _validate_depends_on_indices(subtasks) == []

    def test_multiple_violations_all_reported(self):
        """When multiple violations exist, all are surfaced (caller decides
        whether to report them all or truncate)."""
        from src.orchestrator.decomposition import _validate_depends_on_indices

        subtasks = [
            self._subtask(0, depends_on=[99]),      # out-of-range
            self._subtask(1, depends_on=[-1]),       # negative
            self._subtask(2, depends_on=[2]),        # self-loop
        ]
        violations = _validate_depends_on_indices(subtasks)
        assert len(violations) >= 3
