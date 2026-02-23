"""Unit tests for the Iteration 2 Phase C LangGraph pipeline.

These tests verify:
- The pipeline compiles without errors given a registry and loader.
- The pipeline graph contains all expected nodes.
- The pipeline runs end-to-end with a seeded registry whose capabilities
  match the task (no LLM calls needed when there are no gaps — generators
  are no-ops and the loading node has nothing to load).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.pipeline import create_pipeline
from src.orchestrator.tool_loader import ToolLoader
from src.registry.store import SkillRegistry, seed_registry
from src.schemas.task import TaskState, TaskStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry():
    return SkillRegistry(collection_name=f"test-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def seeded_registry(registry):
    seed_registry(registry)
    return registry


@pytest.fixture
def mock_loader():
    """A ToolLoader mock — no real subprocess execution."""
    loader = MagicMock(spec=ToolLoader)
    return loader


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------


def test_pipeline_compiles(seeded_registry, mock_loader):
    graph = create_pipeline(seeded_registry, mock_loader).compile()
    assert graph is not None


def test_pipeline_contains_expected_nodes(seeded_registry, mock_loader):
    builder = create_pipeline(seeded_registry, mock_loader)
    node_names = set(builder.nodes.keys())
    expected = {
        "decomposition_node",
        "gap_detection",
        "classifier_node",
        "generate_mcp_tool",
        "generate_skill",
        "generate_agent",
        "verification_node",
        "loading_node",
    }
    assert expected.issubset(node_names), (
        f"Missing nodes: {expected - node_names}"
    )


# ---------------------------------------------------------------------------
# Full-pipeline smoke: all capabilities matched (no gaps → no LLM calls)
#
# When every required capability is found in the registry, state.gaps is []
# after gap_detection.  The classifier receives no gaps to classify, and all
# three generators are no-ops.  The loading_node finds no MCP_TOOL artifacts
# and sets status=COMPLETED.  This path exercises the full graph without any
# LLM call, making it a deterministic unit test.
# ---------------------------------------------------------------------------


def test_pipeline_no_gaps_path_completes(seeded_registry, mock_loader):
    """Pipeline runs to completion when all capabilities are in the registry.

    detect_gaps is mocked to return a guaranteed match so this test does not
    depend on ChromaDB embedding similarity scores (which would make it fragile
    across model/library updates).
    """
    fibonacci_cap = seeded_registry.get("fibonacci")
    assert fibonacci_cap is not None, "seed_registry must include 'fibonacci'"

    graph = create_pipeline(seeded_registry, mock_loader).compile()

    # Patch _get_agent so decomposition_node falls back immediately (returns
    # no structured_response → required_capabilities = [task_description]).
    class _FakeAgent:
        def invoke(self, *args, **kwargs):
            return {"structured_response": None}

    # Patch detect_gaps to guarantee a match, removing the embedding-similarity
    # dependency from this unit test.
    guaranteed_match = [(fibonacci_cap, 0.95, "compute fibonacci number")]
    with patch.object(seeded_registry, "detect_gaps", return_value=(guaranteed_match, [])):
        with patch(
            "src.orchestrator.decomposition._get_agent",
            return_value=_FakeAgent(),
        ):
            result = graph.invoke(
                TaskState(task_description="compute fibonacci number")
            )

    # No gaps → all generators are no-ops → loading_node sees no artifacts and
    # no gaps → status = COMPLETED.
    assert result["status"] == TaskStatus.COMPLETED
    assert isinstance(result["errors"], list)




# ---------------------------------------------------------------------------
# create_pipeline signature
# ---------------------------------------------------------------------------


def test_create_pipeline_requires_two_args(seeded_registry, mock_loader):
    """create_pipeline must accept (registry, loader)."""
    import inspect

    sig = inspect.signature(create_pipeline)
    params = list(sig.parameters.keys())
    assert params == ["registry", "loader"]
