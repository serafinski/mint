"""Unit tests for the task decomposition DeepAgent node."""

from __future__ import annotations

import concurrent.futures
from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.decomposition import decomposition_node
from src.schemas.task import (
    DecompositionResult,
    SubTaskItem,
    TaskState,
    TaskStatus,
)

# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Node tests — mocked DeepAgent
# ---------------------------------------------------------------------------


def _make_agent_result(structured: DecompositionResult | None):
    """Create a fake DeepAgent result dict."""
    return {"structured_response": structured, "messages": []}


class TestDecompositionNode:
    """Tests for decomposition_node with a mocked DeepAgent."""

    def _mock_agent(self, return_value):
        """Set up a mock agent that returns *return_value* on invoke."""
        mock = MagicMock()
        mock.invoke.return_value = return_value
        return mock

    def test_successful_decomposition(self):
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(
                    description="Load data",
                    required_capabilities=["read CSV file"],
                ),
                SubTaskItem(
                    description="Analyze data",
                    required_capabilities=["compute statistics"],
                    depends_on=[0],
                ),
            ],
            reasoning="Two-phase approach",
        )
        mock_agent = self._mock_agent(_make_agent_result(structured))

        state = TaskState(task_description="Analyze sales data from CSV")

        with patch("src.orchestrator.decomposition._get_agent", return_value=mock_agent):
            cmd = decomposition_node(state)

        assert cmd.goto == "gap_detection"
        assert len(cmd.update["subtasks"]) == 2
        assert cmd.update["subtasks"][0].description == "Load data"
        assert cmd.update["subtasks"][1].depends_on == [0]
        assert "read CSV file" in cmd.update["required_capabilities"]
        assert "compute statistics" in cmd.update["required_capabilities"]
        assert cmd.update["status"] == TaskStatus.DECOMPOSING
        assert cmd.update["errors"] == []

    def test_no_structured_response(self):
        """Agent returns no structured_response → fallback to whole task."""
        mock_agent = self._mock_agent({"structured_response": None, "messages": []})
        state = TaskState(task_description="Do something complex")

        with patch("src.orchestrator.decomposition._get_agent", return_value=mock_agent):
            cmd = decomposition_node(state)

        assert cmd.goto == "gap_detection"
        # Fallback: no subtasks, required_capabilities = [task_description]
        assert cmd.update["subtasks"] == []
        assert cmd.update["required_capabilities"] == ["Do something complex"]
        assert any("no structured response" in e for e in cmd.update["errors"])

    def test_timeout_handling(self):
        """Agent that hangs → timeout → partial results with error."""
        mock_agent = MagicMock()
        mock_agent.invoke.side_effect = lambda *a, **kw: (_ for _ in ()).throw(
            concurrent.futures.TimeoutError()
        )

        # Patch both _get_agent and the ThreadPoolExecutor to simulate timeout
        state = TaskState(task_description="Hang forever")

        with (
            patch("src.orchestrator.decomposition._get_agent", return_value=mock_agent),
            patch(
                "src.orchestrator.decomposition.concurrent.futures.ThreadPoolExecutor"
            ) as mock_pool_cls,
        ):
            mock_pool = MagicMock()
            mock_pool_cls.return_value.__enter__ = MagicMock(return_value=mock_pool)
            mock_pool_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_future = MagicMock()
            mock_future.result.side_effect = concurrent.futures.TimeoutError()
            mock_pool.submit.return_value = mock_future

            cmd = decomposition_node(state)

        assert cmd.goto == "gap_detection"
        assert cmd.update["subtasks"] == []
        assert cmd.update["required_capabilities"] == ["Hang forever"]
        assert any("timed out" in e for e in cmd.update["errors"])

    def test_agent_exception_handling(self):
        """Agent raises unexpected exception → error recorded, fallback."""
        mock_agent = MagicMock()

        state = TaskState(task_description="Trigger error")

        with (
            patch("src.orchestrator.decomposition._get_agent", return_value=mock_agent),
            patch(
                "src.orchestrator.decomposition.concurrent.futures.ThreadPoolExecutor"
            ) as mock_pool_cls,
        ):
            mock_pool = MagicMock()
            mock_pool_cls.return_value.__enter__ = MagicMock(return_value=mock_pool)
            mock_pool_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_future = MagicMock()
            mock_future.result.side_effect = RuntimeError("API key invalid")
            mock_pool.submit.return_value = mock_future

            cmd = decomposition_node(state)

        assert cmd.goto == "gap_detection"
        assert cmd.update["subtasks"] == []
        assert any("API key invalid" in e for e in cmd.update["errors"])

    def test_preserves_existing_errors(self):
        """Existing errors in state are carried forward."""
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(
                    description="Simple task",
                    required_capabilities=["do thing"],
                ),
            ],
        )
        mock_agent = self._mock_agent(_make_agent_result(structured))
        state = TaskState(
            task_description="Test",
            errors=["previous error"],
        )

        with patch("src.orchestrator.decomposition._get_agent", return_value=mock_agent):
            cmd = decomposition_node(state)

        assert "previous error" in cmd.update["errors"]
        assert len(cmd.update["subtasks"]) == 1

    def test_empty_subtasks_triggers_fallback(self):
        """Agent returns empty subtask list → fallback to whole task."""
        structured = DecompositionResult(subtasks=[])
        mock_agent = self._mock_agent(_make_agent_result(structured))
        state = TaskState(task_description="Simple one-liner")

        with patch("src.orchestrator.decomposition._get_agent", return_value=mock_agent):
            cmd = decomposition_node(state)

        assert cmd.update["required_capabilities"] == ["Simple one-liner"]

    def test_required_capabilities_collected(self):
        """All required_capabilities from subtasks are aggregated."""
        structured = DecompositionResult(
            subtasks=[
                SubTaskItem(
                    description="Step 1",
                    required_capabilities=["cap-a", "cap-b"],
                ),
                SubTaskItem(
                    description="Step 2",
                    required_capabilities=["cap-c"],
                    depends_on=[0],
                ),
            ],
        )
        mock_agent = self._mock_agent(_make_agent_result(structured))
        state = TaskState(task_description="Multi-step task")

        with patch("src.orchestrator.decomposition._get_agent", return_value=mock_agent):
            cmd = decomposition_node(state)

        assert cmd.update["required_capabilities"] == ["cap-a", "cap-b", "cap-c"]
