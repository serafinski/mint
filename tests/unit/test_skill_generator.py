"""Unit tests for the skill generator node.

LLM-calling behaviour is covered by ``tests/integration/test_skill_generator.py``
with real DeepSeek calls.  These tests cover pure helper functions, content
validation (via quick_validate.py), and the node's routing logic without any
LLM mocking.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langgraph.types import Command

import src.orchestrator.generators.skill as _skill_module
from src.orchestrator.generators.skill import (
    _generate_single_skill,
    _get_validate_skill,
    _strip_code_fences,
    extract_skill_name,
    generate_skill,
    validate_skill_content,
)
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap, GeneratedArtifact, TaskState, TaskStatus

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_SKILL_MD = """\
---
name: csv-data-loader
description: "Load and parse CSV files for data pipeline ingestion."
---

# CSV Data Loader

Load CSV files into structured records with schema validation.

## Reading CSV Files

Use the standard library csv module for reliable parsing:

```python
import csv
from pathlib import Path

def load_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)
```

## Error Handling

Skip malformed rows and log warnings rather than failing the entire ingestion.
"""

INVALID_SKILL_MD_BAD_NAME = """\
---
name: CSV_Data_Loader
description: "Load CSV files."
---

# CSV Data Loader

Content here.
"""

INVALID_SKILL_MD_NO_FRONTMATTER = """\
# CSV Data Loader

No frontmatter at all.
"""

SKILL_MD_EMPTY_BODY = """\
---
name: csv-data-loader
description: "Load and parse CSV files."
---
"""


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestStripCodeFences:
    def test_no_fences(self):
        assert _strip_code_fences("hello") == "hello"

    def test_plain_fences(self):
        assert _strip_code_fences("```\nhello\n```") == "hello"

    def test_language_fences(self):
        assert _strip_code_fences("```markdown\nhello\n```") == "hello"

    def test_preserves_inner_content(self):
        text = "```\n---\nname: test\n---\n# Test\n```"
        result = _strip_code_fences(text)
        assert result.startswith("---")
        assert result.endswith("# Test")

    def test_inner_code_fence_preserved(self):
        """Content that itself contains a code fence is not double-stripped.

        The outer ``` is removed but the inner ``` (e.g. a Python example
        embedded in the SKILL.md body) must remain intact.
        """
        text = "```\n---\nname: test\n---\n# Body\n```python\npass\n```\n```"
        result = _strip_code_fences(text)
        assert result.startswith("---")
        # Inner python fence must survive
        assert "```python" in result

    def test_outer_closing_fence_absent_inner_fence_not_stripped(self):
        """When outer closing fence is absent, the inner code block's closing fence is kept.

        Regression test for BUG-01: without parity counting, the closing-fence
        regex incorrectly strips the final ``` of an inner code block when the
        outer wrapping ``` is present but has no corresponding closing ```.
        """
        text = "```\n---\nname: test\n---\n\n# Body\n```python\npass\n```"
        result = _strip_code_fences(text)
        # The inner code block's closing fence must NOT be stripped
        assert result.endswith("```"), (
            f"Inner code block closing fence was incorrectly stripped: {result!r}"
        )
        assert "```python" in result


class TestExtractSkillName:
    def test_valid_frontmatter(self):
        assert extract_skill_name(VALID_SKILL_MD) == "csv-data-loader"

    def test_no_frontmatter(self):
        assert extract_skill_name("# No frontmatter") is None

    def test_invalid_yaml(self):
        assert extract_skill_name("---\n: bad: yaml:\n---\ncontent") is None

    def test_missing_name(self):
        content = "---\ndescription: test\n---\ncontent"
        assert extract_skill_name(content) is None


