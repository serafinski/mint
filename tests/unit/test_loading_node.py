"""Unit tests for the loading node."""

from __future__ import annotations

from unittest.mock import MagicMock

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


def test_mcp_tool_is_registered_without_host_import():
    """Security audit finding #1 (2026-04-19): loading_node must NOT call
    ``loader.load_tool`` or ``loader.execute_tool`` — those execute
    generated code on the host. Only ``register_tool`` runs, reading
    ``source_code`` without executing it. Downstream runtime uses the
    Docker sandbox exclusively."""
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

    # Host import never fires (security audit finding #1).
    loader.load_tool.assert_not_called()
    # register_tool fires for the MCP artifact only; skills/agents register
    # via their own path (see skill/agent branches further down).
    loader.register_tool.assert_called_once()


# ---------------------------------------------------------------------------
# Load failure is recorded but pipeline continues
# ---------------------------------------------------------------------------


def test_all_mcp_tools_register_regardless_of_source_shape():
    """Security audit finding #1 (2026-04-19): host-import path removed.
    The pre-fix behavior — load_tool raises → register anyway with source
    code only — is no longer relevant because load_tool is never called
    in this node. Both artifacts should register cleanly."""
    artifacts = [_mcp_artifact("bad"), _mcp_artifact("good")]
    state = TaskState(task_description="t", generated_artifacts=artifacts)

    loader = MagicMock()

    cmd = loading_node(state, loader, _make_registry())

    # Host import path is dead code; register_tool is the only side effect.
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
# FAILED when gaps exist and all MCP tool loads fail (observation fix)
# ---------------------------------------------------------------------------


def test_host_load_fails_but_registration_succeeds():
    """Gaps detected + host import fails but registration succeeds → COMPLETED.

    Tools with external deps (pandas, numpy) fail importlib on the host but
    still get registered with source_code for sandbox execution.
    """
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

    # Both registered despite host load failure
    assert loader.register_tool.call_count == 2
    assert cmd.update["status"] == TaskStatus.COMPLETED


def test_failed_when_gaps_and_all_registrations_fail():
    """Gaps detected + both host load AND registration fail → FAILED."""
    from src.schemas.task import GapItem

    artifacts = [_mcp_artifact("tool_a"), _mcp_artifact("tool_b")]
    gaps = [
        GapItem(description="tool a", query="tool a"),
        GapItem(description="tool b", query="tool b"),
    ]
    state = TaskState(task_description="t", gaps=gaps, generated_artifacts=artifacts)

    loader = MagicMock()
    loader.load_tool.side_effect = RuntimeError("import error")
    loader.register_tool.side_effect = RuntimeError("registry error")

    cmd = loading_node(state, loader, _make_registry())

    assert cmd.update["status"] == TaskStatus.FAILED
    assert len(cmd.update["errors"]) == 2  # one per failed registration


def test_failed_when_gaps_and_load_succeeds_but_register_fails():
    """Gaps detected + artifact loads but registration fails → FAILED (nothing deployed)."""
    from src.schemas.task import GapItem

    gaps = [GapItem(description="tool a", query="tool a")]
    artifact = _mcp_artifact("tool_a")
    state = TaskState(task_description="t", gaps=gaps, generated_artifacts=[artifact])

    loader = _make_loader(register_raises=RuntimeError("registry full"))

    cmd = loading_node(state, loader, _make_registry())

    # load succeeded, but register failed → registered=0, deployed=0, gaps non-empty → FAILED
    assert cmd.update["status"] == TaskStatus.FAILED
    assert any("registry full" in e for e in cmd.update["errors"])


def test_completed_when_gaps_and_skill_artifact_even_if_mcp_load_fails():
    """If an MCP load fails but a skill artifact was generated, status is COMPLETED."""
    from src.schemas.task import GapItem

    gaps = [GapItem(description="g", query="g")]
    artifacts = [_mcp_artifact("bad_mcp"), _skill_artifact("my-skill")]
    state = TaskState(task_description="t", gaps=gaps, generated_artifacts=artifacts)

    loader = MagicMock()
    loader.load_tool.side_effect = RuntimeError("import error")

    cmd = loading_node(state, loader, _make_registry())

    # Skill artifact counts as deployed → COMPLETED despite MCP failure
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
# Security audit finding #1 end-to-end — malicious top-level side effect
# ---------------------------------------------------------------------------


def test_does_not_execute_module_top_level_on_host(tmp_path):
    """Pre-fix, ``loading_node`` called ``loader.load_tool(artifact)`` which
    ran ``spec.loader.exec_module(module)`` in the parent Python process.
    A generated tool with a top-level side effect would fire during
    registration. Post-fix, no host execution happens — the marker file
    must NOT be created.

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
        "Security audit finding #1 regression — loading_node must not "
        "call load_tool (which runs exec_module) in production."
    )
    # Registration still succeeded.
    registry.add.assert_called_once()
    assert cmd.update["status"] == TaskStatus.COMPLETED
