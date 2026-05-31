"""Helpers for coercing generated MCP tool output envelopes."""

from __future__ import annotations

from typing import Any

_MISSING = object()


def extract_tool_result(parsed: dict) -> Any:
    """Return the ``result`` value from a parsed tool-output envelope.

    Preserves falsy values (``0``, ``False``, ``""``, ``[]``): they are
    legitimate answers and must not be coerced to ``{}``. Returns ``{}``
    only when the envelope has no ``result`` key at all.
    """
    value = parsed.get("result", _MISSING)
    if value is _MISSING:
        return {}
    return value
