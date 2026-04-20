"""Shared sandbox-stdout parser used by behavioral + coverage paths.

Security audit findings #21 + #22 (2026-04-20): two parsers in
``src/verification/`` were fragile — both called ``json.loads(stdout)``
on the full stdout string and failed on any non-JSON prefix (debug
prints from the LLM-generated tool, pandas/matplotlib deprecation
warnings routed to stdout, etc.). A third parser in the same file
(``_parse`` inside ``_check_network_disconnect_differential``)
correctly used ``stdout.splitlines()[-1]`` — three parsers, one
contract, different robustness.

This module factors the correct semantics: parse the last non-empty
line of stdout as JSON, returning ``None`` on any failure. Shared
across ``behavioral._parse_sandbox_output``,
``behavioral._check_network_disconnect_differential._parse``, and
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
