"""Unit tests for the agent generator module.

LLM-calling behaviour is covered by ``tests/integration/test_agent_generator.py``
with real DeepSeek calls.  These tests cover the pure-Python validation logic
and the node's routing behaviour without any LLM mocking.
"""

from __future__ import annotations

from unittest.mock import patch

from langgraph.types import Command

from src.orchestrator.generators.agent import (
    AgentGenerationResult,
    _generate_single_agent,
    _validate_result,
    generate_agent,
)
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap, GeneratedArtifact, TaskState, TaskStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_SKILL_CONTENT = """\
---
name: test-investigator
description: >
  Autonomous agent that investigates failures through multi-step reasoning.
---

# Test Investigator

This skill enables autonomous failure investigation.
"""


def _valid_plain_result() -> AgentGenerationResult:
    return AgentGenerationResult(
        agent_name="data-quality-monitor",
        system_prompt="# Data Quality Monitor\n\nYou monitor data quality.",
        node_name="data_quality_monitor_node",
        model="openai:gpt-5-mini",

        description="Monitors data quality metrics and flags anomalies.",
        is_deep_agent=False,
        skill_content=None,
    )


def _valid_deep_agent_result() -> AgentGenerationResult:
    # agent_name must match the `name` field in VALID_SKILL_CONTENT's frontmatter
    return AgentGenerationResult(
        agent_name="test-investigator",
        system_prompt="# Test Investigator\n\nYou investigate failures.",
        node_name="test_investigator_node",
        model="openai:gpt-5-mini",

        description="Investigates failures through multi-step analysis.",
        is_deep_agent=True,
        skill_content=VALID_SKILL_CONTENT,
    )


# ---------------------------------------------------------------------------
# Tests: _validate_result — pure Python, no LLM
# ---------------------------------------------------------------------------


class TestValidateResult:
    def test_valid_plain_agent(self):
        errors = _validate_result(_valid_plain_result())
        assert errors == []

    def test_valid_deep_agent(self):
        errors = _validate_result(_valid_deep_agent_result())
        assert errors == []

    def test_empty_system_prompt(self):
        result = _valid_plain_result()
        result.system_prompt = "  "
        errors = _validate_result(result)
        assert any("system_prompt" in e for e in errors)

    def test_invalid_agent_name(self):
        result = _valid_plain_result()
        result.agent_name = "Invalid_Name"
        errors = _validate_result(result)
        assert any("agent_name" in e for e in errors)

    def test_agent_name_too_long(self):
        result = _valid_plain_result()
        result.agent_name = "a" * 65
        errors = _validate_result(result)
        assert any("too long" in e for e in errors)

    def test_invalid_node_name(self):
        result = _valid_plain_result()
        result.node_name = "Invalid-Node"
        errors = _validate_result(result)
        assert any("node_name" in e for e in errors)

    def test_deep_agent_missing_skill_content(self):
        result = _valid_deep_agent_result()
        result.skill_content = None
        errors = _validate_result(result)
        assert any("skill_content" in e for e in errors)

    def test_skill_content_missing_frontmatter(self):
        result = _valid_deep_agent_result()
        result.skill_content = "No frontmatter here"
        errors = _validate_result(result)
        assert any("frontmatter" in e for e in errors)

    def test_skill_content_invalid_name_format(self):
        """skill_content with non-hyphen-case name fails quick_validate check."""
        result = _valid_deep_agent_result()
        result.skill_content = (
            "---\nname: Invalid_Name\ndescription: test\n---\n\n# Body\n"
        )
        errors = _validate_result(result)
        assert any("skill_content" in e for e in errors)

    def test_skill_content_empty_body(self):
        """skill_content with no body after frontmatter fails quick_validate check."""
        result = _valid_deep_agent_result()
        result.skill_content = "---\nname: valid-name\ndescription: test\n---\n"
        errors = _validate_result(result)
        assert any("skill_content" in e for e in errors)

    def test_agent_name_exactly_64_chars(self):
        """agent_name at the max 64-char boundary is valid."""
        result = _valid_plain_result()
        # Must still be valid hyphen-case; use single segment of 64 lowercase chars
        result.agent_name = "a" * 64
        errors = _validate_result(result)
        assert errors == []

    def test_skill_content_name_mismatch_with_agent_name(self):
        """skill_content frontmatter name that differs from agent_name must fail."""
        result = _valid_deep_agent_result()
        # Deliberately set skill_content with a different name
        result.skill_content = (
            "---\nname: different-name\ndescription: Test deep agent.\n---\n\n"
            "# Body\n\nContent here.\n"
        )
        errors = _validate_result(result)
        assert any("name" in e and "match" in e for e in errors)

    def test_skill_content_invalid_yaml_syntax(self):
        """skill_content with malformed YAML in frontmatter fails validation."""
        result = _valid_deep_agent_result()
        result.skill_content = "---\nname: : : bad yaml\n---\n\n# Body\n\nContent.\n"
        errors = _validate_result(result)
        assert any("skill_content" in e for e in errors)

    def test_node_name_with_leading_underscore_is_valid(self):
        """Leading underscore in node_name is currently allowed by the snake_case regex.

        This is intentional — Python function names may start with underscore.
        Document this as expected behavior so the test acts as a specification.
        """
        result = _valid_plain_result()
        result.node_name = "_private_node"
        errors = _validate_result(result)
        # Leading underscore is allowed; this test documents the current behavior
        assert errors == []

    def test_empty_agent_name(self):
        result = _valid_plain_result()
        result.agent_name = ""
        errors = _validate_result(result)
        assert any("agent_name" in e for e in errors)

    def test_whitespace_only_agent_name(self):
        """Whitespace-only agent_name is caught by the strip() check."""
        result = _valid_plain_result()
        result.agent_name = "   "
        errors = _validate_result(result)
        assert any("agent_name" in e for e in errors)

    def test_invalid_agent_name_with_skill_content_does_not_crash(self):
        """_validate_result must not crash (FileNotFoundError) when agent_name
        contains path-unsafe characters (e.g. '/') AND skill_content is present.

        Previously, _validate_result would call validate_skill_content(skill_content,
        'foo/bar') even after recording an agent_name error, and mkdir() without
        parents=True would raise FileNotFoundError instead of returning an error list.
        The guard in _validate_result skips validate_skill_content when agent_name
        is invalid.
        """
        result = _valid_deep_agent_result()
        result.agent_name = "foo/bar"  # path-unsafe: contains slash
        # Must return a list of errors, not raise
        errors = _validate_result(result)
        assert any("agent_name" in e for e in errors)
        assert any("skipped" in e for e in errors), (
            "Expected a 'skill_content validation skipped' error when agent_name is invalid"
        )

    def test_empty_node_name(self):
        result = _valid_plain_result()
        result.node_name = "  "
        errors = _validate_result(result)
        assert any("node_name" in e for e in errors)

    def test_empty_description(self):
        result = _valid_plain_result()
        result.description = ""
        errors = _validate_result(result)
        assert any("description" in e for e in errors)


