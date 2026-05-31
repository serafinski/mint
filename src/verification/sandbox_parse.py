"""Shared sandbox-stdout parser used by behavioral and coverage checks.

Sandbox snippets emit their result as a final JSON line, but generated tools
may print debug output, warnings, or diagnostics before that line. This module
centralizes the parser contract: parse the last non-empty stdout line as JSON
and return ``None`` on any failure.

Used by ``behavioral._parse_sandbox_output`` and
``coverage._parse_replay_output``.
"""

from __future__ import annotations

import json


def parse_sandbox_last_json_line(sandbox_result: dict) -> dict | None:
    """Parse the last non-empty line of ``sandbox_result["stdout"]`` as JSON.

    Returns ``None`` when:
    - ``success`` is False (sandbox process failed)
    - stdout is empty or whitespace-only
    - the last non-empty line fails to parse as JSON

    Tolerates any non-JSON prefix in stdout (debug prints, import
    warnings, Docker diagnostic lines) as long as the final JSON line
    the sandbox code explicitly emits is the LAST non-empty line.
    """
    if not sandbox_result.get("success"):
        return None
    stdout = (sandbox_result.get("stdout") or "").strip()
    if not stdout:
        return None
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except (json.JSONDecodeError, ValueError):
        return None
