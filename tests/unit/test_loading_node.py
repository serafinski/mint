"""Unit tests for the loading node."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.orchestrator.loading_node import loading_node
from src.orchestrator.tool_loader import LoadedTool, ToolResult
from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact, TaskState, TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mcp_artifact(name: str = "test_tool") -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.MCP_TOOL,
        source_code="# generated tool\nasync def test_tool(): pass",
    )


def _skill_artifact(name: str = "test-skill") -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.SKILL,
        skill_content="---\nname: test-skill\ndescription: A test skill.\n---\nDo stuff.",
    )


def _agent_artifact(name: str = "test-agent") -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.AGENT,
        agent_config={"system_prompt": "You are a test agent."},
    )


def _make_loader(
    *,
    load_raises: Exception | None = None,
    smoke_success: bool = True,
    register_raises: Exception | None = None,
) -> MagicMock:
    loader = MagicMock()
    loaded = MagicMock(spec=LoadedTool)

    if load_raises:
        loader.load_tool.side_effect = load_raises
    else:
        loader.load_tool.return_value = loaded

    smoke_result = ToolResult(
        success=smoke_success,
        error=None if smoke_success else "smoke failed",
    )
    loader.execute_tool.return_value = smoke_result

    if register_raises:
        loader.register_tool.side_effect = register_raises

    return loader


def _make_registry() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# MCP tools are loaded, smoke-tested, and registered
# ---------------------------------------------------------------------------


def test_mcp_tool_is_loaded_and_registered():
    artifact = _mcp_artifact("my_tool")
    state = TaskState(
        task_description="t",
        generated_artifacts=[artifact],
    )
    loader = _make_loader()
    registry = _make_registry()

    cmd = loading_node(state, loader, registry)

    loader.load_tool.assert_called_once_with(artifact)
    loader.execute_tool.assert_called_once()
    loader.register_tool.assert_called_once_with(artifact, registry)
    assert cmd.update["status"] == TaskStatus.COMPLETED
    assert cmd.goto == "__end__"


def test_multiple_mcp_tools_all_registered():
    artifacts = [_mcp_artifact(f"tool_{i}") for i in range(3)]
    state = TaskState(task_description="t", generated_artifacts=artifacts)
    loader = _make_loader()
    registry = _make_registry()

    loading_node(state, loader, registry)

    assert loader.load_tool.call_count == 3
    assert loader.register_tool.call_count == 3


# ---------------------------------------------------------------------------
# Skills and agents are skipped
# ---------------------------------------------------------------------------


def test_skill_artifacts_are_skipped():
    state = TaskState(
        task_description="t",
        generated_artifacts=[_skill_artifact()],
    )
    loader = _make_loader()
    registry = _make_registry()

    cmd = loading_node(state, loader, registry)

    loader.load_tool.assert_not_called()
    loader.register_tool.assert_not_called()
    # Skills count as total_artifacts, so status is COMPLETED (not FAILED)
    assert cmd.update["status"] == TaskStatus.COMPLETED


def test_agent_artifacts_are_skipped():
    state = TaskState(
        task_description="t",
        generated_artifacts=[_agent_artifact()],
    )
    loader = _make_loader()
    registry = _make_registry()

    cmd = loading_node(state, loader, registry)

    loader.load_tool.assert_not_called()
    assert cmd.update["status"] == TaskStatus.COMPLETED


def test_mixed_artifacts_only_mcp_tools_loaded():
    artifacts = [_mcp_artifact("mcp"), _skill_artifact("sk"), _agent_artifact("ag")]
    state = TaskState(task_description="t", generated_artifacts=artifacts)
    loader = _make_loader()
    registry = _make_registry()

    loading_node(state, loader, registry)

    # Only the MCP tool should be loaded/registered
    assert loader.load_tool.call_count == 1
    assert loader.register_tool.call_count == 1


# ---------------------------------------------------------------------------
# Load failure is recorded but pipeline continues
# ---------------------------------------------------------------------------


def test_load_failure_records_error_and_continues():
    artifacts = [_mcp_artifact("bad"), _mcp_artifact("good")]
    state = TaskState(task_description="t", generated_artifacts=artifacts)

    loader = MagicMock()
    loaded = MagicMock(spec=LoadedTool)
    # First call raises, second succeeds
    loader.load_tool.side_effect = [RuntimeError("import error"), loaded]
    loader.execute_tool.return_value = ToolResult(success=True)

    cmd = loading_node(state, loader, _make_registry())

    assert loader.register_tool.call_count == 1  # only 'good' was registered
    assert any("bad" in e for e in cmd.update["errors"])
    assert cmd.update["status"] == TaskStatus.COMPLETED  # total_artifacts > 0


# ---------------------------------------------------------------------------
# Smoke test failure logs warning but registration still happens
# ---------------------------------------------------------------------------


def test_smoke_failure_does_not_prevent_registration():
    artifact = _mcp_artifact()
    state = TaskState(task_description="t", generated_artifacts=[artifact])
    loader = _make_loader(smoke_success=False)

    cmd = loading_node(state, loader, _make_registry())

    # Registration still happens
    loader.register_tool.assert_called_once()
    assert cmd.update["status"] == TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# Register failure is recorded but pipeline continues
# ---------------------------------------------------------------------------


def test_register_failure_records_error():
    artifact = _mcp_artifact()
    state = TaskState(task_description="t", generated_artifacts=[artifact])
    loader = _make_loader(register_raises=RuntimeError("registry full"))

    cmd = loading_node(state, loader, _make_registry())

    assert any("registry full" in e for e in cmd.update["errors"])
    assert cmd.update["status"] == TaskStatus.COMPLETED  # artifact exists


# ---------------------------------------------------------------------------
# Status: FAILED when gaps exist but nothing was generated
# ---------------------------------------------------------------------------


def test_failed_status_when_gaps_but_no_artifacts():
    from src.schemas.task import GapItem

    state = TaskState(
        task_description="t",
        gaps=[GapItem(description="missing tool", query="missing tool")],
        generated_artifacts=[],
    )
    loader = _make_loader()

    cmd = loading_node(state, loader, _make_registry())

    assert cmd.update["status"] == TaskStatus.FAILED
    loader.load_tool.assert_not_called()


# ---------------------------------------------------------------------------
# Status: COMPLETED when no gaps and no artifacts (all capabilities matched)
# ---------------------------------------------------------------------------


def test_completed_when_no_gaps_and_no_artifacts():
    state = TaskState(
        task_description="t",
        gaps=[],
        generated_artifacts=[],
    )
    cmd = loading_node(state, _make_loader(), _make_registry())
    assert cmd.update["status"] == TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# Existing errors in state are preserved
# ---------------------------------------------------------------------------


def test_existing_errors_are_preserved():
    state = TaskState(
        task_description="t",
        errors=["prior error"],
        generated_artifacts=[],
    )
    cmd = loading_node(state, _make_loader(), _make_registry())
    assert "prior error" in cmd.update["errors"]