# ---------------------------------------------------------------------------
# Node routing tests — no LLM needed
# ---------------------------------------------------------------------------


class TestGenerateAgentNode:
    def test_node_returns_command(self):
        """generate_agent returns a Command with correct structure."""
        state = TaskState(task_description="Test", classified_gaps=[])
        cmd = generate_agent(state)

        assert isinstance(cmd, Command)
        assert cmd.goto == "verification_node"
        assert "generated_artifacts" in cmd.update
        assert "errors" in cmd.update
        assert cmd.update["status"] == TaskStatus.GENERATING

    def test_empty_gaps(self):
        """Empty gaps → empty artifacts and errors."""
        state = TaskState(task_description="Empty", classified_gaps=[])
        cmd = generate_agent(state)

        assert cmd.update["generated_artifacts"] == []
        assert cmd.update["errors"] == []

    def test_skips_non_agent_gaps(self):
        """Node produces no artifacts when no AGENT gaps are present."""
        state = TaskState(
            task_description="Multi-type task",
            classified_gaps=[
                ClassifiedGap(
                    description="Parse CSV files",
                    query="parse-csv",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.95,
                    rationale="Atomic operation",
                ),
                ClassifiedGap(
                    description="ETL data transformation workflow",
                    query="etl-workflow",
                    classified_type=CapabilityType.SKILL,
                    confidence=0.85,
                    rationale="Multi-step",
                ),
            ],
        )
        cmd = generate_agent(state)

        assert len(cmd.update["generated_artifacts"]) == 0
        assert len(cmd.update["errors"]) == 0
        assert cmd.goto == "verification_node"

    def test_preserves_existing_artifacts(self):
        """Node preserves artifacts already in state when no AGENT gaps are present."""
        existing = GeneratedArtifact(
            capability_name="existing-tool",
            capability_type=CapabilityType.MCP_TOOL,
            source_code="# existing",
        )
        state = TaskState(
            task_description="Add more",
            classified_gaps=[],
            generated_artifacts=[existing],
        )
        cmd = generate_agent(state)

        assert len(cmd.update["generated_artifacts"]) == 1
        assert cmd.update["generated_artifacts"][0].capability_name == "existing-tool"

    def test_preserves_existing_errors(self):
        """Node preserves errors already in state."""
        state = TaskState(
            task_description="Add more",
            classified_gaps=[],
            errors=["pre-existing error from earlier node"],
        )
        cmd = generate_agent(state)

        assert "pre-existing error from earlier node" in cmd.update["errors"]

    def test_filters_agent_gaps_only(self):
        """Node only processes AGENT gaps, skips MCP_TOOL and SKILL."""
        state = TaskState(
            task_description="Multi-gap task",
            classified_gaps=[
                ClassifiedGap(
                    description="Autonomous data quality monitor",
                    query="data-quality-monitor",
                    classified_type=CapabilityType.AGENT,
                    confidence=0.9,
                    rationale="Needs monitoring loop",
                ),
                ClassifiedGap(
                    description="Parse CSV atomically",
                    query="parse-csv",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="Atomic operation",
                ),
                ClassifiedGap(
                    description="ETL workflow",
                    query="etl-workflow",
                    classified_type=CapabilityType.SKILL,
                    confidence=0.85,
                    rationale="Multi-step",
                ),
            ],
        )

        mock_artifact = GeneratedArtifact(
            capability_name="data-quality-monitor",
            capability_type=CapabilityType.AGENT,
            agent_config={
                "system_prompt": "# Monitor",
                "node_config": {"name": "monitor_node", "model": "openai:gpt-5-mini",
                                "description": "Monitors data quality."},
                "is_deep_agent": False,
            },
        )

        with patch(
            "src.orchestrator.generators.agent._generate_single_agent",
            return_value=(mock_artifact, None),
        ) as mock_gen:
            cmd = generate_agent(state)

        # Only the AGENT gap should be processed
        mock_gen.assert_called_once()
        call_gap = mock_gen.call_args[0][0]
        assert call_gap.classified_type == CapabilityType.AGENT

        assert len(cmd.update["generated_artifacts"]) == 1
        assert cmd.update["status"] == TaskStatus.GENERATING

    def test_mixed_success_and_failure(self):
        """Some generations succeed, some fail → partial artifacts + errors."""
        state = TaskState(
            task_description="Two agents needed",
            classified_gaps=[
                ClassifiedGap(
                    description="Data quality monitoring agent",
                    query="data-quality-monitor",
                    classified_type=CapabilityType.AGENT,
                    confidence=0.9,
                    rationale="Stateful monitoring",
                ),
                ClassifiedGap(
                    description="Anomaly detection agent",
                    query="anomaly-detector",
                    classified_type=CapabilityType.AGENT,
                    confidence=0.85,
                    rationale="Stateful detection",
                ),
            ],
        )

        artifact = GeneratedArtifact(
            capability_name="data-quality-monitor",
            capability_type=CapabilityType.AGENT,
            agent_config={
                "system_prompt": "# Monitor",
                "node_config": {"name": "monitor_node", "model": "openai:gpt-5-mini",
                                "description": "Monitors data quality."},
                "is_deep_agent": False,
            },
        )

        side_effects = [
            (artifact, None),
            (None, "Generation failed for anomaly-detector"),
        ]

        with patch(
            "src.orchestrator.generators.agent._generate_single_agent",
            side_effect=side_effects,
        ):
            cmd = generate_agent(state)

        assert len(cmd.update["generated_artifacts"]) == 1
        assert len(cmd.update["errors"]) == 1
        assert "anomaly-detector" in cmd.update["errors"][0]

    def test_deep_agent_disk_write_failure_returned_as_error(self, tmp_path):
        """OSError during DeepAgent skill disk write is caught and returned as a soft error.

        Uses a FILE at the target directory path to trigger NotADirectoryError
        (subclass of OSError) on mkdir(), mirroring the skill generator test pattern.
        """
        gap = ClassifiedGap(
            description="Autonomous pipeline failure investigator",
            query="pipeline-failure-investigator",
            classified_type=CapabilityType.AGENT,
            confidence=0.9,
            rationale="Multi-step reasoning",
        )

        valid_skill_content = (
            "---\nname: test-deep-agent\ndescription: Test deep agent.\n---\n\n"
            "# Body\n\nContent here.\n"
        )
        mock_result = AgentGenerationResult(
            agent_name="test-deep-agent",
            system_prompt="You are a test deep agent.",
            node_name="test_deep_agent",
            model="openai:gpt-5-mini",
    
            description="A test deep agent.",
            is_deep_agent=True,
            skill_content=valid_skill_content,
        )

        class MockStructuredLLM:
            def invoke(self, prompt, config=None):
                return mock_result

        class MockLLM:
            def with_structured_output(self, schema):
                return MockStructuredLLM()

        # Block the target directory by creating a FILE at the forced_name path
        # (the generator uses forced_name, not result.agent_name, for the directory).
        from src.orchestrator.generators.utils import slugify_gap_hyphen

        forced_name = slugify_gap_hyphen(gap.query)
        blocker = tmp_path / forced_name
        blocker.write_text("not a directory")

        with (
            patch(
                "src.orchestrator.generators.agent._get_llm",
                return_value=MockLLM(),
            ),
            patch(
                "src.orchestrator.generators.agent._validate_result",
                return_value=[],
            ),
        ):
            artifact, error = _generate_single_agent(
                gap, "Test context", output_dir=tmp_path
            )

        assert artifact is None
        assert error is not None
        assert "Failed to write" in error


