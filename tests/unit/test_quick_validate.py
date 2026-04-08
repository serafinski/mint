"""Unit tests for tools/quick_validate.py — Tier 0 SKILL.md validator.

Covers:
- Frontmatter structural checks (existing behaviour)
- Tier 0 prose checks (A1 hardening): lorem ipsum, TODO: implement,
  '# placeholder return' literal markers
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from tools.quick_validate import validate_skill


def _write_skill(tmp_path: Path, content: str) -> str:
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(content)
    return str(skill_dir)


# ---------------------------------------------------------------------------
# Existing behaviour — clean and structurally invalid skills
# ---------------------------------------------------------------------------


class TestStructuralValidation:
    def test_valid_skill_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = (
                "---\n"
                "name: clean-skill\n"
                "description: A clean skill body\n"
                "---\n\n"
                "## Overview\n\n"
                "This skill does useful things.\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, msg = validate_skill(path)
            assert ok is True
            assert msg == "Skill is valid!"

    def test_missing_skill_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty_dir = Path(tmp) / "empty"
            empty_dir.mkdir()
            ok, msg = validate_skill(str(empty_dir))
            assert ok is False
            assert "SKILL.md" in msg

    def test_no_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_skill(Path(tmp), "# Just a heading\nBody text\n")
            ok, msg = validate_skill(path)
            assert ok is False


# ---------------------------------------------------------------------------
# A1: Tier 0 prose checks
# ---------------------------------------------------------------------------


class TestProseHardFails:
    def test_rejects_lorem_ipsum_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = (
                "---\n"
                "name: bad-skill\n"
                "description: A skill with lorem ipsum body\n"
                "---\n\n"
                "## Overview\n\n"
                "Lorem ipsum dolor sit amet, consectetur adipiscing elit.\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, msg = validate_skill(path)
            assert ok is False
            assert "lorem ipsum" in msg.lower()

    def test_rejects_lorem_ipsum_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = (
                "---\n"
                "name: bad-skill\n"
                "description: A skill\n"
                "---\n\n"
                "LOREM IPSUM placeholder content\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, _msg = validate_skill(path)
            assert ok is False

    def test_rejects_todo_implement(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = (
                "---\n"
                "name: todo-skill\n"
                "description: A skill\n"
                "---\n\n"
                "## Steps\n\n"
                "TODO: implement this section later.\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, msg = validate_skill(path)
            assert ok is False
            assert "TODO: implement" in msg

    def test_rejects_placeholder_return_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = (
                "---\n"
                "name: placeholder-skill\n"
                "description: A skill\n"
                "---\n\n"
                "## Example\n\n"
                "```python\n"
                "def thing():\n"
                "    # placeholder return\n"
                "    return None\n"
                "```\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, msg = validate_skill(path)
            assert ok is False
            assert "# placeholder return" in msg

    def test_legitimate_placeholder_word_passes(self):
        """T04 regression: 'placeholder' as a word in prose must NOT be
        rejected. Only the literal '# placeholder return' marker is blocked.
        """
        with tempfile.TemporaryDirectory() as tmp:
            content = (
                "---\n"
                "name: clean-skill\n"
                "description: Build an inventory pipeline\n"
                "---\n\n"
                "## Instructions\n\n"
                "Compute reorder quantities. Do not leave placeholder values\n"
                "in the output. Replace any placeholder fields with real\n"
                "computed values before returning.\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, msg = validate_skill(path)
            assert ok is True
            assert msg == "Skill is valid!"

    def test_legitimate_todo_word_passes(self):
        """The literal 'TODO: implement' is blocked, but the word 'TODO'
        on its own (e.g., 'TODO list', 'manage TODOs') must pass.
        """
        with tempfile.TemporaryDirectory() as tmp:
            content = (
                "---\n"
                "name: clean-skill\n"
                "description: Manage a TODO list\n"
                "---\n\n"
                "This skill helps manage TODO items in a project.\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, _msg = validate_skill(path)
            assert ok is True
