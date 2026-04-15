"""Tests for src/optimizer/operations.py."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.optimizer.operations import (
    MergeAgentsResult,
    MergeAgentsSubagent,
    MergeSkillsResult,
    MergeToolsResult,
    SplitResult,
    SplitVariant,
    _MAX_AGENT_SUBAGENT_BUDGET,
    _merge_agents,
    _merge_skills,
    _merge_tools,
    merge,
    prune,
    split,
    verify_and_register_or_rollback,
)
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GeneratedArtifact
from src.schemas.verification import (
    Severity,
    VerificationFinding,
    VerificationReport,
)


def _make_cap(
    name: str = "test-cap",
    cap_type: CapabilityType = CapabilityType.SKILL,
    domain: str = "general",
    tags: list[str] | None = None,
    is_active: bool = True,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name=name,
        description=f"Description for {name}",
        capability_type=cap_type,
        domain=domain,
        tags=tags or [],
        is_active=is_active,
    )


class TestPrune:
    def test_prunes_zero_invocation_capabilities(self):
        registry = MagicMock()
        registry.list_all.return_value = [
            _make_cap("cap-a"),
            _make_cap("cap-b"),
        ]

        store = MagicMock()
        store.invocations_since.return_value = 0

        result = prune(registry, store, window_days=30)

        assert set(result) == {"cap-a", "cap-b"}
        assert registry.deactivate.call_count == 2
        assert store.record_event.call_count == 2

    def test_skips_capabilities_with_recent_invocations(self):
        registry = MagicMock()
        registry.list_all.return_value = [
            _make_cap("cap-a"),
            _make_cap("cap-b"),
        ]

        store = MagicMock()
        # cap-a has invocations, cap-b does not
        store.invocations_since.side_effect = [5, 0]

        result = prune(registry, store, window_days=30)

        assert result == ["cap-b"]
        registry.deactivate.assert_called_once_with("cap-b")

    def test_empty_registry(self):
        registry = MagicMock()
        registry.list_all.return_value = []
        store = MagicMock()

        result = prune(registry, store)
        assert result == []

    def test_custom_window_days(self):
        registry = MagicMock()
        registry.list_all.return_value = [_make_cap("cap-a")]

        store = MagicMock()
        store.invocations_since.return_value = 0

        prune(registry, store, window_days=7)

        # Verify the cutoff was passed correctly
        args = store.invocations_since.call_args
        cutoff = args[0][1]
        now = datetime.now(UTC)
        # Cutoff should be ~7 days ago
        assert (now - cutoff).days in (6, 7)

    def test_prune_skips_pinned_capability(self):
        """Pinned capabilities are never pruned, even with zero invocations."""
        pinned_cap = _make_cap("csv-parser")
        pinned_cap = pinned_cap.model_copy(update={"metadata": {"pinned": "true"}})
        registry = MagicMock()
        registry.list_all.return_value = [pinned_cap]
        store = MagicMock()
        store.invocations_since.return_value = 0

        result = prune(registry, store, window_days=30)

        assert result == []
        registry.deactivate.assert_not_called()

    def test_prune_deactivates_unpinned_zero_invocation(self):
        """Unpinned capabilities with zero invocations are still pruned (regression)."""
        registry = MagicMock()
        registry.list_all.return_value = [_make_cap("ephemeral")]
        store = MagicMock()
        store.invocations_since.return_value = 0

        result = prune(registry, store, window_days=30)

        assert result == ["ephemeral"]
        registry.deactivate.assert_called_once_with("ephemeral")

    def test_prune_mixed_registry_only_deactivates_unpinned(self):
        """Mixed registry: pinned survives, active-with-invocations survives, idle-unpinned pruned."""
        pinned = _make_cap("pinned-cap")
        pinned = pinned.model_copy(update={"metadata": {"pinned": "true"}})
        active = _make_cap("active-cap")
        idle = _make_cap("idle-cap")

        registry = MagicMock()
        registry.list_all.return_value = [pinned, active, idle]
        store = MagicMock()
        # pinned is skipped before invocations_since is called
        # active has invocations, idle has none
        store.invocations_since.side_effect = [5, 0]

        result = prune(registry, store, window_days=30)

        assert result == ["idle-cap"]
        registry.deactivate.assert_called_once_with("idle-cap")


class TestSplit:
    def test_rejects_mcp_tool(self):
        registry = MagicMock()
        registry.get.return_value = _make_cap("tool-a", cap_type=CapabilityType.MCP_TOOL)
        store = MagicMock()

        with pytest.raises(ValueError, match="Split is not supported for MCP_TOOL"):
            split(registry, store, "tool-a")

    def test_raises_key_error_for_missing_capability(self):
        registry = MagicMock()
        registry.get.return_value = None
        store = MagicMock()

        with pytest.raises(KeyError, match="not found"):
            split(registry, store, "nonexistent")

    @patch("src.optimizer.operations.init_chat_model")
    def test_split_skill(self, mock_init):
        cap = _make_cap("data-workflow", cap_type=CapabilityType.SKILL, domain="data", tags=["etl"])

        registry = MagicMock()
        # Return parent for its own ID; None for variant names (no collision).
        registry.get.side_effect = lambda n: cap if n == "data-workflow" else None

        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitResult(
            analysis="The skill fails for large datasets",
            variants=[
                SplitVariant(name="data-workflow-small", description="For small datasets"),
                SplitVariant(name="data-workflow-large", description="For large datasets"),
            ],
        )

        result = split(registry, store, "data-workflow")

        assert result == ["data-workflow-small", "data-workflow-large"]
        # Parent deactivated
        registry.deactivate.assert_called_once_with("data-workflow")
        # Two new capabilities registered
        assert registry.add.call_count == 2
        # New caps inherit type and domain
        for call in registry.add.call_args_list:
            new_cap = call[0][0]
            assert new_cap.capability_type == CapabilityType.SKILL
            assert new_cap.domain == "data"

    @patch("src.optimizer.operations.init_chat_model")
    def test_split_inherits_tags_from_parent(self, mock_init):
        cap = _make_cap(
            "data-workflow", cap_type=CapabilityType.SKILL, domain="data", tags=["etl", "csv"]
        )
        registry = MagicMock()
        registry.get.side_effect = lambda n: cap if n == "data-workflow" else None
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitResult(
            analysis="Split by format",
            variants=[
                SplitVariant(name="variant-a", description="CSV variant"),
                SplitVariant(name="variant-b", description="JSON variant"),
            ],
        )

        split(registry, store, "data-workflow")

        for call in registry.add.call_args_list:
            new_cap = call[0][0]
            assert new_cap.tags == ["etl", "csv"]

    @patch("src.optimizer.operations.init_chat_model")
    def test_split_agent(self, mock_init):
        cap = _make_cap("analysis-agent", cap_type=CapabilityType.AGENT)

        registry = MagicMock()
        registry.get.side_effect = lambda n: cap if n == "analysis-agent" else None
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitResult(
            analysis="Agent scope too broad",
            variants=[
                SplitVariant(name="profiling-agent", description="Data profiling"),
                SplitVariant(name="correlation-agent", description="Correlation analysis"),
            ],
        )

        result = split(registry, store, "analysis-agent")

        assert len(result) == 2
        for call in registry.add.call_args_list:
            new_cap = call[0][0]
            assert new_cap.capability_type == CapabilityType.AGENT

    @patch("src.optimizer.operations.init_chat_model")
    def test_split_repair_pass_on_first_failure(self, mock_init):
        """split() retries once when LLM output fails validation."""
        cap = _make_cap("data-workflow", cap_type=CapabilityType.SKILL)
        registry = MagicMock()
        registry.get.side_effect = lambda n: cap if n == "data-workflow" else None
        store = MagicMock()

        good_result = SplitResult(
            analysis="Second attempt was correct",
            variants=[
                SplitVariant(name="variant-a", description="Handles A"),
                SplitVariant(name="variant-b", description="Handles B"),
            ],
        )

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        # First call fails, second (repair) succeeds
        mock_structured.invoke.side_effect = [RuntimeError("bad JSON"), good_result]

        result = split(registry, store, "data-workflow")
        assert result == ["variant-a", "variant-b"]
        assert mock_structured.invoke.call_count == 2

    @patch("src.optimizer.operations.init_chat_model")
    def test_split_raises_if_repair_also_fails(self, mock_init):
        """split() raises if both initial call and repair pass fail."""
        cap = _make_cap("data-workflow", cap_type=CapabilityType.SKILL)
        registry = MagicMock()
        registry.get.return_value = cap
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.side_effect = RuntimeError("always fails")

        with pytest.raises(RuntimeError, match="always fails"):
            split(registry, store, "data-workflow")
        assert mock_structured.invoke.call_count == 2

    @patch("src.optimizer.operations.init_chat_model")
    def test_split_aborts_if_any_variant_name_collides(self, mock_init):
        """split(): if any variant name already exists, abort entirely (all-or-nothing)."""
        cap = _make_cap("data-workflow", cap_type=CapabilityType.SKILL, domain="data", tags=["etl"])

        existing = _make_cap("data-workflow-small", cap_type=CapabilityType.SKILL)

        def _get(name):
            if name == "data-workflow":
                return cap
            if name == "data-workflow-small":
                return existing  # collision
            return None

        registry = MagicMock()
        registry.get.side_effect = _get
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitResult(
            analysis="Split by dataset size",
            variants=[
                SplitVariant(name="data-workflow-small", description="Small datasets"),
                SplitVariant(name="data-workflow-large", description="Large datasets"),
            ],
        )

        with pytest.raises(ValueError, match="already exists in registry"):
            split(registry, store, "data-workflow")

        # All-or-nothing: no variants registered, parent not deactivated
        assert registry.add.call_count == 0
        registry.deactivate.assert_not_called()

    @patch("src.optimizer.operations.init_chat_model")
    def test_split_does_not_deactivate_parent_when_all_variants_collide(self, mock_init):
        """split(): parent is kept active if every variant name already exists."""
        cap = _make_cap("data-workflow", cap_type=CapabilityType.SKILL)

        def _get(name):
            if name == "data-workflow":
                return cap
            # Every variant name collides
            return _make_cap(name, cap_type=CapabilityType.SKILL)

        registry = MagicMock()
        registry.get.side_effect = _get
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitResult(
            analysis="Split",
            variants=[
                SplitVariant(name="variant-a", description="A"),
                SplitVariant(name="variant-b", description="B"),
            ],
        )

        with pytest.raises(ValueError, match="already exists in registry"):
            split(registry, store, "data-workflow")

        registry.add.assert_not_called()
        registry.deactivate.assert_not_called()


# ---------------------------------------------------------------------------
# Step 5 — verify_and_register_or_rollback helper
# ---------------------------------------------------------------------------


def _passing_report(name: str) -> VerificationReport:
    return VerificationReport(
        capability_name=name,
        findings=[],
        overall_passed=True,
        tier_reached=3,
    )


def _failing_report(name: str, *, severity: Severity = Severity.CRITICAL) -> VerificationReport:
    return VerificationReport(
        capability_name=name,
        findings=[
            VerificationFinding(
                tier=1,
                category="hardcoded_data",
                severity=severity,
                message="simulated gate failure",
            )
        ],
        overall_passed=False,
        tier_reached=1,
    )


def _skill_artifact(name: str = "merged-skill") -> GeneratedArtifact:
    return GeneratedArtifact(
        capability_name=name,
        capability_type=CapabilityType.SKILL,
        description="merged skill",
        skill_content="---\nname: merged-skill\ndescription: merged\n---\n\nDo things.\n",
        input_schema={"x": "int"},
        output_schema={"y": "int"},
    )


class TestVerifyAndRegisterOrRollback:
    """Step 5 — the shared helper that merge and split funnel through."""

    @patch("src.verification.gate.verify")
    def test_gate_pass_registers_skill_and_records_event(self, mock_verify):
        mock_verify.return_value = _passing_report("merged-skill")
        registry = MagicMock()
        store = MagicMock()

        ok, reason = verify_and_register_or_rollback(
            _skill_artifact(),
            registry,
            store,
            event_type="merged",
            event_details="from skill-a, skill-b",
        )

        assert ok is True
        assert reason is None
        # Registry was called with a descriptor carrying the skill content
        assert registry.add.call_count == 1
        descriptor = registry.add.call_args[0][0]
        assert descriptor.capability_type == CapabilityType.SKILL
        assert descriptor.metadata.get("skill_content", "").startswith("---")
        # Success event recorded with the caller-provided event_type
        success_events = [
            c for c in store.record_event.call_args_list if c[0][1] == "merged"
        ]
        assert len(success_events) == 1

    @patch("src.verification.gate.verify")
    def test_gate_rollback_does_not_touch_registry(self, mock_verify):
        mock_verify.return_value = _failing_report("merged-skill")
        registry = MagicMock()
        store = MagicMock()

        ok, reason = verify_and_register_or_rollback(
            _skill_artifact(),
            registry,
            store,
            event_type="merged",
            event_details="from skill-a, skill-b",
        )

        assert ok is False
        assert reason is not None
        assert "hardcoded_data" in reason
        registry.add.assert_not_called()
        registry.deactivate.assert_not_called()
        # Rollback event recorded
        rollback_events = [
            c for c in store.record_event.call_args_list
            if c[0][1] == "verification_rollback"
        ]
        assert len(rollback_events) == 1

    @patch("src.verification.gate.verify")
    def test_high_severity_also_triggers_rollback(self, mock_verify):
        """HIGH findings are blocking in the gate, so the helper must
        treat HIGH-only failures as rollbacks (not just CRITICAL)."""
        mock_verify.return_value = _failing_report(
            "merged-skill", severity=Severity.HIGH
        )
        registry = MagicMock()
        store = MagicMock()

        ok, _ = verify_and_register_or_rollback(
            _skill_artifact(),
            registry,
            store,
            event_type="merged",
            event_details="x",
        )
        assert ok is False
        registry.add.assert_not_called()

    @patch("src.verification.gate.verify")
    def test_verify_raises_is_swallowed(self, mock_verify):
        """Never-raises: if verify() blows up, the helper records a
        rollback and returns (False, reason)."""
        mock_verify.side_effect = RuntimeError("gate exploded")
        registry = MagicMock()
        store = MagicMock()

        ok, reason = verify_and_register_or_rollback(
            _skill_artifact(),
            registry,
            store,
            event_type="merged",
            event_details="x",
        )
        assert ok is False
        assert "gate exploded" in reason
        registry.add.assert_not_called()

    @patch("src.verification.gate.verify")
    def test_register_raises_is_swallowed(self, mock_verify):
        """If registry.add raises after a gate pass, the helper still
        returns (False, reason) and records a rollback event — the
        partial-state case is recoverable by the optimizer loop."""
        mock_verify.return_value = _passing_report("merged-skill")
        registry = MagicMock()
        registry.add.side_effect = RuntimeError("chroma locked")
        store = MagicMock()

        ok, reason = verify_and_register_or_rollback(
            _skill_artifact(),
            registry,
            store,
            event_type="merged",
            event_details="x",
        )
        assert ok is False
        assert "chroma locked" in reason


# ---------------------------------------------------------------------------
# Step 6 — merge() dispatch + per-type merge functions
# ---------------------------------------------------------------------------


def _cap_with_source(
    name: str,
    cap_type: CapabilityType = CapabilityType.MCP_TOOL,
    source_code: str | None = None,
    skill_content: str | None = None,
    agent_config: dict | None = None,
    tags: list[str] | None = None,
    domain: str = "general",
) -> CapabilityDescriptor:
    """Capability descriptor with executable content embedded in metadata.

    Mirrors the real registry shape where ``tool_loader.register_tool``
    writes ``metadata['source_code']`` and ``loading_node`` writes
    ``metadata['skill_content']`` / ``metadata['agent_config']``. Merge
    helpers pull parent executable content from these metadata keys.
    """
    import json as _json
    metadata: dict[str, str] = {"generated": "true"}
    if source_code is not None:
        metadata["source_code"] = source_code
    if skill_content is not None:
        metadata["skill_content"] = skill_content
    if agent_config is not None:
        metadata["agent_config"] = _json.dumps(agent_config)
    return CapabilityDescriptor(
        name=name,
        description=f"Description for {name}",
        capability_type=cap_type,
        domain=domain,
        tags=tags or [],
        metadata=metadata,
    )


class TestMergeDispatch:
    """merge() routes by capability type and rejects cross-type."""

    def test_cross_type_raises_value_error(self):
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "tool-a": _cap_with_source("tool-a", CapabilityType.MCP_TOOL, source_code="..."),
            "skill-b": _cap_with_source("skill-b", CapabilityType.SKILL, skill_content="..."),
        }[n]
        store = MagicMock()

        with pytest.raises(ValueError, match="cross-type"):
            merge(registry, store, ["tool-a", "skill-b"])

    def test_missing_capability_raises_key_error(self):
        registry = MagicMock()
        registry.get.return_value = None
        store = MagicMock()

        with pytest.raises(KeyError, match="not found"):
            merge(registry, store, ["nonexistent-a", "nonexistent-b"])

    def test_single_capability_rejected(self):
        registry = MagicMock()
        registry.get.return_value = _cap_with_source(
            "cap-a", CapabilityType.SKILL, skill_content="..."
        )
        store = MagicMock()

        with pytest.raises(ValueError, match="at least 2"):
            merge(registry, store, ["cap-a"])

    @patch("src.optimizer.operations._merge_tools")
    def test_all_mcp_tool_routes_to_merge_tools(self, mock_merge_tools):
        mock_merge_tools.return_value = "merged-tool"
        registry = MagicMock()
        registry.get.side_effect = lambda n: _cap_with_source(
            n, CapabilityType.MCP_TOOL, source_code="async def f(): pass"
        )
        store = MagicMock()

        result = merge(registry, store, ["tool-a", "tool-b"])
        assert result == "merged-tool"
        mock_merge_tools.assert_called_once()

    @patch("src.optimizer.operations._merge_skills")
    def test_all_skill_routes_to_merge_skills(self, mock_merge_skills):
        mock_merge_skills.return_value = "merged-skill"
        registry = MagicMock()
        registry.get.side_effect = lambda n: _cap_with_source(
            n, CapabilityType.SKILL, skill_content="---\nname: x\n---\n"
        )
        store = MagicMock()

        result = merge(registry, store, ["skill-a", "skill-b"])
        assert result == "merged-skill"
        mock_merge_skills.assert_called_once()

    @patch("src.optimizer.operations._merge_agents")
    def test_all_agent_routes_to_merge_agents(self, mock_merge_agents):
        mock_merge_agents.return_value = "merged-agent"
        registry = MagicMock()
        registry.get.side_effect = lambda n: _cap_with_source(
            n, CapabilityType.AGENT,
            agent_config={"system_prompt": "you are an agent"},
        )
        store = MagicMock()

        result = merge(registry, store, ["agent-a", "agent-b"])
        assert result == "merged-agent"
        mock_merge_agents.assert_called_once()


class TestMergeTools:
    """_merge_tools: MCP_TOOL parents → combined FastMCP source."""

    def _mcp_cap(self, name: str, tags: list[str] | None = None) -> CapabilityDescriptor:
        return _cap_with_source(
            name,
            cap_type=CapabilityType.MCP_TOOL,
            source_code=(
                f'"""Tool {name}."""\n'
                "from typing import Annotated\n"
                "from mcp.server.fastmcp import FastMCP\n"
                "from pydantic import Field\n"
                f"mcp = FastMCP(\"{name}\")\n"
                "@mcp.tool()\n"
                f"async def {name.replace('-', '_')}(x: Annotated[int, Field(description='x')]) -> dict:\n"
                "    return {'y': x}\n"
            ),
            tags=tags,
            domain="data",
        )

    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_happy_path_registers_and_deactivates_parents(
        self, mock_init, mock_verify
    ):
        cap_a = self._mcp_cap("tool-a", tags=["csv"])
        cap_b = self._mcp_cap("tool-b", tags=["json"])
        registry = MagicMock()
        # Parent lookups + collision check for merged name.
        registry.get.side_effect = lambda n: {
            "tool-a": cap_a, "tool-b": cap_b, "merged-parser": None,
        }.get(n)
        store = MagicMock()
        # Gate passes on the merged artifact.
        mock_verify.return_value = _passing_report("merged-parser")

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeToolsResult(
            name="merged-parser",
            description="Merged parser",
            rationale="Both parse data",
            combined_source_code=(
                '"""Merged."""\n'
                "from typing import Annotated\n"
                "from mcp.server.fastmcp import FastMCP\n"
                "from pydantic import Field\n"
                'mcp = FastMCP("merged")\n'
                "@mcp.tool()\n"
                "async def merged_parser(x: Annotated[int, Field(description='x')]) -> dict:\n"
                "    return {'y': x}\n"
                'if __name__ == "__main__":\n'
                "    mcp.run()\n"
            ),
            combined_pip_packages=[],
        )

        result = merge(registry, store, ["tool-a", "tool-b"])

        assert result == "merged-parser"
        # Gate was called
        assert mock_verify.call_count == 1
        # Registry add called once (via ToolLoader.register_tool → registry.add)
        assert registry.add.call_count == 1
        descriptor = registry.add.call_args[0][0]
        assert descriptor.capability_type == CapabilityType.MCP_TOOL
        assert descriptor.metadata.get("source_code", "").startswith('"""Merged."""')
        # Both parents deactivated AFTER registration
        assert registry.deactivate.call_count == 2
        assert set(c.args[0] for c in registry.deactivate.call_args_list) == {
            "tool-a", "tool-b"
        }
        # Union of parent tags
        assert set(descriptor.tags) == {"csv", "json"}

    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_gate_rollback_keeps_parents_active(
        self, mock_init, mock_verify
    ):
        cap_a = self._mcp_cap("tool-a")
        cap_b = self._mcp_cap("tool-b")
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "tool-a": cap_a, "tool-b": cap_b, "merged-parser": None,
        }.get(n)
        store = MagicMock()
        # Gate REJECTS the merged artifact.
        mock_verify.return_value = _failing_report("merged-parser")

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeToolsResult(
            name="merged-parser",
            description="Merged parser",
            rationale="Both parse data",
            combined_source_code="bad source",
            combined_pip_packages=[],
        )

        result = merge(registry, store, ["tool-a", "tool-b"])

        # Merge returns None on gate rejection
        assert result is None
        # Parents NOT deactivated — atomic rollback
        registry.deactivate.assert_not_called()
        # registry.add either wasn't called or was called inside the
        # helper but not committed. The atomicity guarantee is on the
        # visible side-effect: parents must stay active.
        # (ToolLoader.register_tool calls registry.add before we see
        # the return value — that's a pre-existing quirk. The test
        # asserts on the deactivate, which is the important invariant.)

    @patch("src.optimizer.operations.init_chat_model")
    def test_name_collision_aborts_without_calling_gate(self, mock_init):
        cap_a = self._mcp_cap("tool-a")
        cap_b = self._mcp_cap("tool-b")
        existing = self._mcp_cap("already-exists")
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "tool-a": cap_a, "tool-b": cap_b, "already-exists": existing,
        }.get(n)
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeToolsResult(
            name="already-exists",  # collides
            description="x",
            rationale="x",
            combined_source_code="...",
            combined_pip_packages=[],
        )

        result = merge(registry, store, ["tool-a", "tool-b"])
        assert result is None
        registry.deactivate.assert_not_called()


class TestMergeSkills:
    """_merge_skills: SKILL parents → combined SKILL.md."""

    def _skill_cap(self, name: str, tags: list[str] | None = None) -> CapabilityDescriptor:
        return _cap_with_source(
            name,
            cap_type=CapabilityType.SKILL,
            skill_content=(
                f"---\nname: {name}\ndescription: {name} skill\n---\n\n"
                f"Do the {name} thing.\n"
            ),
            tags=tags,
            domain="analytics",
        )

    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_happy_path_registers_and_deactivates_parents(
        self, mock_init, mock_verify
    ):
        cap_a = self._skill_cap("skill-a", tags=["csv"])
        cap_b = self._skill_cap("skill-b", tags=["json"])
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "skill-a": cap_a, "skill-b": cap_b, "merged-skill": None,
        }.get(n)
        store = MagicMock()
        mock_verify.return_value = _passing_report("merged-skill")

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeSkillsResult(
            name="merged-skill",
            description="Merged skill",
            rationale="Overlapping scope",
            combined_skill_content=(
                "---\nname: merged-skill\ndescription: Merged\n---\n\n"
                "Do both things.\n"
            ),
        )

        result = merge(registry, store, ["skill-a", "skill-b"])
        assert result == "merged-skill"
        assert registry.add.call_count == 1
        descriptor = registry.add.call_args[0][0]
        assert descriptor.capability_type == CapabilityType.SKILL
        assert descriptor.metadata.get("skill_content", "").startswith("---")
        assert registry.deactivate.call_count == 2
        assert set(descriptor.tags) == {"csv", "json"}

    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_gate_rollback_keeps_parents_active(self, mock_init, mock_verify):
        cap_a = self._skill_cap("skill-a")
        cap_b = self._skill_cap("skill-b")
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "skill-a": cap_a, "skill-b": cap_b, "merged-skill": None,
        }.get(n)
        store = MagicMock()
        mock_verify.return_value = _failing_report("merged-skill")

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeSkillsResult(
            name="merged-skill",
            description="Merged",
            rationale="x",
            combined_skill_content="---\nname: merged-skill\ndescription: x\n---\n\nBad.\n",
        )

        result = merge(registry, store, ["skill-a", "skill-b"])
        assert result is None
        registry.deactivate.assert_not_called()


class TestMergeAgents:
    """_merge_agents: AGENT parents → combined agent_config, budget ceiling enforced."""

    def _agent_cap(self, name: str, subagents: list[dict] | None = None) -> CapabilityDescriptor:
        cfg: dict = {"system_prompt": f"You are {name}."}
        if subagents is not None:
            cfg["subagents"] = subagents
        return _cap_with_source(
            name,
            cap_type=CapabilityType.AGENT,
            agent_config=cfg,
            domain="research",
        )

    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_happy_path_solo_agents(self, mock_init, mock_verify):
        cap_a = self._agent_cap("agent-a")
        cap_b = self._agent_cap("agent-b")
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "agent-a": cap_a, "agent-b": cap_b, "merged-agent": None,
        }.get(n)
        store = MagicMock()
        mock_verify.return_value = _passing_report("merged-agent")

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeAgentsResult(
            name="merged-agent",
            description="Merged agent",
            rationale="Both investigate",
            combined_system_prompt="You investigate both X and Y.",
            combined_subagents=[],
        )

        result = merge(registry, store, ["agent-a", "agent-b"])
        assert result == "merged-agent"
        assert registry.add.call_count == 1
        descriptor = registry.add.call_args[0][0]
        assert descriptor.capability_type == CapabilityType.AGENT
        # agent_config is stored as JSON string in metadata
        import json as _json
        agent_cfg = _json.loads(descriptor.metadata["agent_config"])
        assert agent_cfg["system_prompt"] == "You investigate both X and Y."
        # No subagents key when the list is empty
        assert "subagents" not in agent_cfg
        assert registry.deactivate.call_count == 2

    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_happy_path_with_subagents_under_budget(self, mock_init, mock_verify):
        cap_a = self._agent_cap("agent-a")
        cap_b = self._agent_cap("agent-b")
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "agent-a": cap_a, "agent-b": cap_b, "merged-agent": None,
        }.get(n)
        store = MagicMock()
        mock_verify.return_value = _passing_report("merged-agent")

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeAgentsResult(
            name="merged-agent",
            description="Merged agent",
            rationale="x",
            combined_system_prompt="Lead prompt",
            combined_subagents=[
                MergeAgentsSubagent(
                    name="sub-1",
                    description="subagent 1",
                    system_prompt="you are sub 1",
                    tools=["sandbox_execute"],
                ),
                MergeAgentsSubagent(
                    name="sub-2",
                    description="subagent 2",
                    system_prompt="you are sub 2",
                    tools=["sandbox_execute"],
                ),
            ],
        )

        result = merge(registry, store, ["agent-a", "agent-b"])
        assert result == "merged-agent"
        import json as _json
        agent_cfg = _json.loads(registry.add.call_args[0][0].metadata["agent_config"])
        assert len(agent_cfg["subagents"]) == 2
        assert agent_cfg["subagents"][0]["name"] == "sub-1"

    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_budget_ceiling_rejects_merge_without_calling_gate(
        self, mock_init, mock_verify
    ):
        """Step 6d: more than 8 subagents → abort, parents stay active,
        gate is NOT called (cheaper to reject than to run Tier 3)."""
        cap_a = self._agent_cap("agent-a")
        cap_b = self._agent_cap("agent-b")
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "agent-a": cap_a, "agent-b": cap_b,
        }.get(n)
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        # 9 subagents — over the _MAX_AGENT_SUBAGENT_BUDGET of 8
        too_many = [
            MergeAgentsSubagent(
                name=f"sub-{i}",
                description=f"subagent {i}",
                system_prompt=f"you are sub {i}",
                tools=["sandbox_execute"],
            )
            for i in range(_MAX_AGENT_SUBAGENT_BUDGET + 1)
        ]
        mock_structured.invoke.return_value = MergeAgentsResult(
            name="merged-agent",
            description="too complex",
            rationale="over budget",
            combined_system_prompt="lead",
            combined_subagents=too_many,
        )

        result = merge(registry, store, ["agent-a", "agent-b"])
        assert result is None
        # Gate not called — budget check happens before verification
        mock_verify.assert_not_called()
        registry.deactivate.assert_not_called()

    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_gate_rollback_keeps_parents_active(self, mock_init, mock_verify):
        cap_a = self._agent_cap("agent-a")
        cap_b = self._agent_cap("agent-b")
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "agent-a": cap_a, "agent-b": cap_b, "merged-agent": None,
        }.get(n)
        store = MagicMock()
        mock_verify.return_value = _failing_report("merged-agent")

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeAgentsResult(
            name="merged-agent",
            description="x",
            rationale="x",
            combined_system_prompt="x",
            combined_subagents=[],
        )

        result = merge(registry, store, ["agent-a", "agent-b"])
        assert result is None
        registry.deactivate.assert_not_called()