class TestValidateSkillContent:
    def test_valid_content(self):
        is_valid, msg = validate_skill_content(VALID_SKILL_MD, "csv-data-loader")
        assert is_valid, msg

    def test_invalid_name(self):
        is_valid, msg = validate_skill_content(INVALID_SKILL_MD_BAD_NAME, "CSV_Data_Loader")
        assert not is_valid
        assert "hyphen-case" in msg

    def test_no_frontmatter(self):
        is_valid, msg = validate_skill_content(INVALID_SKILL_MD_NO_FRONTMATTER, "test")
        assert not is_valid

    def test_empty_body_rejected(self):
        """A skill with only frontmatter and no body must fail validation."""
        is_valid, msg = validate_skill_content(SKILL_MD_EMPTY_BODY, "csv-data-loader")
        assert not is_valid
        assert "non-empty" in msg

    def test_empty_skill_name_rejected(self):
        """Empty skill_name is rejected before reaching quick_validate."""
        is_valid, msg = validate_skill_content(VALID_SKILL_MD, "")
        assert not is_valid
        assert "non-empty" in msg

    def test_whitespace_skill_name_rejected(self):
        """Whitespace-only skill_name is rejected before reaching quick_validate."""
        is_valid, msg = validate_skill_content(VALID_SKILL_MD, "   ")
        assert not is_valid
        assert "non-empty" in msg

    def test_slash_in_skill_name_rejected(self):
        """skill_name containing '/' must be rejected (not crash with FileNotFoundError).

        Path(tmp) / 'foo/bar' creates a nested path; mkdir() without parents=True
        would raise FileNotFoundError instead of returning a validation error.
        This guard ensures we return (False, message) for path-unsafe names.
        """
        is_valid, msg = validate_skill_content(VALID_SKILL_MD, "foo/bar")
        assert not is_valid
        assert "path-unsafe" in msg

    def test_absolute_path_in_skill_name_rejected(self):
        """skill_name starting with '/' is rejected before reaching mkdir."""
        is_valid, msg = validate_skill_content(VALID_SKILL_MD, "/absolute/path")
        assert not is_valid
        assert "path-unsafe" in msg


# ---------------------------------------------------------------------------
# Node routing tests — no LLM needed
# ---------------------------------------------------------------------------


