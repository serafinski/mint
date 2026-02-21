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
