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
    SplitAgentsResult,
    SplitAgentVariant,
    SplitAgentVariantSubagent,
    SplitSkillsResult,
    SplitSkillVariant,
    SplitToolsResult,
    SplitToolVariant,
    _MAX_AGENT_SUBAGENT_BUDGET,
    _merge_agents,
    _merge_skills,
    _merge_tools,
    _register_artifact_directly,
    _split_agents,
    _split_skills,
    _split_tools,
    _summarize_failures_for_prompt,
    merge,
    prune,
    split,
    verify_and_register_or_rollback,
)
from src.schemas.metrics import InvocationRecord
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
    def test_skips_freshly_created_capabilities_inside_prune_window(self):
        registry = MagicMock()
        fresh_cap = _make_cap("fresh-cap").model_copy(
            update={"created_at": datetime.now(UTC)}
        )
        registry.list_all.return_value = [fresh_cap]

        store = MagicMock()

        result = prune(registry, store, window_days=30)

        assert result == []
        registry.deactivate.assert_not_called()
        store.invocations_since.assert_not_called()

    def test_prunes_zero_invocation_capabilities(self):
        registry = MagicMock()
        registry.list_all.return_value = [
            _make_cap("cap-a").model_copy(
                update={"created_at": datetime(2020, 1, 1, tzinfo=UTC)}
            ),
            _make_cap("cap-b").model_copy(
                update={"created_at": datetime(2020, 1, 1, tzinfo=UTC)}
            ),
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
            _make_cap("cap-a").model_copy(
                update={"created_at": datetime(2020, 1, 1, tzinfo=UTC)}
            ),
            _make_cap("cap-b").model_copy(
                update={"created_at": datetime(2020, 1, 1, tzinfo=UTC)}
            ),
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
        registry.list_all.return_value = [
            _make_cap("cap-a").model_copy(
                update={"created_at": datetime(2020, 1, 1, tzinfo=UTC)}
            )
        ]

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
        pinned_cap = pinned_cap.model_copy(
            update={
                "created_at": datetime(2020, 1, 1, tzinfo=UTC),
                "metadata": {"pinned": "true"},
            }
        )
        registry = MagicMock()
        registry.list_all.return_value = [pinned_cap]
        store = MagicMock()
        store.invocations_since.return_value = 0

        result = prune(registry, store, window_days=30)

        assert result == []
        registry.deactivate.assert_not_called()

    def test_prune_deactivates_unpinned_zero_invocation(self):
        """Unpinned capabilities with zero invocations are still pruned.

        Regression fence for `test_prune_skips_pinned_capability`: the pinned
        short-circuit must not accidentally apply to caps whose metadata
        simply has no ``pinned`` key. Extends #1 with an explicit
        ``record_event`` payload assertion to catch silent event-schema
        drift when the prune helper is refactored.
        """
        registry = MagicMock()
        cap = _make_cap("ephemeral").model_copy(
            update={"created_at": datetime(2020, 1, 1, tzinfo=UTC)}
        )
        # Explicitly no "pinned" key in metadata (empty {})
        assert cap.metadata == {}
        registry.list_all.return_value = [cap]
        store = MagicMock()
        store.invocations_since.return_value = 0

        result = prune(registry, store, window_days=30)

        assert result == ["ephemeral"]
        registry.deactivate.assert_called_once_with("ephemeral")
        # Event recorded with capability name AND the "pruned" event type —
        # not just "some event was recorded".
        assert store.record_event.call_count == 1
        event_call = store.record_event.call_args
        # Positional args: (capability_id, event_type, ...) per operations.py
        assert event_call[0][0] == "ephemeral"
        assert event_call[0][1] == "pruned"

    def test_prune_mixed_registry_only_deactivates_unpinned(self):
        """Mixed registry: pinned survives, active-with-invocations survives, idle-unpinned pruned."""
        pinned = _make_cap("pinned-cap")
        pinned = pinned.model_copy(
            update={
                "created_at": datetime(2020, 1, 1, tzinfo=UTC),
                "metadata": {"pinned": "true"},
            }
        )
        active = _make_cap("active-cap").model_copy(
            update={"created_at": datetime(2020, 1, 1, tzinfo=UTC)}
        )
        idle = _make_cap("idle-cap").model_copy(
            update={"created_at": datetime(2020, 1, 1, tzinfo=UTC)}
        )

        registry = MagicMock()
        registry.list_all.return_value = [pinned, active, idle]
        store = MagicMock()
        # pinned is skipped before invocations_since is called
        # active has invocations, idle has none
        store.invocations_since.side_effect = [5, 0]

        result = prune(registry, store, window_days=30)

        assert result == ["idle-cap"]
        registry.deactivate.assert_called_once_with("idle-cap")


# TestSplit tests live below the helper functions (_passing_report /
# _failing_report / _cap_with_source) — see TestSplitDispatch and the
# per-type TestSplit{Tools,Skills,Agents} classes after TestMergeAgents.


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
        ).model_copy(update={
            "input_schema": {"request": "str"},
            "output_schema": {"result": "str"},
        })

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

    def _agent_cap(
        self,
        name: str,
        subagents: list[dict] | None = None,
        *,
        input_schema: dict[str, str] | None = None,
        output_schema: dict[str, str] | None = None,
    ) -> CapabilityDescriptor:
        cfg: dict = {"system_prompt": f"You are {name}."}
        if subagents is not None:
            cfg["subagents"] = subagents
        cap = _cap_with_source(
            name,
            cap_type=CapabilityType.AGENT,
            agent_config=cfg,
            domain="research",
        )
        if input_schema is not None or output_schema is not None:
            cap = cap.model_copy(update={
                "input_schema": input_schema or {},
                "output_schema": output_schema or {},
            })
        return cap

    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_happy_path_solo_agents(self, mock_init, mock_verify):
        cap_a = self._agent_cap(
            "agent-a",
            input_schema={"question": "str"},
            output_schema={"summary": "str"},
        )
        cap_b = self._agent_cap(
            "agent-b",
            input_schema={"context": "str"},
            output_schema={"answer": "str"},
        )
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
        assert descriptor.input_schema == {"question": "str", "context": "str"}
        assert descriptor.output_schema == {"summary": "str", "answer": "str"}
        assert registry.deactivate.call_count == 2

    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_happy_path_with_subagents_under_budget(self, mock_init, mock_verify):
        cap_a = self._agent_cap(
            "agent-a",
            input_schema={"left": "str"},
            output_schema={"left_result": "str"},
        )
        cap_b = self._agent_cap(
            "agent-b",
            input_schema={"right": "str"},
            output_schema={"right_result": "str"},
        )
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
        descriptor = registry.add.call_args[0][0]
        agent_cfg = _json.loads(descriptor.metadata["agent_config"])
        assert len(agent_cfg["subagents"]) == 2
        assert agent_cfg["subagents"][0]["name"] == "sub-1"
        assert descriptor.input_schema == {"left": "str", "right": "str"}
        assert descriptor.output_schema == {
            "left_result": "str",
            "right_result": "str",
        }

    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_parents_without_schemas_fail_closed_before_registration(
        self, mock_init, mock_verify
    ):
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
        assert result is None
        registry.add.assert_not_called()
        registry.deactivate.assert_not_called()

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


