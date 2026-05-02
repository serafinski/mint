"""Unit tests for ``src/orchestrator/output_coerce.py``.

Security audit finding #11
(2026-04-19).

The pre-fix idiom ``parsed.get("result", {}) or {}`` conflated two
questions (result-key-present vs truthy-value) and dropped legitimate
zero-valued / empty / False answers. These tests lock the correct
semantics: preserve falsy values and only substitute ``{}`` when the
``result`` key is absent entirely.
"""

from __future__ import annotations

import pytest

from src.orchestrator.output_coerce import extract_tool_result


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
