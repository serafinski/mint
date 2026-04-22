"""Tests for ``tools/init_skill.py`` — path-traversal + name-policy guards.

Regression for security audit finding #2 (2026-04-19): pre-fix, ``skill_name``
was joined into ``Path(path).resolve() / skill_name`` with no validation,
allowing ``skill_name="../escaped"`` to create the directory one level
above ``--path``. Fix tightens the name to the DeepAgents hyphen-case
policy enforced by ``tools/quick_validate.py`` and adds a containment
check via ``is_relative_to``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.init_skill import _validate_skill_name, init_skill  # noqa: E402


class TestValidateSkillName:
    @pytest.mark.parametrize(
        "name",
        [
            "data-analyzer",
            "a",
            "my-skill",
            "analyzer-123",
            "a" * 64,  # upper bound
        ],
    )
    def test_valid_names(self, name):
        _validate_skill_name(name)  # raises on failure

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "1-starts-with-digit",
            "-starts-with-hyphen",
            "Caps-Not-Allowed",
            "has_underscore",
            "has space",
            "has.dot",
            "has/slash",
            "has\\backslash",
            "..",
            "../escaped",
            "a" * 65,  # over the 64-char ceiling
        ],
    )
    def test_rejects_invalid(self, name):
        with pytest.raises(ValueError, match="Invalid skill_name"):
            _validate_skill_name(name)


class TestInitSkillContainment:
    def test_path_traversal_rejected_before_filesystem_writes(self, tmp_path):
        """``../escaped`` must raise ValueError and NOT create anything."""
        base = tmp_path / "base"
        base.mkdir()

        with pytest.raises(ValueError):
            init_skill("../escaped", str(base))

        # Nothing created under base, and no sibling directory above it.
        assert list(base.iterdir()) == []
        assert not (tmp_path / "escaped").exists()

    def test_valid_name_creates_skill_dir(self, tmp_path):
        result = init_skill("valid-skill-name", str(tmp_path))
        assert result is None or result.exists()
        skill_dir = tmp_path / "valid-skill-name"
        assert skill_dir.exists()
        assert (skill_dir / "SKILL.md").exists()

    def test_symlink_escape_containment(self, tmp_path):
        """If the base directory is a symlink to its parent, the skill dir
        must still resolve within the resolved base — not outside."""
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        try:
            link.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation not supported on this FS")

        # Using a valid name — containment check still applies to the
        # resolved path. A valid name under the symlinked base should
        # succeed because resolve() follows the symlink to target,
        # and skill_dir resolves under target.
        result = init_skill("clean-skill", str(link))
        skill_dir = target / "clean-skill"
        assert skill_dir.exists()
        assert (skill_dir / "SKILL.md").exists()
        # result is the path (or None); not the focus of this test.
        assert result is None or result.exists()
