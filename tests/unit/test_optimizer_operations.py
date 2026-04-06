"""Tests for src/optimizer/operations.py."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.optimizer.operations import (
    MergeResult,
    SplitResult,
    SplitVariant,
    merge,
    prune,
    split,
)
from src.schemas.capability import CapabilityDescriptor, CapabilityType


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

        result = split(registry, store, "data-workflow")

        # All-or-nothing: no variants registered, parent not deactivated
        assert result == []
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

        result = split(registry, store, "data-workflow")

        assert result == []
        registry.add.assert_not_called()
        registry.deactivate.assert_not_called()


class TestMerge:
    def test_rejects_non_skill(self):
        registry = MagicMock()
        registry.get.side_effect = [
            _make_cap("tool-a", cap_type=CapabilityType.MCP_TOOL),
            _make_cap("skill-b", cap_type=CapabilityType.SKILL),
        ]
        store = MagicMock()

        with pytest.raises(ValueError, match="Merge is only supported for SKILL"):
            merge(registry, store, ["tool-a", "skill-b"])

    def test_rejects_agent_type(self):
        registry = MagicMock()
        registry.get.side_effect = [
            _make_cap("agent-a", cap_type=CapabilityType.AGENT),
            _make_cap("skill-b", cap_type=CapabilityType.SKILL),
        ]
        store = MagicMock()

        with pytest.raises(ValueError, match="Merge is only supported for SKILL"):
            merge(registry, store, ["agent-a", "skill-b"])

    def test_raises_key_error_for_missing(self):
        registry = MagicMock()
        registry.get.side_effect = [None]
        store = MagicMock()

        with pytest.raises(KeyError, match="not found"):
            merge(registry, store, ["nonexistent"])

    @patch("src.optimizer.operations.init_chat_model")
    def test_merge_skills(self, mock_init):
        cap_a = _make_cap("clean-csv", cap_type=CapabilityType.SKILL, domain="data", tags=["csv"])
        cap_b = _make_cap("clean-json", cap_type=CapabilityType.SKILL, domain="data", tags=["json"])

        registry = MagicMock()
        # [cap_a, cap_b] for parent lookups, None for merged-name collision check.
        registry.get.side_effect = [cap_a, cap_b, None]
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeResult(
            name="data-cleaning",
            description="Clean CSV and JSON data",
            rationale="Both handle data cleaning",
        )

        result = merge(registry, store, ["clean-csv", "clean-json"])

        assert result == "data-cleaning"
        # Merged capability registered
        assert registry.add.call_count == 1
        new_cap = registry.add.call_args[0][0]
        assert new_cap.capability_type == CapabilityType.SKILL
        assert new_cap.domain == "data"
        assert set(new_cap.tags) == {"csv", "json"}
        # Both parents deactivated
        assert registry.deactivate.call_count == 2

    @patch("src.optimizer.operations.init_chat_model")
    def test_merge_records_events(self, mock_init):
        cap_a = _make_cap("skill-a", cap_type=CapabilityType.SKILL)
        cap_b = _make_cap("skill-b", cap_type=CapabilityType.SKILL)

        registry = MagicMock()
        registry.get.side_effect = [cap_a, cap_b, None]
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeResult(
            name="combined-skill",
            description="Combined capability",
            rationale="Low reuse individually",
        )

        merge(registry, store, ["skill-a", "skill-b"])

        # 1 created event + 2 merged events = 3
        assert store.record_event.call_count == 3

    @patch("src.optimizer.operations.init_chat_model")
    def test_merge_repair_pass_on_first_failure(self, mock_init):
        """merge() retries once when LLM output fails validation."""
        cap_a = _make_cap("skill-a", cap_type=CapabilityType.SKILL)
        cap_b = _make_cap("skill-b", cap_type=CapabilityType.SKILL)
        registry = MagicMock()
        registry.get.side_effect = [cap_a, cap_b, None]
        store = MagicMock()

        good_result = MergeResult(
            name="merged-skill",
            description="Merged capability",
            rationale="Low reuse individually",
        )

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.side_effect = [RuntimeError("bad JSON"), good_result]

        result = merge(registry, store, ["skill-a", "skill-b"])
        assert result == "merged-skill"
        assert mock_structured.invoke.call_count == 2

    @patch("src.optimizer.operations.init_chat_model")
    def test_merge_raises_if_repair_also_fails(self, mock_init):
        """merge() raises if both initial call and repair pass fail."""
        cap_a = _make_cap("skill-a", cap_type=CapabilityType.SKILL)
        cap_b = _make_cap("skill-b", cap_type=CapabilityType.SKILL)
        registry = MagicMock()
        registry.get.side_effect = [cap_a, cap_b]
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.side_effect = RuntimeError("always fails")

        with pytest.raises(RuntimeError, match="always fails"):
            merge(registry, store, ["skill-a", "skill-b"])
        assert mock_structured.invoke.call_count == 2

    @patch("src.optimizer.operations.init_chat_model")
    def test_merge_raises_if_merged_name_already_exists(self, mock_init):
        """merge(): raises ValueError and leaves parents intact if merged name collides."""
        cap_a = _make_cap("skill-a", cap_type=CapabilityType.SKILL)
        cap_b = _make_cap("skill-b", cap_type=CapabilityType.SKILL)
        existing_merged = _make_cap("data-cleaning", cap_type=CapabilityType.SKILL)

        def _get(name):
            return {"skill-a": cap_a, "skill-b": cap_b, "data-cleaning": existing_merged}.get(name)

        registry = MagicMock()
        registry.get.side_effect = _get
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeResult(
            name="data-cleaning",  # collides with existing_merged
            description="Clean CSV and JSON data",
            rationale="Overlapping scope",
        )

        with pytest.raises(ValueError, match="already exists in registry"):
            merge(registry, store, ["skill-a", "skill-b"])

        # Parents must NOT be deactivated
        registry.deactivate.assert_not_called()
        registry.add.assert_not_called()

    @patch("src.optimizer.operations.init_chat_model")
    def test_merge_uses_domain_from_first_parent(self, mock_init):
        """merge(): merged capability inherits domain from the first parent."""
        cap_a = _make_cap("skill-a", cap_type=CapabilityType.SKILL, domain="analytics")
        cap_b = _make_cap("skill-b", cap_type=CapabilityType.SKILL, domain="reporting")

        registry = MagicMock()
        registry.get.side_effect = [cap_a, cap_b, None]
        store = MagicMock()

        mock_llm = MagicMock()
        mock_init.return_value = mock_llm
        mock_structured = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured
        mock_structured.invoke.return_value = MergeResult(
            name="analytics-reporting",
            description="Combined analytics and reporting capability",
            rationale="Both produce output summaries",
        )

        merge(registry, store, ["skill-a", "skill-b"])

        new_cap = registry.add.call_args[0][0]
        # Domain from the first parent, not the second
        assert new_cap.domain == "analytics"
