"""Integration tests for the skill generator (real LLM calls).

Verifies that the skill generator produces valid SKILL.md files using the
real DeepSeek LLM, that they pass ``tools/quick_validate.py``, and that they
are written to disk correctly.  All calls appear in LangFuse.

Requires DEEPSEEK_API_KEY environment variable to be set.
"""

from __future__ import annotations

import os
import re
from unittest.mock import patch

import pytest

from src.orchestrator.generators.skill import (
    _generate_single_skill,
    _get_validate_skill,
    generate_skill,
)
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap, GeneratedArtifact, TaskState

_has_api_key = bool(os.environ.get("DEEPSEEK_API_KEY"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _has_api_key, reason="DEEPSEEK_API_KEY not set"),
]


@pytest.fixture(autouse=True)
def _reset_skill_llm():
    """Reset the lazy-init LLM singleton so each test starts with a fresh model."""
    import src.orchestrator.generators.skill as mod

    mod._skill_llm = None
    yield
    mod._skill_llm = None


class TestSkillGeneratorLive:
    def test_generate_skill_passes_validation(self, tmp_path):
        """Real LLM generates a SKILL.md that passes tools/quick_validate.py."""
        gap = ClassifiedGap(
            description="Load and parse CSV files for data pipeline ingestion",
            query="csv-data-loader",
            classified_type=CapabilityType.SKILL,
            confidence=0.9,
            rationale="Multi-step data workflow",
        )

        artifact, error = _generate_single_skill(
            gap, "Build a data analytics pipeline", output_dir=tmp_path
        )

        assert error is None, f"Generation failed: {error}"
        assert artifact is not None
        assert isinstance(artifact, GeneratedArtifact)
        assert artifact.capability_type == CapabilityType.SKILL
        assert artifact.skill_content is not None

        # Verify capability_name is valid hyphen-case (required by DeepAgents skill loader)
        assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", artifact.capability_name), (
            f"capability_name '{artifact.capability_name}' is not valid hyphen-case"
        )

        # Verify quick_validate passes
        validate_skill = _get_validate_skill()
        skill_dir = tmp_path / artifact.capability_name
        is_valid, msg = validate_skill(skill_dir)
        assert is_valid, (
            f"Generated skill failed quick_validate: {msg}\n\n"
            f"Content:\n{artifact.skill_content}"
        )

    def test_skill_written_to_disk(self, tmp_path):
        """Generated skill SKILL.md is written under output_dir/<name>/SKILL.md."""
        gap = ClassifiedGap(
            description="Format and validate date strings for pipeline timestamps",
            query="date-formatter",
            classified_type=CapabilityType.SKILL,
            confidence=0.9,
            rationale="Reusable date handling workflow",
        )

        artifact, error = _generate_single_skill(
            gap, "Analytics pipeline date handling", output_dir=tmp_path
        )

        assert error is None, f"Generation failed: {error}"
        assert artifact is not None

        skill_path = tmp_path / artifact.capability_name / "SKILL.md"
        assert skill_path.exists(), f"Expected SKILL.md at {skill_path}"

        content = skill_path.read_text()
        assert content.startswith("---"), "SKILL.md must start with YAML frontmatter"
        assert len(content.strip()) > 50, "SKILL.md body must be non-trivial"

    def test_generate_skill_node(self, tmp_path):
        """generate_skill node processes SKILL gaps end-to-end with a real LLM."""
        state = TaskState(
            task_description="Build a data analytics pipeline",
            classified_gaps=[
                ClassifiedGap(
                    description="Transform and normalise raw sales records into a standard schema",
                    query="sales-data-normalizer",
                    classified_type=CapabilityType.SKILL,
                    confidence=0.9,
                    rationale="Multi-step transformation workflow",
                ),
            ],
        )

        with patch("src.orchestrator.generators.skill.SKILLS_OUTPUT_DIR", tmp_path):
            cmd = generate_skill(state)

        assert len(cmd.update["generated_artifacts"]) == 1, (
            f"Expected 1 artifact, errors: {cmd.update['errors']}"
        )
        assert cmd.update["errors"] == []
        assert cmd.goto == "generate_agent"

        artifact = cmd.update["generated_artifacts"][0]
        assert artifact.capability_type == CapabilityType.SKILL
        assert artifact.skill_content is not None

        # Verify capability_name is valid hyphen-case
        assert re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", artifact.capability_name), (
            f"capability_name '{artifact.capability_name}' is not valid hyphen-case"
        )

        # Verify SKILL.md was actually written to disk
        skill_path = tmp_path / artifact.capability_name / "SKILL.md"
        assert skill_path.exists(), f"Expected SKILL.md at {skill_path}"