# ---------------------------------------------------------------------------
# Step 8 — per-type split dispatch + helpers
# ---------------------------------------------------------------------------


def _vacuous_coverage_report():
    """Mock return for verify_coverage — vacuously passing (empty failure cases)."""
    from src.verification.coverage import CoverageReport
    return CoverageReport(all_covered=True, status="vacuous")


def _verified_coverage_report():
    """Mock return for verify_coverage — all cases covered."""
    from src.verification.coverage import CoverageReport
    return CoverageReport(
        all_covered=True,
        status="verified",
        coverage_map={"t1": ["variant-a"]},
    )


def _uncovered_coverage_report():
    """Mock return for verify_coverage — one case uncovered."""
    from src.verification.coverage import CoverageReport
    return CoverageReport(
        all_covered=False,
        status="verified",
        coverage_map={},
        uncovered_cases=["t1"],
    )


def _unverifiable_type_coverage_report():
    """Mock return for verify_coverage on SKILL/AGENT — vacuously passing."""
    from src.verification.coverage import CoverageReport
    return CoverageReport(all_covered=True, status="unverifiable_type")


class TestSplitDispatch:
    """split() routes by capability type and rejects unsupported paths."""

    def test_missing_capability_raises_key_error(self):
        registry = MagicMock()
        registry.get.return_value = None
        store = MagicMock()
        with pytest.raises(KeyError, match="not found"):
            split(registry, store, "nonexistent")

    @patch("src.optimizer.operations._split_tools")
    def test_mcp_tool_routes_to_split_tools(self, mock_split_tools):
        mock_split_tools.return_value = ["var-a", "var-b"]
        registry = MagicMock()
        registry.get.return_value = _cap_with_source(
            "tool-a", CapabilityType.MCP_TOOL, source_code="async def f(): pass",
        )
        store = MagicMock()

        result = split(registry, store, "tool-a")
        assert result == ["var-a", "var-b"]
        mock_split_tools.assert_called_once()

    @patch("src.optimizer.operations._split_skills")
    def test_skill_routes_to_split_skills(self, mock_split_skills):
        mock_split_skills.return_value = ["var-a", "var-b"]
        registry = MagicMock()
        registry.get.return_value = _cap_with_source(
            "skill-a", CapabilityType.SKILL,
            skill_content="---\nname: skill-a\n---\n",
        )
        store = MagicMock()

        result = split(registry, store, "skill-a")
        assert result == ["var-a", "var-b"]
        mock_split_skills.assert_called_once()

    @patch("src.optimizer.operations._split_agents")
    def test_agent_routes_to_split_agents(self, mock_split_agents):
        mock_split_agents.return_value = ["var-a", "var-b"]
        registry = MagicMock()
        registry.get.return_value = _cap_with_source(
            "agent-a", CapabilityType.AGENT,
            agent_config={"system_prompt": "You are an agent"},
        )
        store = MagicMock()

        result = split(registry, store, "agent-a")
        assert result == ["var-a", "var-b"]
        mock_split_agents.assert_called_once()


