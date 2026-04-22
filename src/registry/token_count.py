"""Definition-string token cost for Pillar 3 M3 (Step 18.6).

The thesis proposal defines M3 as the token count of the *capability's
definition* — the JSON schema for MCP tools, the SKILL.md body for skills,
the system prompt for agents. This module tokenizes that definition with
tiktoken so Chapter 5/6 can report it side-by-side with the runtime
per-invocation cost that actually drives U(c).

Never-raises: any tokenizer exception returns 0 and logs a warning, so a
missing tokenizer or unexpected input shape cannot break registration.
"""

from __future__ import annotations

import json
import logging

import tiktoken

from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact

logger = logging.getLogger(__name__)

_ENCODING_MODEL = "gpt-4"
_encoding: tiktoken.Encoding | None = None


def _get_encoding() -> tiktoken.Encoding:
    global _encoding
    if _encoding is None:
        _encoding = tiktoken.encoding_for_model(_ENCODING_MODEL)
    return _encoding


def count_definition_tokens(artifact: GeneratedArtifact) -> int:
    """Return the token count of the artifact's definition string.

    Dispatches on ``capability_type``:
      - MCP_TOOL: serialized JSON of {description, input_schema, output_schema}
        (shape an MCP client sees in ``tools/list``).
      - SKILL: full ``skill_content`` (SKILL.md body including frontmatter).
      - AGENT: ``agent_config['system_prompt']`` (subagent prompts excluded —
        they only materialize on delegation, not in the registry listing).

    Returns 0 on any failure (missing fields, tokenizer exception, etc.).
    """
    try:
        definition = _serialize_definition(artifact)
    except Exception as exc:
        logger.warning(
            "definition_token_cost: could not serialize definition for %s: %s",
            artifact.capability_name,
            exc,
        )
        return 0

    if not definition:
        return 0

    try:
        return len(_get_encoding().encode(definition))
    except Exception as exc:
        logger.warning(
            "definition_token_cost: tokenizer failed for %s: %s",
            artifact.capability_name,
            exc,
        )
        return 0


def _serialize_definition(artifact: GeneratedArtifact) -> str:
    cap_type = artifact.capability_type

    if cap_type == CapabilityType.MCP_TOOL:
        payload = {
            "description": artifact.description or "",
            "input_schema": dict(artifact.input_schema or {}),
            "output_schema": dict(artifact.output_schema or {}),
        }
        return json.dumps(payload, sort_keys=True)

    if cap_type == CapabilityType.SKILL:
        return artifact.skill_content or ""

    if cap_type == CapabilityType.AGENT:
        config = artifact.agent_config or {}
        return config.get("system_prompt", "") or ""

    return ""
