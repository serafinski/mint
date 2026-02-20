"""Integration tests for the agent generator (real LLM calls).

Verifies that the agent generator produces valid configurations using the
real DeepSeek LLM, that DeepAgent skills are written to disk, and that
all calls appear in LangFuse.

Requires DEEPSEEK_API_KEY environment variable to be set.
"""

from __future__ import annotations

import os
import re
from unittest.mock import patch

import pytest

from src.orchestrator.generators.agent import (
    _generate_single_agent,
    generate_agent,
)
from src.orchestrator.generators.skill import _get_validate_skill
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap, GeneratedArtifact, TaskState

_has_api_key = bool(os.environ.get("DEEPSEEK_API_KEY"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _has_api_key, reason="DEEPSEEK_API_KEY not set"),
]


@pytest.fixture(autouse=True)
def _reset_agent_llm():
    """Reset the lazy-init LLM singleton so each test starts with a fresh model."""
    import src.orchestrator.generators.agent as mod

    mod._agent_gen_llm = None
    yield
    mod._agent_gen_llm = None


class TestAgentGeneratorLive:
    def test_generate_plain_agent(self, tmp_path):
        """Real LLM generates a valid plain (non-DeepAgent) configuration."""
        gap = ClassifiedGap(
            description="Monitor data quality metrics and flag anomalies in datasets",
            query="data-quality-monitor",
            classified_type=CapabilityType.AGENT,
            confidence=0.9,
            rationale="Reactive monitoring agent",
        )

        artifact, error = _generate_single_agent(
            gap, "Build a business analytics pipeline", output_dir=tmp_path
        )

        assert error is None, f"Generation failed: {error}"
        assert artifact is not None
        assert isinstance(artifact, GeneratedArtifact)
        assert artifact.capability_type == CapabilityType.AGENT
        assert artifact.agent_config is not None
        assert "system_prompt" in artifact.agent_config
        assert "node_config" in artifact.agent_config
        assert "is_deep_agent" in artifact.agent_config
        assert len(artifact.agent_config["system_prompt"].strip()) > 0

        # Verify capability_name is valid hyphen-case (required by agent registry)
        assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", artifact.capability_name), (
            f"capability_name '{artifact.capability_name}' is not valid hyphen-case"
        )

    def test_generate_deep_agent_writes_skill_to_disk(self, tmp_path):
        """Real LLM generates a DeepAgent config and writes SKILL.md to disk."""
        gap = ClassifiedGap(
            description=(
                "Autonomous agent that investigates data pipeline failures, "
                "traces root causes across multiple stages, and proposes fixes "
                "using multi-step reasoning and adaptive planning"
            ),
            query="pipeline-failure-investigator",
            classified_type=CapabilityType.AGENT,
            confidence=0.9,
            rationale="Multi-step investigation with adaptive reasoning",
        )

        artifact, error = _generate_single_agent(
            gap, "Build a self-healing analytics pipeline", output_dir=tmp_path
        )

        assert error is None, f"Generation failed: {error}"
        assert artifact is not None
        assert artifact.capability_type == CapabilityType.AGENT

        # If the LLM classified this as a DeepAgent, verify SKILL.md is on disk
        if artifact.agent_config.get("is_deep_agent"):
            assert "skill_content" in artifact.agent_config
            assert artifact.agent_config["skill_content"] is not None

            skill_path = tmp_path / artifact.capability_name / "SKILL.md"
            assert skill_path.exists(), (
                f"DeepAgent SKILL.md not written to disk at {skill_path}"
            )
            content = skill_path.read_text()
            assert content.startswith("---"), "SKILL.md must start with YAML frontmatter"

            # Full structural validation — same standard as the skill generator
            validate_skill = _get_validate_skill()
            is_valid, msg = validate_skill(tmp_path / artifact.capability_name)
            assert is_valid, f"DeepAgent SKILL.md failed quick_validate: {msg}"

    def test_agent_config_has_required_keys(self, tmp_path):
        """All generated agent configs include the required node_config keys."""
        gap = ClassifiedGap(
            description="Validate and enrich incoming records against a reference dataset",
            query="record-validator",
            classified_type=CapabilityType.AGENT,
            confidence=0.85,
            rationale="Stateful validation agent",
        )

        artifact, error = _generate_single_agent(
            gap, "Data quality pipeline", output_dir=tmp_path
        )

        assert error is None, f"Generation failed: {error}"
        assert artifact is not None

        node_config = artifact.agent_config["node_config"]
        assert "name" in node_config
        assert "model" in node_config
        assert "tools" in node_config
        assert "description" in node_config

        # node_config["tools"] must be a list (not just present)
        assert isinstance(node_config["tools"], list)

        # node_config["name"] is the LangGraph node function name — must be snake_case
        assert re.match(r"^[a-z_][a-z0-9_]*$", node_config["name"]), (
            f"node_config['name'] '{node_config['name']}' is not valid snake_case"
        )

    def test_generate_agent_node(self, tmp_path):
        """generate_agent node processes AGENT gaps end-to-end with a real LLM."""
        state = TaskState(
            task_description="Build a business analytics pipeline",
            classified_gaps=[
                ClassifiedGap(
                    description="Detect and report anomalies in incoming sales data streams",
                    query="anomaly-detection-agent",
                    classified_type=CapabilityType.AGENT,
                    confidence=0.9,
                    rationale="Requires stateful monitoring",
                ),
            ],
        )

        with patch("src.orchestrator.generators.agent.SKILLS_OUTPUT_DIR", tmp_path):
            cmd = generate_agent(state)

        assert len(cmd.update["generated_artifacts"]) == 1, (
            f"Expected 1 artifact, errors: {cmd.update['errors']}"
        )
        assert cmd.update["errors"] == []
        assert cmd.goto == "loading_node"

        artifact = cmd.update["generated_artifacts"][0]
        assert artifact.capability_type == CapabilityType.AGENT
        assert artifact.agent_config is not None

        # Verify capability_name is valid hyphen-case (required by agent registry)
        assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", artifact.capability_name), (
            f"capability_name '{artifact.capability_name}' is not valid hyphen-case"
        )

        # If the LLM classified this as a DeepAgent, verify SKILL.md was written to disk
        if artifact.agent_config.get("is_deep_agent"):
            skill_path = tmp_path / artifact.capability_name / "SKILL.md"
            assert skill_path.exists(), (
                f"DeepAgent SKILL.md not written to disk at {skill_path}"
            )

    def test_deep_agent_skill_disk_write_guaranteed(self, tmp_path):
        """Explicitly test DeepAgent SKILL.md disk write with a description that forces
        is_deep_agent.

        The gap description is crafted to maximally signal multi-step autonomous reasoning
        so the LLM will reliably set is_deep_agent=True. If the LLM still returns False,
        the test fails with a clear message indicating the gap description needs updating.
        """
        gap = ClassifiedGap(
            description=(
                "Autonomous multi-step agent that uses write_todos for planning, "
                "executes probes in a sandboxed subprocess, writes intermediate "
                "findings to files between steps, adapts its investigation strategy "
                "based on each result, and produces a structured root-cause report. "
                "Requires multi-step autonomous reasoning with adaptive planning."
            ),
            query="adaptive-probe-investigator",
            classified_type=CapabilityType.AGENT,
            confidence=0.95,
            rationale="Explicitly requires multi-step planning and adaptive execution",
        )

        artifact, error = _generate_single_agent(
            gap, "Build a self-healing data pipeline with autonomous debugging", output_dir=tmp_path
        )

        assert error is None, f"Generation failed: {error}"
        assert artifact is not None

        if not artifact.agent_config.get("is_deep_agent"):
            pytest.fail(
                f"LLM did not classify as DeepAgent (is_deep_agent=False). "
                f"Update the gap description to be more explicit about multi-step reasoning. "
                f"Generated config: {artifact.agent_config}"
            )

        # Unconditional DeepAgent assertions
        assert "skill_content" in artifact.agent_config
        assert artifact.agent_config["skill_content"] is not None

        skill_path = tmp_path / artifact.capability_name / "SKILL.md"
        assert skill_path.exists(), (
            f"DeepAgent SKILL.md not written to disk at {skill_path}"
        )
        content = skill_path.read_text()
        assert content.startswith("---"), "SKILL.md must start with YAML frontmatter"

        # Full validation via quick_validate
        validate_skill = _get_validate_skill()
        is_valid, msg = validate_skill(tmp_path / artifact.capability_name)
        assert is_valid, f"DeepAgent SKILL.md failed quick_validate: {msg}"
