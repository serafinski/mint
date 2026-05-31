"""Unit tests for tools/quick_validate.py.

Covers:
- SKILL.md existence and frontmatter structure
- prose hard-fails for known placeholder markers
- body length warnings
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
# Clean and structurally invalid skills
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


class TestBodyDelimiterValidation:
    def test_rejects_skill_with_bare_dashes_in_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = (
                "---\n"
                "name: test-skill\n"
                "description: test\n"
                "---\n"
                "# Body\n"
                "content\n"
                "---\n"
                "trailing content\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, msg = validate_skill(path)

            assert ok is False
            assert "---" in msg
            assert "extra bare-line" in msg or "delimiter" in msg.lower()

    def test_accepts_skill_without_body_delimiters(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = (
                "---\n"
                "name: clean-skill\n"
                "description: a valid skill\n"
                "---\n"
                "# Body\n"
                "Normal content here, no bare-dash delimiters.\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, msg = validate_skill(path)

            if not ok:
                assert "bare-line" not in msg
                assert "delimiter" not in msg.lower()


# ---------------------------------------------------------------------------
# Prose hard-fail checks
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
        """The word 'placeholder' can be legitimate prose.

        Only the literal '# placeholder return' marker is blocked.
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


# ---------------------------------------------------------------------------
# Body length warnings (Agent Skills spec: < 500 lines recommended)
# ---------------------------------------------------------------------------


class TestBodyLengthWarning:
    def test_short_body_no_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            content = (
                "---\n"
                "name: short-skill\n"
                "description: A short skill\n"
                "---\n\n"
                "## Overview\n\nBrief content.\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, msg = validate_skill(path)
            assert ok is True
            assert msg == "Skill is valid!"

    def test_long_body_warns(self):
        """A body over 500 lines should pass but include a warning."""
        with tempfile.TemporaryDirectory() as tmp:
            body_lines = "\n".join(f"Line {i}" for i in range(510))
            content = (
                "---\n"
                "name: long-skill\n"
                "description: A very long skill\n"
                "---\n\n"
                + body_lines
                + "\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, msg = validate_skill(path)
            assert ok is True
            assert "Warning" in msg
            assert "500" in msg

    def test_exactly_500_lines_no_warning(self):
        """Exactly 500 body lines should not trigger the warning.

        ``validate_skill`` splits the file via regex ``^---\\n.*?\\n---\\n?(.*)$``
        with ``DOTALL``. After the closing ``---\\n``, the optional ``\\n?`` is
        consumed (not captured); the second ``\\n`` from the ``---\\n\\n`` prefix
        lands at the *start* of the body capture group. Counts with
        ``range(499)`` producing 499 ``"Line i"`` entries joined by ``\\n`` plus
        a trailing ``\\n`` appended:

        * body = "\\n" + "Line 0\\nLine 1\\n...\\nLine 498" + "\\n"
        * newline count = 1 (leading) + 498 (between) + 1 (trailing) = 500
        * body ends with ``\\n`` so the final ``+1`` branch does NOT apply
        * body_lines = 500, threshold is ``> 500``, so there is no warning
        """
        with tempfile.TemporaryDirectory() as tmp:
            body_lines = "\n".join(f"Line {i}" for i in range(499))
            content = (
                "---\n"
                "name: edge-skill\n"
                "description: Edge case skill\n"
                "---\n\n"
                + body_lines
                + "\n"
            )
            path = _write_skill(Path(tmp), content)
            ok, msg = validate_skill(path)
            assert ok is True
            assert msg == "Skill is valid!"