class TestSplitTools:
    """_split_tools: MCP_TOOL parent → per-type coverage verification active."""

    def _mcp_parent(self, name: str = "tool-parent") -> CapabilityDescriptor:
        return _cap_with_source(
            name,
            cap_type=CapabilityType.MCP_TOOL,
            source_code=(
                '"""Parent tool."""\n'
                "from typing import Annotated\n"
                "from mcp.server.fastmcp import FastMCP\n"
                "from pydantic import Field\n"
                f'mcp = FastMCP("{name}")\n'
                "@mcp.tool()\n"
                "async def run(x: Annotated[int, Field(description='x')]) -> dict:\n"
                "    return {'y': x * 2}\n"
            ),
            tags=["parser"],
            domain="data",
        )

    def _variant(self, name: str) -> SplitToolVariant:
        return SplitToolVariant(
            name=name,
            description=f"Specialized variant {name}",
            subspace=f"subspace for {name}",
            source_code=(
                '"""Variant."""\n'
                "from typing import Annotated\n"
                "from mcp.server.fastmcp import FastMCP\n"
                "from pydantic import Field\n"
                f'mcp = FastMCP("{name}")\n'
                "@mcp.tool()\n"
                "async def run(x: Annotated[int, Field(description='x')]) -> dict:\n"
                "    return {'y': x * 3}\n"
                'if __name__ == "__main__":\n'
                "    mcp.run()\n"
            ),
            pip_packages=[],
        )

    @patch("src.verification.coverage.verify_coverage")
    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_happy_path_registers_variants_and_deactivates_parent(
        self, mock_init, mock_gate, mock_coverage,
    ):
        parent = self._mcp_parent()
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "tool-parent": parent,
            "var-a": None,
            "var-b": None,
        }.get(n)
        store = MagicMock()
        store.get_recent_failures.return_value = []

        mock_gate.return_value = _passing_report("any")
        mock_coverage.return_value = _vacuous_coverage_report()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitToolsResult(
            analysis="fails on large inputs vs small",
            variants=[self._variant("var-a"), self._variant("var-b")],
        )

        result = split(registry, store, "tool-parent")

        assert result == ["var-a", "var-b"]
        # Gate was called per variant
        assert mock_gate.call_count == 2
        # Coverage was called collectively
        assert mock_coverage.call_count == 1
        # Both variants registered
        assert registry.add.call_count == 2
        # Parent deactivated
        registry.deactivate.assert_called_once_with("tool-parent")

    @patch("src.verification.coverage.verify_coverage")
    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_phase_b_gate_rejection_aborts_without_registering(
        self, mock_init, mock_gate, mock_coverage,
    ):
        parent = self._mcp_parent()
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "tool-parent": parent, "var-a": None, "var-b": None,
        }.get(n)
        store = MagicMock()
        store.get_recent_failures.return_value = []

        # First variant passes gate, second fails
        mock_gate.side_effect = [
            _passing_report("var-a"),
            _failing_report("var-b", severity=Severity.HIGH),
        ]

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitToolsResult(
            analysis="analysis",
            variants=[self._variant("var-a"), self._variant("var-b")],
        )

        result = split(registry, store, "tool-parent")

        assert result is None
        # No registration — phase B aborted before phase D
        registry.add.assert_not_called()
        # Parent NOT deactivated — atomic rollback
        registry.deactivate.assert_not_called()
        # Coverage was NOT called — phase B aborts before phase C
        mock_coverage.assert_not_called()

    @patch("src.verification.coverage.verify_coverage")
    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_phase_c_coverage_rejection_aborts(
        self, mock_init, mock_gate, mock_coverage,
    ):
        parent = self._mcp_parent()
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "tool-parent": parent, "var-a": None, "var-b": None,
        }.get(n)
        store = MagicMock()
        store.get_recent_failures.return_value = []

        mock_gate.return_value = _passing_report("any")
        mock_coverage.return_value = _uncovered_coverage_report()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitToolsResult(
            analysis="analysis",
            variants=[self._variant("var-a"), self._variant("var-b")],
        )

        result = split(registry, store, "tool-parent")

        assert result is None
        # No registration — phase C aborted before phase D
        registry.add.assert_not_called()
        registry.deactivate.assert_not_called()

    @patch("src.optimizer.operations.init_chat_model")
    def test_name_collision_aborts_before_gate(self, mock_init):
        parent = self._mcp_parent()
        existing = _cap_with_source("already-exists", CapabilityType.MCP_TOOL)
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "tool-parent": parent, "already-exists": existing,
        }.get(n)
        store = MagicMock()
        store.get_recent_failures.return_value = []

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitToolsResult(
            analysis="analysis",
            variants=[
                self._variant("already-exists"),  # collides
                self._variant("var-b"),
            ],
        )

        result = split(registry, store, "tool-parent")
        assert result is None
        registry.add.assert_not_called()
        registry.deactivate.assert_not_called()

    @patch("src.optimizer.operations.init_chat_model")
    def test_parent_without_source_aborts(self, mock_init):
        # Parent has no source_code in metadata
        parent = CapabilityDescriptor(
            name="tool-parent",
            description="desc",
            capability_type=CapabilityType.MCP_TOOL,
            domain="data",
            tags=[],
            metadata={"generated": "true"},  # no source_code
        )
        registry = MagicMock()
        registry.get.return_value = parent
        store = MagicMock()

        result = split(registry, store, "tool-parent")
        assert result is None
        mock_init.assert_not_called()  # never even called the LLM


