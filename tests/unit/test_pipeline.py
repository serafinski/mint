"""Unit tests for the Iteration 0 LangGraph pipeline."""

import uuid

import pytest

from src.orchestrator.pipeline import create_pipeline, gap_detection_node, intake_node
from src.registry.store import SkillRegistry, seed_registry
from src.schemas.task import TaskState


@pytest.fixture
def registry():
    return SkillRegistry(collection_name=f"test-{uuid.uuid4().hex[:8]}")


@pytest.fixture
def seeded_registry(registry):
    seed_registry(registry)
    return registry


# --- intake_node ---


def test_intake_node_sets_required_capabilities():
    state = TaskState(task_description="compute fibonacci number")
    result = intake_node(state)
    assert result.update == {"required_capabilities": ["compute fibonacci number"]}
    assert result.goto == "gap_detection"


# --- gap_detection_node ---


def test_gap_detection_finds_match(seeded_registry):
    state = TaskState(
        task_description="compute fibonacci",
        required_capabilities=["compute fibonacci number"],
    )
    result = gap_detection_node(state, seeded_registry)
    matches = result.update["matches"]
    gaps = result.update["gaps"]
    assert len(matches) == 1
    assert matches[0].capability.name == "fibonacci"
    assert len(gaps) == 0
    assert result.goto == "report"


def test_gap_detection_finds_gap(seeded_registry):
    state = TaskState(
        task_description="translate to Klingon",
        required_capabilities=["translate English to Klingon"],
    )
    result = gap_detection_node(state, seeded_registry)
    gaps = result.update["gaps"]
    matches = result.update["matches"]
    assert len(gaps) == 1
    assert len(matches) == 0


# --- Full pipeline ---


def test_pipeline_match_path(seeded_registry):
    graph = create_pipeline(seeded_registry).compile()
    result = graph.invoke({"task_description": "compute fibonacci number"})
    assert len(result["matches"]) == 1
    assert result["matches"][0].capability.name == "fibonacci"
    assert len(result["gaps"]) == 0


def test_pipeline_gap_path(seeded_registry):
    graph = create_pipeline(seeded_registry).compile()
    result = graph.invoke({"task_description": "deploy kubernetes cluster"})
    assert len(result["gaps"]) >= 1
    assert len(result["matches"]) == 0


def test_pipeline_empty_registry(registry):
    graph = create_pipeline(registry).compile()
    result = graph.invoke({"task_description": "anything"})
    assert len(result["matches"]) == 0
    assert len(result["gaps"]) == 1
