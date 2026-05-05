"""Unit tests for the JIT capability LangGraph pipeline.

These tests verify:
- The pipeline compiles without errors given a registry and loader.
- The pipeline graph contains all expected nodes.
- The pipeline runs end-to-end with a seeded registry whose capabilities
  match the task (no LLM calls needed when there are no gaps — generators
  are no-ops and the loading node has nothing to register).
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
    """A ToolLoader mock with no real subprocess execution."""
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
# and sets status=COMPLETED. This path exercises the full graph without any
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

    # No gaps -> all generators are no-ops -> loading_node sees no artifacts and
    # no gaps → status = COMPLETED.
    assert result["status"] == TaskStatus.COMPLETED
    assert isinstance(result["errors"], list)




# ---------------------------------------------------------------------------
# create_pipeline signature
# ---------------------------------------------------------------------------


def test_create_pipeline_compiles_with_positional_args(seeded_registry, mock_loader):
    """create_pipeline accepts (registry, loader) positionally and compiles.

    Exercises the public-API contract by calling ``create_pipeline`` with
    exactly two positional arguments and compiling the result, instead of
    introspecting the signature. A compiled ``CompiledStateGraph`` with an
    ``invoke`` method is the real guarantee callers rely on.
    """
    from langgraph.graph.state import CompiledStateGraph

    # Two positional args — the load-bearing contract other modules use
    # (evaluation harness and scripts).
    builder = create_pipeline(seeded_registry, mock_loader)
    graph = builder.compile()
    assert isinstance(graph, CompiledStateGraph)
    assert callable(getattr(graph, "invoke", None))


def test_create_pipeline_run_optimizer_flag(seeded_registry, mock_loader):
    """create_pipeline with run_optimizer=True compiles without error."""
    from src.registry.metrics import MetricsStore

    ms = MetricsStore()
    graph = create_pipeline(
        seeded_registry, mock_loader, run_optimizer=True, metrics_store=ms
    ).compile()
    assert graph is not None
    # optimizer_node should be present when flag is set
    builder = create_pipeline(
        seeded_registry, mock_loader, run_optimizer=True, metrics_store=ms
    )
    assert "optimizer_node" in builder.nodes


def test_classifier_produces_one_classified_gap_per_unmatched(seeded_registry, mock_loader):
    """Under per-gap classification, classifier_node emits N classified_gaps.

    Runs gap_detection + classifier in sequence with a mocked classify_gap so
    no LLM call is made. The assertion is that the classifier produces one
    ClassifiedGap per unmatched required capability, and that distinct types
    can be assigned across gaps in the same task (mixed-type workflow).
    """
    from src.orchestrator.classifier import classifier_node
    from src.orchestrator.gap_detection import gap_detection_node
    from src.schemas.capability import CapabilityType
    from src.schemas.task import ClassifiedGap, TaskState

    # Registry with no matches for three required capabilities → three raw gaps
    with patch.object(
        seeded_registry,
        "detect_gaps",
        return_value=([], [
            ("cap-a", "below threshold"),
            ("cap-b", "below threshold"),
            ("cap-c", "below threshold"),
        ]),
    ):
        state = TaskState(
            task_description="Task needing three capabilities",
            required_capabilities=["cap-a", "cap-b", "cap-c"],
        )
        cmd = gap_detection_node(state, seeded_registry)

    # One enumerated gap per unmatched required capability
    assert len(cmd.update["gaps"]) == 3

    # Hand the state through the classifier with a mocked classify_gap
    next_state = TaskState(
        task_description="Task needing three capabilities",
        required_capabilities=["cap-a", "cap-b", "cap-c"],
        matches=cmd.update["matches"],
        gaps=cmd.update["gaps"],
    )

    # Mix types across gaps: MCP_TOOL, SKILL, AGENT (one of each)
    type_by_query = {
        "cap-a": CapabilityType.MCP_TOOL,
        "cap-b": CapabilityType.SKILL,
        "cap-c": CapabilityType.AGENT,
    }

    def _mock_classify(gap, task_description, required_capabilities):
        return ClassifiedGap(
            description=gap.description,
            query=f"{gap.query}-abcdef",
            classified_type=type_by_query[gap.query],
            confidence=0.9,
            rationale="test mock",
        )

    with patch(
        "src.orchestrator.classifier.classify_gap",
        side_effect=_mock_classify,
    ):
        cmd = classifier_node(next_state)

    classified = cmd.update["classified_gaps"]
    assert len(classified) == 3
    types = {c.classified_type for c in classified}
    assert types == {CapabilityType.MCP_TOOL, CapabilityType.SKILL, CapabilityType.AGENT}


def test_optimizer_loading_node_routes_through_real_pipeline_node(
    seeded_registry, mock_loader
):
    """Optimizer-enabled loading_node rewrites only successful completion.

    This exercises the actual closure installed by ``create_pipeline`` instead
    of duplicating the routing logic in the test.
    """
    from langgraph.graph import END
    from langgraph.types import Command

    from src.registry.metrics import MetricsStore
    from src.schemas.task import TaskState

    cases = [
        (END, "optimizer_node"),
        ("generate_mcp_tool", "generate_mcp_tool"),
    ]

    for loading_goto, expected_goto in cases:
        state = TaskState(task_description=f"test {expected_goto}")
        update = {"marker": expected_goto}
        with patch(
            "src.orchestrator.pipeline.loading_node",
            return_value=Command(update=update, goto=loading_goto),
        ) as mocked_loading:
            builder = create_pipeline(
                seeded_registry,
                mock_loader,
                run_optimizer=True,
                metrics_store=MetricsStore(),
            )
            cmd = builder.nodes["loading_node"].runnable.invoke(state)

        assert cmd.goto == expected_goto
        assert cmd.update == update
        mocked_loading.assert_called_once_with(state, mock_loader, seeded_registry)