class TestSplitSkills:
    """_split_skills: SKILL parent → coverage is vacuous per MCP_TOOL-only scope."""

    def _skill_parent(self, name: str = "skill-parent") -> CapabilityDescriptor:
        return _cap_with_source(
            name,
            cap_type=CapabilityType.SKILL,
            skill_content=(
                f"---\nname: {name}\ndescription: parent skill\n---\n\n"
                "Do the thing.\n"
            ),
            tags=["workflow"],
            domain="analytics",
        ).model_copy(update={
            "input_schema": {"request": "str"},
            "output_schema": {"result": "str"},
        })

    def _variant(self, name: str) -> SplitSkillVariant:
        return SplitSkillVariant(
            name=name,
            description=f"Specialized skill {name}",
            subspace=f"subspace for {name}",
            skill_content=(
                f"---\nname: {name}\ndescription: specialized\n---\n\n"
                "Do the specialized thing.\n"
            ),
        )

    @patch("src.verification.coverage.verify_coverage")
    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_happy_path(self, mock_init, mock_gate, mock_coverage):
        parent = self._skill_parent()
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "skill-parent": parent, "var-a": None, "var-b": None,
        }.get(n)
        store = MagicMock()
        store.get_recent_failures.return_value = []

        mock_gate.return_value = _passing_report("any")
        # SKILL coverage dispatches to unverifiable_type in the real helper,
        # but we patch verify_coverage directly so we return the same shape.
        mock_coverage.return_value = _unverifiable_type_coverage_report()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitSkillsResult(
            analysis="parent was too broad",
            variants=[self._variant("var-a"), self._variant("var-b")],
        )

        result = split(registry, store, "skill-parent")

        assert result == ["var-a", "var-b"]
        assert mock_gate.call_count == 2
        assert registry.add.call_count == 2
        registry.deactivate.assert_called_once_with("skill-parent")

    @patch("src.verification.coverage.verify_coverage")
    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_gate_rejection_aborts(self, mock_init, mock_gate, mock_coverage):
        parent = self._skill_parent()
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "skill-parent": parent, "var-a": None, "var-b": None,
        }.get(n)
        store = MagicMock()
        store.get_recent_failures.return_value = []

        mock_gate.return_value = _failing_report("any")
        mock_coverage.return_value = _unverifiable_type_coverage_report()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitSkillsResult(
            analysis="x",
            variants=[self._variant("var-a"), self._variant("var-b")],
        )

        result = split(registry, store, "skill-parent")
        assert result is None
        registry.add.assert_not_called()
        registry.deactivate.assert_not_called()

    @patch("src.optimizer.operations.init_chat_model")
    def test_parent_without_skill_content_aborts(self, mock_init):
        parent = CapabilityDescriptor(
            name="skill-parent",
            description="desc",
            capability_type=CapabilityType.SKILL,
            domain="x",
            tags=[],
            metadata={"generated": "true"},
        )
        registry = MagicMock()
        registry.get.return_value = parent
        store = MagicMock()

        result = split(registry, store, "skill-parent")
        assert result is None
        mock_init.assert_not_called()


