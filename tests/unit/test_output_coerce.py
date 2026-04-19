"""Unit tests for ``src/cli/output_coerce.py`` — security audit finding #11
(2026-04-19).

The pre-fix idiom ``parsed.get("result", {}) or {}`` conflated two
questions (result-key-present vs truthy-value) and dropped legitimate
zero-valued / empty / False answers. These tests lock the correct
semantics: preserve falsy values, coerce non-dicts, only substitute
``{}`` when the ``result`` key is absent entirely.
"""

from __future__ import annotations

import pytest

from src.cli.output_coerce import as_dict_for_save, extract_tool_result


class TestExtractToolResult:
    @pytest.mark.parametrize("value", [0, 0.0, False, "", [], {}, None])
    def test_falsy_values_preserved(self, value):
        """A tool legitimately returning a falsy answer must round-trip —
        pre-fix these were coerced to ``{}``."""
        assert extract_tool_result({"result": value}) == value

    @pytest.mark.parametrize("value", [5, 3.14, True, "hello", [1, 2, 3], {"k": 1}])
    def test_truthy_values_preserved(self, value):
        assert extract_tool_result({"result": value}) == value

    def test_missing_result_key_returns_empty_dict(self):
        """No ``result`` key → the tool produced no output envelope;
        caller should treat as failure. ``{}`` is the failure signal."""
        assert extract_tool_result({}) == {}
        assert extract_tool_result({"error": "oops"}) == {}

    def test_distinguishes_missing_key_from_none_value(self):
        """``{"result": None}`` — the tool returned None explicitly.
        Pre-fix this was coerced to ``{}``. Post-fix it's preserved
        because ``None`` is distinct from "key missing."""
        # ``result: None`` is preserved as None, not coerced to {}.
        assert extract_tool_result({"result": None}) is None
        # Missing key is the failure signal.
        assert extract_tool_result({"other": 1}) == {}


class TestAsDictForSave:
    def test_dict_passes_through(self):
        assert as_dict_for_save({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    @pytest.mark.parametrize(
        "value",
        [0, 5, 3.14, "hello", [1, 2, 3], True, False, None],
    )
    def test_non_dict_wrapped_for_scan(self, value):
        """Non-dict outputs are wrapped as ``{"_result": output}`` so
        the per-key scan in ``_save_output`` doesn't crash on
        ``.items()``. The full JSON dump still serializes the original
        shape — this wrapper is per-key-scan-only."""
        wrapped = as_dict_for_save(value)
        assert wrapped == {"_result": value}
        # Can be iterated as dict (the whole point).
        assert list(wrapped.items()) == [("_result", value)]

    def test_empty_dict_passes_through(self):
        """Empty dict is still a dict — no wrapping."""
        assert as_dict_for_save({}) == {}


# ---------------------------------------------------------------------------
# Security audit finding #13 — filesystem containment (2026-04-20)
# ---------------------------------------------------------------------------


class TestSanitiseComponent:
    """Sanitisation strips path separators and shell metacharacters.
    Used by ``contained_join`` to produce filesystem-safe single
    components before joining them to a base directory."""

    def test_plain_name_preserved(self):
        from src.cli.output_coerce import _sanitise_component
        assert _sanitise_component("my-capability", "fb") == "my-capability"

    def test_dots_and_hyphens_preserved(self):
        from src.cli.output_coerce import _sanitise_component
        assert _sanitise_component("tool_v1.2-beta", "fb") == "tool_v1.2-beta"

    def test_path_separators_dropped(self):
        """``../escape`` → sanitised to ``.._escape`` or similar — the
        slash is dropped. Crucially, NOT just ``..``."""
        from src.cli.output_coerce import _sanitise_component
        result = _sanitise_component("../escape", "fb")
        assert "/" not in result
        assert result != ".."  # pure-dots rejected → fallback fires

    def test_pure_dots_rejected(self):
        from src.cli.output_coerce import _sanitise_component
        assert _sanitise_component("..", "fb") == "fb"
        assert _sanitise_component(".", "fb") == "fb"
        assert _sanitise_component("...", "fb") == "fb"

    def test_empty_returns_fallback(self):
        from src.cli.output_coerce import _sanitise_component
        assert _sanitise_component("", "fb") == "fb"
        assert _sanitise_component("!!!@@@", "fb") == "fb"

    def test_shell_metacharacters_dropped(self):
        from src.cli.output_coerce import _sanitise_component
        result = _sanitise_component("evil; rm -rf", "fb")
        assert ";" not in result
        assert " " not in result


class TestContainedJoin:
    """Path joins that refuse to escape the base directory."""

    def test_plain_part_joined_normally(self, tmp_path):
        from src.cli.output_coerce import contained_join
        result = contained_join(tmp_path, "subdir", "file.json")
        assert str(result).startswith(str(tmp_path))
        assert result.name == "file.json"

    def test_path_traversal_replaced_with_fallback(self, tmp_path):
        """``../escape`` must NOT resolve above tmp_path."""
        from src.cli.output_coerce import contained_join
        result = contained_join(tmp_path, "../escape", fallback="safe.bin")
        assert tmp_path.resolve() in result.resolve().parents or \
            result.resolve() == (tmp_path / "safe.bin").resolve() or \
            result.resolve().parent == tmp_path.resolve()
        # Critical: the resolved path must live under tmp_path.
        assert result.resolve().is_relative_to(tmp_path.resolve())

    def test_output_key_escape_attempt_contained(self, tmp_path):
        """Reporter's repro: output key ``'../escape-key'``."""
        from src.cli.output_coerce import contained_join
        result = contained_join(tmp_path, "../escape-key.json", fallback="output.json")
        assert result.resolve().is_relative_to(tmp_path.resolve())

    def test_output_files_fname_escape_attempt_contained(self, tmp_path):
        """Reporter's repro: output_files filename ``'../escape.bin'``."""
        from src.cli.output_coerce import contained_join
        result = contained_join(tmp_path, "../escape.bin", fallback="output.bin")
        assert result.resolve().is_relative_to(tmp_path.resolve())

    def test_absolute_path_as_part_contained(self, tmp_path):
        """If a part itself is an absolute path like ``/etc/passwd``,
        the sanitisation strips the leading slash and the result lives
        under base."""
        from src.cli.output_coerce import contained_join
        result = contained_join(tmp_path, "/etc/passwd", fallback="safe.txt")
        assert result.resolve().is_relative_to(tmp_path.resolve())

    def test_nested_traversal_contained(self, tmp_path):
        """Multi-component traversal: ``subdir/../../escape``."""
        from src.cli.output_coerce import contained_join
        result = contained_join(tmp_path, "subdir/../../escape", fallback="safe")
        assert result.resolve().is_relative_to(tmp_path.resolve())
