"""Shared helpers for coercing MCP tool output envelopes.

Security audit finding #11 (2026-04-19): three sites (`cli/runner.py`,
`tests/evaluation/execution.py`, `tests/evaluation/harness.py`) used
``parsed.get("result", {}) or {}`` which conflates two distinct
questions:

1. Did the tool's output envelope contain a ``result`` key at all?
   (Answer: check key presence — discriminates "tool raised" from
   "tool returned something".)
2. Is the returned value truthy? (Answer: ``bool()`` — discriminates
   "useful value" from "falsy value".)

The idiom answers #1 via the second-argument default, then destroys
the answer by applying #2. Legitimate zero-valued / empty / False
outputs are silently replaced with ``{}``, biasing thesis eval
telemetry against scalar-answer tasks. Non-dict truthy outputs (``5``,
``[1, 2, 3]``, ``"text"``) pass through untouched and crash
downstream consumers that expect a dict.

This module factors the correct semantics into one helper every site
calls.
"""

from __future__ import annotations

from typing import Any

_MISSING = object()


def extract_tool_result(parsed: dict) -> Any:
    """Return the ``result`` value from a parsed tool-output envelope.

    Preserves falsy values (``0``, ``False``, ``""``, ``[]``): they are
    legitimate answers and must not be coerced to ``{}``. Returns ``{}``
    ONLY when the envelope has no ``result`` key at all — the tool
    either raised or produced malformed output that a caller should
    treat as a hard failure.

    Args:
        parsed: the dict produced by ``json.loads(stdout)`` of the tool
            subprocess. Typically shape: ``{"result": <any>, "error": <str|None>}``.

    Returns:
        The raw ``result`` value (any type), or ``{}`` when the key is
        missing entirely. Falsy values pass through unchanged.
    """
    value = parsed.get("result", _MISSING)
    if value is _MISSING:
        return {}
    return value


def as_dict_for_save(output: Any) -> dict:
    """Coerce any tool output shape into a dict for per-key scanning.

    ``_save_output``'s per-key scan expects a dict; scalar / list /
    string outputs would crash with ``AttributeError: 'int' object has
    no attribute 'items'``. Wraps non-dict outputs in
    ``{"_result": output}`` so the scan handles them uniformly. The
    caller's full-output JSON dump (``json.dumps(output, default=str)``)
    still serializes the original shape — only the per-key pass needs
    this coercion.
    """
    if isinstance(output, dict):
        return output
    return {"_result": output}


# ---------------------------------------------------------------------------
# Filesystem containment — security audit finding #13 (2026-04-20)
# ---------------------------------------------------------------------------

import re
from pathlib import Path

# Name whitelist mirroring the DeepAgents skill-name policy
# (``tools/init_skill.py::_validate_skill_name``, also enforced by
# ``tools/quick_validate.py``). Capability names, output keys, and
# chain output filenames that don't match this pattern are rewritten
# to a safe fallback rather than joined into the run directory verbatim.
_SAFE_NAME_RE = re.compile(r"[A-Za-z0-9._\-]+")


def _sanitise_component(raw: str, fallback: str) -> str:
    """Return a filesystem-safe single-component name.

    Drops any character outside ``[A-Za-z0-9._-]`` — which excludes
    path separators (``/``, ``\\``), leading dots (so ``..`` can't
    survive — dots themselves are allowed but the result is re-
    validated below), and shell metacharacters. Returns ``fallback``
    when the sanitised result is empty or still resolves outside
    its parent after ``Path.resolve()``.
    """
    if not isinstance(raw, str):
        raw = str(raw)
    matches = _SAFE_NAME_RE.findall(raw)
    candidate = "_".join(matches) if matches else ""
    # Reject names that are purely dots (``.``, ``..``) or empty.
    if not candidate or candidate.strip(".") == "":
        return fallback
    return candidate


def contained_join(base: Path, *parts: str, fallback: str = "output") -> Path:
    """Join ``parts`` to ``base`` and assert the result stays under ``base``.

    Security audit finding #13 (2026-04-20): three unsanitised path joins
    in ``_save_output`` let LLM-chosen ``cap_name`` / output dict keys /
    ``output_files`` filenames escape the ``mint_output/<run>/``
    directory via ``..`` components. Each ``part`` is individually
    sanitised (characters outside ``[A-Za-z0-9._-]`` dropped), then
    joined; the resolved path is checked against
    ``base.resolve()`` via ``Path.is_relative_to`` and replaced with
    ``base / fallback`` if it escapes.

    Returns an absolute ``Path`` safely contained under ``base``.
    """
    safe_parts = [
        _sanitise_component(p, fallback=fallback) for p in parts
    ]
    candidate = base.joinpath(*safe_parts)
    try:
        resolved = candidate.resolve()
        base_resolved = base.resolve()
    except OSError:
        # Filesystem-level issue (broken symlink, permission); fall back.
        return base / fallback
    if not resolved.is_relative_to(base_resolved):
        # Defensive: sanitisation should have prevented this, but if a
        # future character-class loosening ever allows a separator
        # through, the containment check catches it.
        return base / fallback
    return candidate