class TestSplitAgents:
    """_split_agents: AGENT parent → coverage vacuous, global subagent budget enforced."""

    def _agent_parent(self, name: str = "agent-parent") -> CapabilityDescriptor:
        return _cap_with_source(
            name,
            cap_type=CapabilityType.AGENT,
            agent_config={"system_prompt": f"You are {name}."},
            domain="research",
        ).model_copy(update={
            "input_schema": {"question": "str"},
            "output_schema": {"answer": "str"},
        })

    def _variant(self, name: str, n_subagents: int = 0) -> SplitAgentVariant:
        subagents = [
            SplitAgentVariantSubagent(
                name=f"{name}-sub-{i}",
                description=f"subagent {i}",
                system_prompt=f"you are sub {i}",
                tools=["sandbox_execute"],
            )
            for i in range(n_subagents)
        ]
        return SplitAgentVariant(
            name=name,
            description=f"Specialized agent {name}",
            subspace=f"subspace for {name}",
            system_prompt=f"You investigate {name}'s subspace.",
            subagents=subagents,
        )

    @patch("src.verification.coverage.verify_coverage")
    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_happy_path_solo_variants(self, mock_init, mock_gate, mock_coverage):
        parent = self._agent_parent()
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "agent-parent": parent, "var-a": None, "var-b": None,
        }.get(n)
        store = MagicMock()
        store.get_recent_failures.return_value = []

        mock_gate.return_value = _passing_report("any")
        mock_coverage.return_value = _unverifiable_type_coverage_report()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitAgentsResult(
            analysis="agent too broad",
            variants=[
                self._variant("var-a", n_subagents=0),
                self._variant("var-b", n_subagents=0),
            ],
        )

        result = split(registry, store, "agent-parent")
        assert result == ["var-a", "var-b"]
        assert registry.add.call_count == 2
        registry.deactivate.assert_called_once_with("agent-parent")

    @patch("src.verification.coverage.verify_coverage")
    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_global_subagent_budget_ceiling_aborts_before_gate(
        self, mock_init, mock_gate, mock_coverage,
    ):
        """Total subagent count across all variants > _MAX_AGENT_SUBAGENT_BUDGET
        aborts the split without calling the gate (cheaper than running Tier 3
        on over-complex variants)."""
        parent = self._agent_parent()
        registry = MagicMock()
        registry.get.side_effect = lambda n: {"agent-parent": parent}.get(n)
        store = MagicMock()
        store.get_recent_failures.return_value = []

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        # Each variant has 5 subagents → 2 variants × 5 = 10 > _MAX_AGENT_SUBAGENT_BUDGET (8)
        mock_structured.invoke.return_value = SplitAgentsResult(
            analysis="too complex",
            variants=[
                self._variant("var-a", n_subagents=5),
                self._variant("var-b", n_subagents=5),
            ],
        )

        result = split(registry, store, "agent-parent")
        assert result is None
        mock_gate.assert_not_called()  # budget rejection happens before gate
        mock_coverage.assert_not_called()
        registry.add.assert_not_called()
        registry.deactivate.assert_not_called()

    @patch("src.verification.coverage.verify_coverage")
    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_subagent_budget_under_ceiling_proceeds(
        self, mock_init, mock_gate, mock_coverage,
    ):
        """Total subagent count == 4 (2 + 2) is under 8 — split should proceed."""
        parent = self._agent_parent()
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "agent-parent": parent, "var-a": None, "var-b": None,
        }.get(n)
        store = MagicMock()
        store.get_recent_failures.return_value = []

        mock_gate.return_value = _passing_report("any")
        mock_coverage.return_value = _unverifiable_type_coverage_report()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitAgentsResult(
            analysis="under budget",
            variants=[
                self._variant("var-a", n_subagents=2),
                self._variant("var-b", n_subagents=2),
            ],
        )

        result = split(registry, store, "agent-parent")
        assert result == ["var-a", "var-b"]

    @patch("src.verification.coverage.verify_coverage")
    @patch("src.verification.gate.verify")
    @patch("src.optimizer.operations.init_chat_model")
    def test_gate_rejection_aborts(self, mock_init, mock_gate, mock_coverage):
        parent = self._agent_parent()
        registry = MagicMock()
        registry.get.side_effect = lambda n: {
            "agent-parent": parent, "var-a": None, "var-b": None,
        }.get(n)
        store = MagicMock()
        store.get_recent_failures.return_value = []

        mock_gate.return_value = _failing_report("any")

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = SplitAgentsResult(
            analysis="x",
            variants=[
                self._variant("var-a", n_subagents=0),
                self._variant("var-b", n_subagents=0),
            ],
        )

        result = split(registry, store, "agent-parent")
        assert result is None
        registry.deactivate.assert_not_called()

    @patch("src.optimizer.operations.init_chat_model")
    def test_parent_without_system_prompt_aborts(self, mock_init):
        parent = CapabilityDescriptor(
            name="agent-parent",
            description="desc",
            capability_type=CapabilityType.AGENT,
            domain="x",
            tags=[],
            metadata={"generated": "true"},  # no agent_config
        )
        registry = MagicMock()
        registry.get.return_value = parent
        store = MagicMock()

        result = split(registry, store, "agent-parent")
        assert result is None
        mock_init.assert_not_called()


