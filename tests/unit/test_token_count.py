"""Unit tests for src.registry.token_count.

Covers static definition-token cost:
  - Per-capability-type dispatch (MCP_TOOL / SKILL / AGENT)
  - Stable ordering via sort_keys (JSON output is deterministic)
  - Empty/missing-field fallback returns 0
  - Tokenizer-exception fallback returns 0 and logs a warning
  - Wiring tests for seed_registry + tool_loader + loading_node + operations
"""

from __future__ import annotations

from unittest.mock import patch

from src.registry.token_count import _tiktoken_model_name, count_definition_tokens
from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact


def test_tiktoken_model_name_strips_known_provider_prefixes():
    assert _tiktoken_model_name("openai:gpt-5-mini") == "gpt-5-mini"
    assert _tiktoken_model_name("azure:gpt-35-turbo") == "gpt-35-turbo"
    assert _tiktoken_model_name("gpt-5-mini") == "gpt-5-mini"


# ---------------------------------------------------------------------------
# MCP_TOOL dispatch
# ---------------------------------------------------------------------------


def test_mcp_tool_serializes_description_and_schemas():
    artifact = GeneratedArtifact(
        capability_name="parse-csv",
        capability_type=CapabilityType.MCP_TOOL,
        description="Parse a CSV file and return row count + column names.",
        input_schema={"file_path": "str"},
        output_schema={"row_count": "int", "columns": "list[str]"},
    )
    n = count_definition_tokens(artifact)
    assert n > 0


def test_mcp_tool_token_count_stable_across_schema_key_ordering():
    """Reordering dict keys must not change the count — sort_keys=True enforces this."""
    artifact_a = GeneratedArtifact(
        capability_name="x",
        capability_type=CapabilityType.MCP_TOOL,
        description="desc",
        input_schema={"a": "int", "b": "str"},
        output_schema={"y": "int", "x": "str"},
    )
    artifact_b = GeneratedArtifact(
        capability_name="x",
        capability_type=CapabilityType.MCP_TOOL,
        description="desc",
        input_schema={"b": "str", "a": "int"},
        output_schema={"x": "str", "y": "int"},
    )
    assert count_definition_tokens(artifact_a) == count_definition_tokens(artifact_b)


def test_mcp_tool_with_empty_fields_returns_nonzero():
    """Even an empty schema has structural JSON tokens."""
    artifact = GeneratedArtifact(
        capability_name="x",
        capability_type=CapabilityType.MCP_TOOL,
        description="",
    )
    n = count_definition_tokens(artifact)
    # The wrapper dict still tokenizes even when all payload fields are empty.
    assert n > 0


# ---------------------------------------------------------------------------
# SKILL dispatch
# ---------------------------------------------------------------------------


def test_skill_tokenizes_skill_content():
    body = """---
name: example-skill
description: A skill that does X
---

# Example Skill

This skill processes input data and returns a summary.
"""
    artifact = GeneratedArtifact(
        capability_name="example-skill",
        capability_type=CapabilityType.SKILL,
        skill_content=body,
    )
    n = count_definition_tokens(artifact)
    assert n > 0


def test_skill_with_missing_content_returns_zero():
    artifact = GeneratedArtifact(
        capability_name="x",
        capability_type=CapabilityType.SKILL,
        skill_content=None,
    )
    assert count_definition_tokens(artifact) == 0


def test_skill_with_empty_content_returns_zero():
    artifact = GeneratedArtifact(
        capability_name="x",
        capability_type=CapabilityType.SKILL,
        skill_content="",
    )
    assert count_definition_tokens(artifact) == 0


# ---------------------------------------------------------------------------
# AGENT dispatch
# ---------------------------------------------------------------------------


def test_agent_tokenizes_system_prompt_only():
    """Subagent prompts are NOT counted — only the parent's system_prompt."""
    artifact = GeneratedArtifact(
        capability_name="investigator",
        capability_type=CapabilityType.AGENT,
        agent_config={
            "system_prompt": "You are an investigator. Analyze data and report findings.",
            "subagents": [
                {"system_prompt": "You are a subagent. " * 100},
            ],
        },
    )
    n = count_definition_tokens(artifact)

    # Without subagents, only the parent prompt contributes — should be small.
    without_subagents = GeneratedArtifact(
        capability_name="investigator",
        capability_type=CapabilityType.AGENT,
        agent_config={
            "system_prompt": "You are an investigator. Analyze data and report findings.",
        },
    )
    n_isolated = count_definition_tokens(without_subagents)
    assert n == n_isolated, "subagent prompts must not inflate the count"


def test_agent_with_missing_agent_config_returns_zero():
    artifact = GeneratedArtifact(
        capability_name="x",
        capability_type=CapabilityType.AGENT,
        agent_config=None,
    )
    assert count_definition_tokens(artifact) == 0


def test_agent_with_missing_system_prompt_returns_zero():
    artifact = GeneratedArtifact(
        capability_name="x",
        capability_type=CapabilityType.AGENT,
        agent_config={"tools": ["sandbox_execute"]},
    )
    assert count_definition_tokens(artifact) == 0


# ---------------------------------------------------------------------------
# Never-raises contract
# ---------------------------------------------------------------------------


def test_tokenizer_exception_returns_zero(caplog):
    """If the encoder blows up, return 0 and log a warning — never raise."""
    artifact = GeneratedArtifact(
        capability_name="x",
        capability_type=CapabilityType.MCP_TOOL,
        description="ok",
    )

    class _BrokenEncoding:
        def encode(self, text):  # noqa: ARG002
            raise RuntimeError("tokenizer boom")

    with patch(
        "src.registry.token_count._get_encoding", return_value=_BrokenEncoding()
    ):
        caplog.set_level("WARNING", logger="src.registry.token_count")
        n = count_definition_tokens(artifact)
    assert n == 0
    assert any("tokenizer failed" in r.message for r in caplog.records)


def test_serialization_failure_returns_zero(caplog):
    """Non-JSON-serializable agent_config must not raise — fall back to 0."""
    artifact = GeneratedArtifact(
        capability_name="x",
        capability_type=CapabilityType.MCP_TOOL,
        description="ok",
    )

    with patch(
        "src.registry.token_count.json.dumps", side_effect=TypeError("can't serialize")
    ):
        caplog.set_level("WARNING", logger="src.registry.token_count")
        n = count_definition_tokens(artifact)
    assert n == 0
    assert any("could not serialize" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Wiring: seed_registry populates the field
# ---------------------------------------------------------------------------


def test_seed_registry_populates_definition_token_cost():
    from src.registry.store import SkillRegistry, seed_registry

    registry = SkillRegistry()
    seed_registry(registry)

    caps = registry.list_all()
    assert len(caps) > 0, "seed_registry should populate the store"

    # Every seed has content — every cap should have a non-zero token cost.
    for cap in caps:
        assert cap.definition_token_cost > 0, (
            f"Seeded capability {cap.name} ({cap.capability_type}) "
            f"has definition_token_cost=0"
        )


def test_definition_token_cost_survives_chromadb_round_trip():
    """Persistence check: value written by add() must come back through get()."""
    from src.registry.store import SkillRegistry, seed_registry

    registry = SkillRegistry()
    seed_registry(registry)

    before = registry.list_all()[0]
    fetched = registry.get(before.name)
    assert fetched is not None
    assert fetched.definition_token_cost == before.definition_token_cost