class TestGenerateSkillNode:
    def test_skips_non_skill_gaps(self):
        """Node returns empty artifacts when no SKILL gaps are present."""
        state = TaskState(
            task_description="Multi-type task",
            classified_gaps=[
                ClassifiedGap(
                    description="Parse JSON file",
                    query="parse-json",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="Atomic operation",
                ),
                ClassifiedGap(
                    description="Autonomous investigation agent",
                    query="investigation",
                    classified_type=CapabilityType.AGENT,
                    confidence=0.9,
                    rationale="Needs planning",
                ),
            ],
        )
        cmd = generate_skill(state)

        assert isinstance(cmd, Command)
        assert len(cmd.update["generated_artifacts"]) == 0
        assert len(cmd.update["errors"]) == 0
        assert cmd.goto == "generate_agent"

    def test_empty_gaps_command_structure(self):
        """Empty gaps → correct Command structure with all required keys and status."""
        state = TaskState(task_description="Empty", classified_gaps=[])
        cmd = generate_skill(state)

        assert isinstance(cmd, Command)
        assert cmd.update["generated_artifacts"] == []
        assert cmd.update["errors"] == []
        assert cmd.update["status"] == TaskStatus.GENERATING
        assert cmd.goto == "generate_agent"

    def test_preserves_existing_artifacts(self):
        """Node preserves artifacts already in state when no SKILL gaps are present."""
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
        cmd = generate_skill(state)

        assert len(cmd.update["generated_artifacts"]) == 1
        assert cmd.update["generated_artifacts"][0].capability_name == "existing-tool"

    def test_preserves_existing_errors(self):
        """Node preserves errors already in state."""
        state = TaskState(
            task_description="Add more",
            classified_gaps=[],
            errors=["pre-existing error from earlier node"],
        )
        cmd = generate_skill(state)

        assert "pre-existing error from earlier node" in cmd.update["errors"]

    def test_filters_skill_gaps_only(self):
        """Node only processes SKILL gaps, skips MCP_TOOL and AGENT."""
        state = TaskState(
            task_description="Multi-gap task",
            classified_gaps=[
                ClassifiedGap(
                    description="CSV data loading workflow",
                    query="csv-loader",
                    classified_type=CapabilityType.SKILL,
                    confidence=0.9,
                    rationale="Multi-step workflow",
                ),
                ClassifiedGap(
                    description="Parse JSON file atomically",
                    query="parse-json",
                    classified_type=CapabilityType.MCP_TOOL,
                    confidence=0.9,
                    rationale="Atomic operation",
                ),
                ClassifiedGap(
                    description="Autonomous investigation agent",
                    query="investigation",
                    classified_type=CapabilityType.AGENT,
                    confidence=0.8,
                    rationale="Needs planning",
                ),
            ],
        )

        mock_artifact = GeneratedArtifact(
            capability_name="csv-loader",
            capability_type=CapabilityType.SKILL,
            skill_content="---\nname: csv-loader\ndescription: test\n---\n\n# Body\n",
        )

        with patch(
            "src.orchestrator.generators.skill._generate_single_skill",
            return_value=(mock_artifact, None),
        ) as mock_gen:
            cmd = generate_skill(state)

        # Only the SKILL gap should be processed
        mock_gen.assert_called_once()
        call_gap = mock_gen.call_args[0][0]
        assert call_gap.classified_type == CapabilityType.SKILL

        assert len(cmd.update["generated_artifacts"]) == 1
        assert cmd.update["status"] == TaskStatus.GENERATING

    def test_disk_write_failure_returned_as_error(self, tmp_path):
        """OSError during disk write is caught and returned as a soft error.

        Uses a FILE at the target directory path to trigger NotADirectoryError
        (subclass of OSError) on mkdir(), without any LLM mocking.
        """
        from src.orchestrator.generators.skill import _generate_single_skill

        gap = ClassifiedGap(
            description="CSV loading workflow",
            query="csv-loader",
            classified_type=CapabilityType.SKILL,
            confidence=0.9,
            rationale="Multi-step",
        )

        # Block the target directory by creating a FILE at the forced_name path
        # (the generator uses forced_name, not skill_name, for the directory).
        # mkdir(...) will raise NotADirectoryError (subclass of OSError)
        from src.orchestrator.generators.utils import slugify_gap_hyphen

        forced_name = slugify_gap_hyphen(gap.query)
        blocker = tmp_path / forced_name
        blocker.write_text("not a directory")

        with (
            patch(
                "src.orchestrator.generators.skill._get_llm",
                return_value=type(
                    "LLM",
                    (),
                    {"invoke": lambda self, p, config=None: type(
                        "R", (), {"content": VALID_SKILL_MD}
                    )()},
                )(),
            ),
            patch(
                "src.orchestrator.generators.skill.validate_skill_content",
                return_value=(True, "Skill is valid!"),
            ),
            patch(
                "src.orchestrator.generators.skill.extract_skill_name",
                return_value="csv-loader",
            ),
        ):
            artifact, error = _generate_single_skill(
                gap, "Data pipeline", output_dir=tmp_path
            )

        assert artifact is None
        assert error is not None
        assert "Failed to write" in error

    def test_mixed_success_and_failure(self):
        """Some generations succeed, some fail → partial artifacts + errors."""
        state = TaskState(
            task_description="Two skills needed",
            classified_gaps=[
                ClassifiedGap(
                    description="CSV loading workflow",
                    query="csv-loader",
                    classified_type=CapabilityType.SKILL,
                    confidence=0.9,
                    rationale="Multi-step",
                ),
                ClassifiedGap(
                    description="Data normalisation workflow",
                    query="data-normaliser",
                    classified_type=CapabilityType.SKILL,
                    confidence=0.85,
                    rationale="Multi-step",
                ),
            ],
        )

        artifact = GeneratedArtifact(
            capability_name="csv-loader",
            capability_type=CapabilityType.SKILL,
            skill_content="---\nname: csv-loader\ndescription: test\n---\n\n# Body\n",
        )

        side_effects = [
            (artifact, None),
            (None, "Generation failed for data-normaliser"),
        ]

        with patch(
            "src.orchestrator.generators.skill._generate_single_skill",
            side_effect=side_effects,
        ):
            cmd = generate_skill(state)

        assert len(cmd.update["generated_artifacts"]) == 1
        assert len(cmd.update["errors"]) == 1
        assert "data-normaliser" in cmd.update["errors"][0]


# ---------------------------------------------------------------------------
# _generate_single_skill — repair-path unit tests (no real LLM calls)
# ---------------------------------------------------------------------------


