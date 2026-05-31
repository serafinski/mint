"""Unit tests for ``src/orchestrator/output_coerce.py``.

The helper separates two questions that ordinary truthiness checks conflate:
whether the output envelope contains a ``result`` key, and whether that result
is truthy. Falsy values such as ``0``, ``False``, ``""``, ``[]``, ``{}``, and
``None`` are valid tool outputs and must round-trip unchanged.
"""

from __future__ import annotations

import pytest

from src.orchestrator.output_coerce import extract_tool_result


class TestExtractToolResult:
    @pytest.mark.parametrize("value", [0, 0.0, False, "", [], {}, None])
    def test_falsy_values_preserved(self, value):
        """A tool legitimately returning a falsy answer must round-trip."""
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
        """``{"result": None}`` means the tool returned None explicitly."""
        # ``result: None`` is preserved as None, not coerced to {}.
        assert extract_tool_result({"result": None}) is None
        # Missing key is the failure signal.
        assert extract_tool_result({"other": 1}) == {}