# ---------------------------------------------------------------------------
# _generate_single_agent — repair-path unit tests (no real LLM calls)
# ---------------------------------------------------------------------------


class TestGenerateSingleAgent:
    """Tests for the LLM failure and repair paths inside _generate_single_agent.

    Patches ``_get_llm`` to avoid real API calls; verifies that every error
    path returns ``(None, error_string)`` rather than raising.
    """

    # A result with an invalid agent_name (CamelCase) — triggers _validate_result failure
    _INVALID_RESULT = AgentGenerationResult(
        agent_name="InvalidName",
        system_prompt="You are a monitor agent.",
        node_name="monitor_node",
        model="openai:gpt-5-mini",

        description="Monitors data quality.",
        is_deep_agent=False,
        skill_content=None,
    )
    _VALID_RESULT = AgentGenerationResult(
        agent_name="data-quality-monitor",
        system_prompt="You are a data quality monitor.",
        node_name="data_quality_monitor_node",
        model="openai:gpt-5-mini",

        description="Monitors data quality metrics.",
        is_deep_agent=False,
        skill_content=None,
    )

    def _make_mock_llm(self, responses):
        """Return a mock LLM whose with_structured_output().invoke() yields responses in order."""
        responses_iter = iter(responses)

        class _MockStructuredLLM:
            def invoke(self_, prompt, config=None):
                r = next(responses_iter)
                if isinstance(r, Exception):
                    raise r
                return r

        _mock_structured = _MockStructuredLLM()

        class _MockLLM:
            def with_structured_output(self_, schema):
                return _mock_structured

        return _MockLLM()

    def _gap(self):
        return ClassifiedGap(
            description="Monitor data quality metrics",
            query="data-quality-monitor",
            classified_type=CapabilityType.AGENT,
            confidence=0.9,
            rationale="Stateful monitoring",
        )

    def test_first_attempt_succeeds_plain_agent(self, tmp_path):
        """Valid plain agent result on first attempt returns artifact without repair."""
        with patch(
            "src.orchestrator.generators.agent._get_llm",
            return_value=self._make_mock_llm([self._VALID_RESULT]),
        ):
            artifact, error = _generate_single_agent(
                self._gap(), "context", output_dir=tmp_path
            )

        assert error is None
        assert artifact is not None
        # Fix 5: name is now a deterministic slug, not the LLM-chosen name
        from src.orchestrator.generators.utils import slugify_gap_hyphen
        assert artifact.capability_name == slugify_gap_hyphen("data-quality-monitor")
        assert artifact.capability_type == CapabilityType.AGENT
        assert artifact.agent_config is not None
        assert artifact.agent_config["is_deep_agent"] is False

    def test_repair_succeeds(self, tmp_path):
        """Invalid first attempt fixed by repair pass returns artifact."""
        with patch(
            "src.orchestrator.generators.agent._get_llm",
            return_value=self._make_mock_llm([self._INVALID_RESULT, self._VALID_RESULT]),
        ):
            artifact, error = _generate_single_agent(
                self._gap(), "context", output_dir=tmp_path
            )

        assert error is None
        assert artifact is not None
        from src.orchestrator.generators.utils import slugify_gap_hyphen
        assert artifact.capability_name == slugify_gap_hyphen("data-quality-monitor")

    def test_first_llm_call_fails(self, tmp_path):
        """LLM error on first attempt returns (None, error) without raising."""
        with patch(
            "src.orchestrator.generators.agent._get_llm",
            return_value=self._make_mock_llm([ValueError("network error")]),
        ):
            artifact, error = _generate_single_agent(
                self._gap(), "context", output_dir=tmp_path
            )

        assert artifact is None
        assert "Agent generation failed" in error

    def test_repair_llm_call_fails(self, tmp_path):
        """Invalid first attempt followed by repair LLM failure returns (None, error)."""
        with patch(
            "src.orchestrator.generators.agent._get_llm",
            return_value=self._make_mock_llm(
                [self._INVALID_RESULT, RuntimeError("timeout")]
            ),
        ):
            artifact, error = _generate_single_agent(
                self._gap(), "context", output_dir=tmp_path
            )

        assert artifact is None
        assert "repair failed" in error.lower()

    def test_repair_still_invalid(self, tmp_path):
        """Repair returning still-invalid result returns (None, error) without raising."""
        with patch(
            "src.orchestrator.generators.agent._get_llm",
            return_value=self._make_mock_llm(
                [self._INVALID_RESULT, self._INVALID_RESULT]
            ),
        ):
            artifact, error = _generate_single_agent(
                self._gap(), "context", output_dir=tmp_path
            )

        assert artifact is None
        assert "invalid after repair" in error

    def test_task_context_forwarded_to_prompt(self, tmp_path):
        """task_context passed to _generate_single_agent appears in the LLM prompt."""
        captured_prompts: list = []

        class _MockStructuredLLM:
            def invoke(self_, prompt, config=None):
                captured_prompts.append(prompt)
                return self._VALID_RESULT

        class _MockLLM:
            def with_structured_output(self_, schema):
                return _MockStructuredLLM()

        custom_context = "Business analytics pipeline for Q4 revenue reporting"

        with patch("src.orchestrator.generators.agent._get_llm", return_value=_MockLLM()):
            _generate_single_agent(self._gap(), task_context=custom_context, output_dir=tmp_path)

        assert len(captured_prompts) >= 1
        assert custom_context in captured_prompts[0]

    def test_repair_prompt_includes_validation_errors(self, tmp_path):
        """When first attempt fails validation, the repair LLM call receives the error messages."""
        captured_prompts: list = []
        call_count = 0

        class _MockStructuredLLM:
            def invoke(self_, prompt, config=None):
                nonlocal call_count
                call_count += 1
                captured_prompts.append(prompt)
                return self._INVALID_RESULT if call_count == 1 else self._VALID_RESULT

        class _MockLLM:
            def with_structured_output(self_, schema):
                return _MockStructuredLLM()

        with patch("src.orchestrator.generators.agent._get_llm", return_value=_MockLLM()):
            artifact, error = _generate_single_agent(
                self._gap(), "context", output_dir=tmp_path
            )

        assert error is None
        assert artifact is not None
        assert call_count == 2
        # The repair prompt (second call) must reference the validation errors from the first
        repair_prompt = captured_prompts[1]
        # _INVALID_RESULT has agent_name="InvalidName" which fails the hyphen-case check
        assert "agent_name" in repair_prompt or "InvalidName" in repair_prompt

    def test_deep_agent_skill_written_to_disk(self, tmp_path):
        """Successful DeepAgent generation writes SKILL.md to the output directory."""
        deep_agent_result = AgentGenerationResult(
            agent_name="test-investigator",
            system_prompt="You investigate failures.",
            node_name="test_investigator_node",
            model="openai:gpt-5-mini",
    
            description="Investigates failures.",
            is_deep_agent=True,
            skill_content=VALID_SKILL_CONTENT,
        )

        with patch(
            "src.orchestrator.generators.agent._get_llm",
            return_value=self._make_mock_llm([deep_agent_result]),
        ):
            artifact, error = _generate_single_agent(
                self._gap(), "context", output_dir=tmp_path
            )

        assert error is None
        assert artifact is not None
        assert artifact.agent_config["is_deep_agent"] is True
        assert "skill_content" in artifact.agent_config

        # Skill is written under forced_name (deterministic slug with hash),
        # not result.agent_name.
        from src.orchestrator.generators.utils import slugify_gap_hyphen

        forced_name = slugify_gap_hyphen(self._gap().query)
        skill_path = tmp_path / forced_name / "SKILL.md"
        assert skill_path.exists(), f"SKILL.md not found at {skill_path}"
        # The frontmatter name is rewritten to forced_name so directory and
        # SKILL.md name field match (DeepAgents requires this).
        written = skill_path.read_text()
        assert f"name: {forced_name}" in written
