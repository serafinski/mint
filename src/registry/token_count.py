"""Static definition-token cost for registered capabilities.

The evaluation reports a capability's static definition footprint: the JSON
schema visible for MCP tools, the SKILL.md body for skills, or the parent
system prompt for agents. The count is stored separately from runtime
per-invocation token cost and is used by registry utility scoring and Chapter
6 analysis.

Never raises: serialization or tokenizer failures return 0 and log a warning,
so token accounting cannot block capability registration.
"""

from __future__ import annotations

import json
import logging

import tiktoken

from src.config import DEFAULT_LLM_MODEL
from src.schemas.capability import CapabilityType
from src.schemas.task import GeneratedArtifact

logger = logging.getLogger(__name__)

_PROVIDER_PREFIXES = ("openai:", "azure:")


def _tiktoken_model_name(model_name: str) -> str:
    """Return the model identifier expected by ``tiktoken``.

    LangChain model identifiers commonly include provider prefixes such as
    ``openai:gpt-5-mini``. ``tiktoken`` expects the raw model name, so strip
    known provider prefixes before resolving the encoding.
    """
    for prefix in _PROVIDER_PREFIXES:
        if model_name.startswith(prefix):
            return model_name[len(prefix):]
    return model_name


_ENCODING_MODEL = _tiktoken_model_name(DEFAULT_LLM_MODEL)
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
    """Serialize only the static definition surface for an artifact."""
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