class TestGenerateSingleSkill:
    """Tests for the LLM failure and repair paths inside _generate_single_skill.

    Patches ``_get_llm`` to avoid real API calls; verifies that every error
    path returns ``(None, error_string)`` rather than raising.

    The skill generator's LLM returns a plain response object with ``.content``,
    not a structured output — so the mock returns objects with ``.content``.
    """

    # Content that passes validation — used as the "valid repair" response
    _VALID_CONTENT = VALID_SKILL_MD
    # Content that fails validation — no frontmatter, so quick_validate rejects it
    _INVALID_CONTENT = "not a valid skill file — no YAML frontmatter"

    def _gap(self):
        return ClassifiedGap(
            description="Load CSV files for data pipeline ingestion",
            query="csv-loader",
            classified_type=CapabilityType.SKILL,
            confidence=0.9,
            rationale="Multi-step workflow",
        )

    def _make_mock_llm(self, responses):
        """Return a mock LLM whose invoke() yields responses or raises exceptions in order."""
        responses_iter = iter(responses)

        class _MockResponse:
            def __init__(self_, content):
                self_.content = content

        class _MockLLM:
            def invoke(self_, prompt, config=None):
                r = next(responses_iter)
                if isinstance(r, Exception):
                    raise r
                return _MockResponse(r)

        return _MockLLM()

    def test_first_attempt_succeeds(self, tmp_path):
        """Valid SKILL.md on first attempt returns artifact without repair."""
        with patch(
            "src.orchestrator.generators.skill._get_llm",
            return_value=self._make_mock_llm([self._VALID_CONTENT]),
        ):
            artifact, error = _generate_single_skill(self._gap(), "context", output_dir=tmp_path)

        assert error is None
        assert artifact is not None
        # Fix 5: name is now a deterministic slug, not the LLM-chosen name
        from src.orchestrator.generators.utils import slugify_gap_hyphen
        assert artifact.capability_name == slugify_gap_hyphen("csv-loader")
        assert artifact.capability_type == CapabilityType.SKILL
        assert artifact.skill_content is not None

    def test_repair_succeeds(self, tmp_path):
        """Invalid first attempt fixed by repair pass returns artifact."""
        with patch(
            "src.orchestrator.generators.skill._get_llm",
            return_value=self._make_mock_llm([self._INVALID_CONTENT, self._VALID_CONTENT]),
        ):
            artifact, error = _generate_single_skill(self._gap(), "context", output_dir=tmp_path)

        assert error is None
        assert artifact is not None
        from src.orchestrator.generators.utils import slugify_gap_hyphen
        assert artifact.capability_name == slugify_gap_hyphen("csv-loader")

    def test_first_llm_call_fails(self, tmp_path):
        """LLM error on first attempt returns (None, error) without raising."""
        with patch(
            "src.orchestrator.generators.skill._get_llm",
            return_value=self._make_mock_llm([ValueError("network error")]),
        ):
            artifact, error = _generate_single_skill(self._gap(), "context", output_dir=tmp_path)

        assert artifact is None
        assert "LLM call failed" in error

    def test_repair_llm_call_fails(self, tmp_path):
        """Invalid first attempt followed by repair LLM failure returns (None, error)."""
        with patch(
            "src.orchestrator.generators.skill._get_llm",
            return_value=self._make_mock_llm(
                [self._INVALID_CONTENT, RuntimeError("timeout")]
            ),
        ):
            artifact, error = _generate_single_skill(self._gap(), "context", output_dir=tmp_path)

        assert artifact is None
        assert "Repair LLM call failed" in error

    def test_repair_still_invalid(self, tmp_path):
        """Repair returning still-invalid content returns (None, error) without raising."""
        with patch(
            "src.orchestrator.generators.skill._get_llm",
            return_value=self._make_mock_llm(
                [self._INVALID_CONTENT, self._INVALID_CONTENT]
            ),
        ):
            artifact, error = _generate_single_skill(self._gap(), "context", output_dir=tmp_path)

        assert artifact is None
        assert "failed validation after repair" in error

    def test_skill_name_derived_from_query_when_frontmatter_has_no_name(self, tmp_path):
        """When content has no name in frontmatter, skill_name is derived from gap.query."""
        # Content without a 'name' field — extract_skill_name returns None
        content_no_name = "---\ndescription: A skill without a name.\n---\n\n# Body\n"
        gap = ClassifiedGap(
            description="Multi-step CSV pipeline",
            query="CSV Data Loader",  # Mixed-case with spaces — cleaned to hyphen-case
            classified_type=CapabilityType.SKILL,
            confidence=0.9,
            rationale="Workflow",
        )

        with (
            patch(
                "src.orchestrator.generators.skill._get_llm",
                return_value=self._make_mock_llm([content_no_name]),
            ),
            patch(
                "src.orchestrator.generators.skill.validate_skill_content",
                return_value=(True, "ok"),
            ),
        ):
            artifact, error = _generate_single_skill(gap, "context", output_dir=tmp_path)

        assert error is None
        assert artifact is not None
        # Fix 5: name is now a deterministic slug from gap.query
        from src.orchestrator.generators.utils import slugify_gap_hyphen
        assert artifact.capability_name == slugify_gap_hyphen("CSV Data Loader")

    def test_task_context_forwarded_to_prompt(self, tmp_path):
        """task_context passed to _generate_single_skill appears in the LLM prompt."""
        captured_prompts: list = []

        class _MockResponse:
            def __init__(self_, content):
                self_.content = content

        class _MockLLM:
            def invoke(self_, prompt, config=None):
                captured_prompts.append(prompt)
                return _MockResponse(VALID_SKILL_MD)

        custom_context = "Business analytics pipeline for Q4 revenue reporting"

        with patch(
            "src.orchestrator.generators.skill._get_llm",
            return_value=_MockLLM(),
        ):
            _generate_single_skill(self._gap(), task_context=custom_context, output_dir=tmp_path)

        assert len(captured_prompts) >= 1
        assert custom_context in captured_prompts[0]

    def test_all_special_char_query_returns_error(self, tmp_path):
        """When gap.query is all special chars, derived skill_name is empty → error."""
        # "@#$%" → "----" → collapse → "-" → strip → "" → guard triggers
        content_no_name = "---\ndescription: Some skill.\n---\n\n# Body\n"
        gap = ClassifiedGap(
            description="Something",
            query="@#$%",
            classified_type=CapabilityType.SKILL,
            confidence=0.9,
            rationale="Workflow",
        )

        with (
            patch(
                "src.orchestrator.generators.skill._get_llm",
                return_value=self._make_mock_llm([content_no_name]),
            ),
            patch(
                "src.orchestrator.generators.skill.validate_skill_content",
                return_value=(True, "ok"),
            ),
        ):
            artifact, error = _generate_single_skill(gap, "context", output_dir=tmp_path)

        assert artifact is None
        assert error is not None
        assert "Cannot derive skill name" in error


