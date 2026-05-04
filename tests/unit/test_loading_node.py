"""Unit tests for the loading node."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.orchestrator.loading_node import loading_node
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
    register_raises: Exception | None = None,
) -> MagicMock:
    loader = MagicMock()
    if register_raises:
        loader.register_tool.side_effect = register_raises

    return loader


def _make_registry() -> MagicMock:
    return MagicMock()


# ---------------------------------------------------------------------------
# MCP tools are registered without host import
# ---------------------------------------------------------------------------


def test_mcp_tool_is_registered_without_host_import():
    """MCP registration must not execute generated source in the host process."""
    artifact = _mcp_artifact("my_tool")
    state = TaskState(
        task_description="t",
        generated_artifacts=[artifact],
    )
    loader = _make_loader()
    registry = _make_registry()

    cmd = loading_node(state, loader, registry)

    # The host-import path must NOT fire.
    loader.load_tool.assert_not_called()
    loader.execute_tool.assert_not_called()
    # register_tool is the only side effect.
    loader.register_tool.assert_called_once_with(artifact, registry)
    assert cmd.update["status"] == TaskStatus.COMPLETED
    assert cmd.goto == "__end__"


def test_multiple_mcp_tools_all_registered():
    artifacts = [_mcp_artifact(f"tool_{i}") for i in range(3)]
    state = TaskState(task_description="t", generated_artifacts=artifacts)
    loader = _make_loader()
    registry = _make_registry()

    loading_node(state, loader, registry)

    # Host import still not called; register_tool for each.
    loader.load_tool.assert_not_called()
    assert loader.register_tool.call_count == 3


# ---------------------------------------------------------------------------
# Skills and agents use the direct registry descriptor path
# ---------------------------------------------------------------------------


def test_skill_artifacts_register_descriptor_without_tool_loader():
    state = TaskState(
        task_description="t",
        generated_artifacts=[_skill_artifact()],
    )
    loader = _make_loader()
    registry = _make_registry()

    cmd = loading_node(state, loader, registry)

    loader.load_tool.assert_not_called()
    loader.register_tool.assert_not_called()
    registry.add.assert_called_once()
    assert cmd.update["status"] == TaskStatus.COMPLETED


def test_agent_artifacts_register_descriptor_without_tool_loader():
    state = TaskState(
        task_description="t",
        generated_artifacts=[_agent_artifact()],
    )
    loader = _make_loader()
    registry = _make_registry()

    cmd = loading_node(state, loader, registry)

    loader.load_tool.assert_not_called()
    loader.register_tool.assert_not_called()
    registry.add.assert_called_once()
    assert cmd.update["status"] == TaskStatus.COMPLETED


def test_mixed_artifacts_use_type_specific_registration_paths():
    artifacts = [_mcp_artifact("mcp"), _skill_artifact("sk"), _agent_artifact("ag")]
    state = TaskState(task_description="t", generated_artifacts=artifacts)
    loader = _make_loader()
    registry = _make_registry()

    loading_node(state, loader, registry)

    # Host import never fires.
    loader.load_tool.assert_not_called()
    # register_tool fires for the MCP artifact only; skills/agents register
    # via direct registry descriptors.
    loader.register_tool.assert_called_once()
    assert registry.add.call_count == 2


# ---------------------------------------------------------------------------
# Host import is not attempted
# ---------------------------------------------------------------------------


def test_all_mcp_tools_register_regardless_of_source_shape():
    """MCP artifacts register from source metadata regardless of import shape."""
    artifacts = [_mcp_artifact("bad"), _mcp_artifact("good")]
    state = TaskState(task_description="t", generated_artifacts=artifacts)

    loader = MagicMock()

    cmd = loading_node(state, loader, _make_registry())

    # Host import path is not part of loading_node.
    loader.load_tool.assert_not_called()
    loader.execute_tool.assert_not_called()
    assert loader.register_tool.call_count == 2
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
# Status when gaps exist and registration succeeds or fails
# ---------------------------------------------------------------------------


def test_mcp_registration_succeeds_without_host_import():
    """Gaps detected + MCP registration succeeds -> COMPLETED."""
    from src.schemas.task import GapItem

    artifacts = [_mcp_artifact("tool_a"), _mcp_artifact("tool_b")]
    gaps = [
        GapItem(description="tool a", query="tool a"),
        GapItem(description="tool b", query="tool b"),
    ]
    state = TaskState(task_description="t", gaps=gaps, generated_artifacts=artifacts)

    loader = MagicMock()
    loader.load_tool.side_effect = RuntimeError("import error")

    cmd = loading_node(state, loader, _make_registry())

    loader.load_tool.assert_not_called()
    assert loader.register_tool.call_count == 2
    assert cmd.update["status"] == TaskStatus.COMPLETED


def test_failed_when_gaps_and_all_registrations_fail():
    """Gaps detected + all registrations fail -> FAILED."""
    from src.schemas.task import GapItem

    artifacts = [_mcp_artifact("tool_a"), _mcp_artifact("tool_b")]
    gaps = [
        GapItem(description="tool a", query="tool a"),
        GapItem(description="tool b", query="tool b"),
    ]
    state = TaskState(task_description="t", gaps=gaps, generated_artifacts=artifacts)

    loader = MagicMock()
    loader.register_tool.side_effect = RuntimeError("registry error")

    cmd = loading_node(state, loader, _make_registry())

    assert cmd.update["status"] == TaskStatus.FAILED
    assert len(cmd.update["errors"]) == 2  # one per failed registration


def test_failed_when_gaps_and_load_succeeds_but_register_fails():
    """Gaps detected + registration fails -> FAILED when nothing deployed."""
    from src.schemas.task import GapItem

    gaps = [GapItem(description="tool a", query="tool a")]
    artifact = _mcp_artifact("tool_a")
    state = TaskState(task_description="t", gaps=gaps, generated_artifacts=[artifact])

    loader = _make_loader(register_raises=RuntimeError("registry full"))

    cmd = loading_node(state, loader, _make_registry())

    assert cmd.update["status"] == TaskStatus.FAILED
    assert any("registry full" in e for e in cmd.update["errors"])


def test_completed_when_gaps_and_skill_artifact_registers():
    """If a skill artifact registers, status is COMPLETED."""
    from src.schemas.task import GapItem

    gaps = [GapItem(description="g", query="g")]
    artifacts = [_mcp_artifact("bad_mcp"), _skill_artifact("my-skill")]
    state = TaskState(task_description="t", gaps=gaps, generated_artifacts=artifacts)

    loader = MagicMock()

    cmd = loading_node(state, loader, _make_registry())

    # Skill artifact counts as deployed.
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


# ---------------------------------------------------------------------------
# Malicious top-level side effect must not execute on host
# ---------------------------------------------------------------------------


def test_does_not_execute_module_top_level_on_host(tmp_path):
    """Generated MCP source is registered without executing top-level code.

    Uses a real ToolLoader (not a MagicMock) to ensure that if the code
    path accidentally re-introduces ``load_tool``, the test catches it."""
    from src.orchestrator.tool_loader import ToolLoader
    from src.registry.store import SkillRegistry

    marker = tmp_path / "pwned"
    source = (
        f"from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('fired at import')\n"
        "async def benign_tool(x: int) -> int:\n"
        "    return x + 1\n"
    )

    artifact = GeneratedArtifact(
        capability_name="benign-tool",
        capability_type=CapabilityType.MCP_TOOL,
        source_code=source,
    )
    state = TaskState(task_description="t", generated_artifacts=[artifact])

    real_loader = ToolLoader()
    registry = MagicMock(spec=SkillRegistry)

    cmd = loading_node(state, real_loader, registry)

    # The decisive assertion: the malicious side effect did NOT fire.
    assert not marker.exists(), (
        f"Top-level code executed on host: {marker} was created. "
        "loading_node must not "
        "call load_tool (which runs exec_module) in production."
    )
    # Registration still succeeded.
    registry.add.assert_called_once()
    assert cmd.update["status"] == TaskStatus.COMPLETED