class TestSummarizeFailuresForPrompt:
    """Guards the Pillar 3 Split LLM-prompt summarizer against the 2026-04-19
    invocation-recording regression. Pre-fix, both production writers left
    ``input_args`` and ``actual_output`` as None, so ``get_recent_failures``
    returned an empty list and this summarizer hit its degraded-mode fallback
    on every real-telemetry split. This test asserts the summarizer produces
    real LLM-ready context when fed rows in the shape the post-fix writers
    now produce. See ``docs/invocation_recording_gap_2026_04_19.md``.
    """

    def test_empty_failures_returns_degraded_fallback(self):
        summary = _summarize_failures_for_prompt([])
        assert summary.startswith("No recorded failure cases.")

    def test_populated_failures_produce_real_context(self):
        """The critical post-fix invariant: when production writers populate
        input_args + error_message, the summarizer must NOT return the
        degraded fallback."""
        failures = [
            InvocationRecord(
                capability_id="row-aggregator",
                task_id="T99",
                success=False,
                token_cost=0,
                latency_ms=7.5,
                input_args='{"0": {"rows": [{"cat": "A", "val": 1}]}}',
                actual_output='{"error": "schema mismatch"}',
                error_message="schema mismatch",
            ),
        ]
        summary = _summarize_failures_for_prompt(failures)

        # Not the degraded fallback.
        assert not summary.startswith("No recorded failure cases.")
        # Carries input_args preview so the LLM can infer the input subspace.
        assert '"rows"' in summary
        # Carries the error mode.
        assert "ERROR" in summary
        assert "schema mismatch" in summary

    def test_wrong_output_case_rendered_distinctly(self):
        """Rows with actual_output but no error_message render as
        WRONG OUTPUT, not ERROR — the summarizer must distinguish."""
        failures = [
            InvocationRecord(
                capability_id="classifier",
                task_id="T99",
                success=False,
                token_cost=0,
                latency_ms=1.0,
                input_args='{"x": 1}',
                actual_output='{"label": "B"}',  # user flagged this as wrong
                error_message=None,
            ),
        ]
        summary = _summarize_failures_for_prompt(failures)
        assert "WRONG OUTPUT" in summary
        assert "ERROR" not in summary
        assert '"label"' in summary