# ---------------------------------------------------------------------------
# Lazy-import safety: _VALIDATE_MODULE must not be cached on exec failure (Bug 2)
# ---------------------------------------------------------------------------


class TestGetValidateSkillLazyImport:
    def test_module_not_cached_on_exec_failure(self):
        """If exec_module raises, _VALIDATE_MODULE must remain None so the next
        call retries the load rather than returning a broken module.

        Regression test for Bug 2: previously _VALIDATE_MODULE was assigned
        before exec_module ran, leaving a broken module in the cache.
        """
        original = _skill_module._VALIDATE_MODULE
        try:
            # Reset the cache so we control the first load attempt
            _skill_module._VALIDATE_MODULE = None

            with patch("importlib.util.spec_from_file_location") as mock_spec_fn:
                mock_spec = MagicMock()
                mock_spec.loader.exec_module.side_effect = ImportError("broken module")
                mock_spec_fn.return_value = mock_spec

                with pytest.raises(RuntimeError, match="Failed to load"):
                    _get_validate_skill()

            # After the failure, _VALIDATE_MODULE must still be None
            assert _skill_module._VALIDATE_MODULE is None, (
                "_VALIDATE_MODULE was set to a broken module; "
                "next call would get AttributeError instead of retrying the load"
            )
        finally:
            # Restore to original state (may be a fully-loaded module or None)
            _skill_module._VALIDATE_MODULE = original
