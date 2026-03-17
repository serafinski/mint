"""Skill generator — produces DeepAgents SKILL.md files from classified gaps.

A plain LangGraph node (no DeepAgent) that generates SKILL.md files for each
gap classified as ``CapabilityType.SKILL``.  Each generated skill is validated
with ``tools/quick_validate.py`` and written to ``.deepagents/skills/<name>/``.
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import logging
import re
import tempfile
import threading
from pathlib import Path
from typing import Literal

import yaml
from langgraph.types import Command

from src.orchestrator.generators.utils import build_task_context, slugify_gap_hyphen
from src.orchestrator.prompts.skill_generator import SKILL_GENERATOR_PROMPT, SKILL_REPAIR_PROMPT
from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap, GeneratedArtifact, TaskState, TaskStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Project root (for writing generated skills)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
SKILLS_OUTPUT_DIR = _PROJECT_ROOT / ".deepagents" / "skills"

# ---------------------------------------------------------------------------
# Import validate_skill from tools/quick_validate.py
# ---------------------------------------------------------------------------

_VALIDATE_MODULE = None


def _get_validate_skill():
    """Lazily import ``validate_skill`` from ``tools/quick_validate.py``."""
    global _VALIDATE_MODULE  # noqa: PLW0603
    if _VALIDATE_MODULE is None:
        validate_path = _PROJECT_ROOT / "tools" / "quick_validate.py"
        if not validate_path.exists():
            raise RuntimeError(
                f"tools/quick_validate.py not found at {validate_path}. "
                "This file is required for skill validation."
            )
        spec = importlib.util.spec_from_file_location("quick_validate", validate_path)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise RuntimeError(f"Failed to load tools/quick_validate.py: {exc}") from exc
        # Only assign after successful execution so a failure doesn't leave a
        # broken module in the cache (which would bypass the guard on next call).
        _VALIDATE_MODULE = module
    return _VALIDATE_MODULE.validate_skill


# ---------------------------------------------------------------------------
# Shared LLM instance (lazy initialisation)
# ---------------------------------------------------------------------------

MAX_GENERATION_WORKERS = 4

_skill_llm = None
_skill_llm_lock = threading.Lock()


def _get_llm():
    """Lazily create the skill generator LLM on first use (thread-safe)."""
    global _skill_llm  # noqa: PLW0603
    if _skill_llm is None:
        with _skill_llm_lock:
            if _skill_llm is None:
                from langchain.chat_models import init_chat_model

                _skill_llm = init_chat_model("openai:gpt-5-mini", request_timeout=300)
    return _skill_llm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences wrapping the content, if present.

    Uses fence-parity counting to avoid stripping inner code-block fences when
    the outer closing fence is absent.  An odd count of backtick-triple sequences
    in the body means one fence is unmatched — that is the outer closing fence.
    An even count means all fences are matched inner pairs — leave them intact.
    """
    text = text.strip()
    if not text.startswith("```"):
        return text
    # Remove opening fence (with optional language tag)
    body = re.sub(r"^```[a-zA-Z]*\n?", "", text, count=1)
    # Count all backtick-triple occurrences in the body.
    # Odd  → last one is the unmatched outer closing fence → strip it.
    # Even → all fences are matched inner pairs → leave them intact.
    if len(re.findall(r"```", body)) % 2 == 1:
        body = re.sub(r"\n?```\s*$", "", body, count=1)
    return body.strip()


def _parse_frontmatter(content: str) -> dict | None:
    """Parse YAML frontmatter from SKILL.md content.  Returns None on failure."""
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return None
    try:
        data = yaml.safe_load(match.group(1))
        return data if isinstance(data, dict) else None
    except yaml.YAMLError:
        return None


def extract_skill_name(content: str) -> str | None:
    """Extract the ``name`` field from SKILL.md YAML frontmatter."""
    frontmatter = _parse_frontmatter(content)
    if frontmatter is None:
        return None
    name = frontmatter.get("name")
    if isinstance(name, str):
        return name.strip()
    return None


def extract_skill_description(content: str) -> str | None:
    """Extract the ``description`` field from SKILL.md YAML frontmatter."""
    frontmatter = _parse_frontmatter(content)
    if frontmatter is None:
        return None
    desc = frontmatter.get("description")
    if isinstance(desc, str):
        return desc.strip()
    return None


def validate_skill_content(content: str, skill_name: str) -> tuple[bool, str]:
    """Validate SKILL.md content using ``tools/quick_validate.py``.

    Also checks that the skill body (content after the frontmatter) is
    non-empty — ``quick_validate.py`` only checks frontmatter fields.

    Writes the content to a temporary directory and runs ``validate_skill``.

    Returns:
        Tuple of (is_valid, message).
    """
    if not skill_name or not skill_name.strip():
        return False, "skill_name must be non-empty"

    # Guard against path-unsafe names (e.g. containing "/") — Path(tmp) / "a/b"
    # silently creates a nested path; mkdir() without parents=True then crashes
    # with FileNotFoundError rather than returning a validation error.
    if "/" in skill_name or "\\" in skill_name or "\x00" in skill_name:
        return False, (
            f"skill_name '{skill_name}' contains path-unsafe characters "
            "(forward slash, backslash, or null byte are not allowed)"
        )

    # Additional check: non-empty body after frontmatter closing ---
    # quick_validate.py only validates frontmatter; it does not check body.
    parts = re.split(r"^---\s*$", content, maxsplit=2, flags=re.MULTILINE)
    if len(parts) >= 3:
        body = parts[2].strip()
        if not body:
            return False, "SKILL.md body (after frontmatter) must be non-empty"

    validate_skill = _get_validate_skill()
    with tempfile.TemporaryDirectory() as tmp_dir:
        skill_dir = Path(tmp_dir) / skill_name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(content)
        return validate_skill(skill_dir)


# ---------------------------------------------------------------------------
# Single skill generation
# ---------------------------------------------------------------------------


def _generate_single_skill(
    gap: ClassifiedGap,
    task_context: str,
    output_dir: Path | None = None,
) -> tuple[GeneratedArtifact | None, str | None]:
    """Generate a single SKILL.md for a classified gap.

    Returns:
        Tuple of (artifact, error).  Exactly one will be ``None``.
    """
    forced_name = slugify_gap_hyphen(gap.query)

    llm = _get_llm()

    # No per-node CallbackHandler: invoked from a LangGraph node so the pipeline-level
    # handler propagates automatically via LangChain's contextvar inheritance.

    prompt = SKILL_GENERATOR_PROMPT.format(
        description=gap.description,
        query=gap.query,
        task_context=task_context,
        forced_name=forced_name,
    )

    # --- First attempt ---
    try:
        response = llm.invoke(prompt)
        content = _strip_code_fences(response.content)
    except Exception as exc:  # noqa: BLE001
        error_msg = f"LLM call failed for skill '{gap.query}': {exc}"
        logger.warning(error_msg)
        return None, error_msg

    skill_name = extract_skill_name(content)
    if not skill_name:
        # Derive a fallback name from the query; collapse consecutive hyphens
        skill_name = re.sub(r"[^a-z0-9-]", "-", gap.query.lower().strip())
        skill_name = re.sub(r"-{2,}", "-", skill_name)
        skill_name = skill_name[:64].strip("-")
        if not skill_name:
            return None, f"Cannot derive skill name from query: {gap.query!r}"

    is_valid, message = validate_skill_content(content, skill_name)

    # --- Repair pass ---
    if not is_valid:
        logger.warning(
            "Validation failed for skill '%s' — triggering repair pass. "
            "Error: %s. Malformed content (first 500 chars): %.500s",
            skill_name,
            message,
            content,
        )
        emit_langfuse_event(
            None,
            "skill.validation_failure",
            "WARNING",
            {"skill_name": skill_name, "error": message},
        )
        repair_prompt = SKILL_REPAIR_PROMPT.format(
            validation_error=message,
            skill_content=content,
            gap_description=gap.description,
            gap_query=gap.query,
            task_context=task_context,
        )
        try:
            repair_response = llm.invoke(repair_prompt)
            content = _strip_code_fences(repair_response.content)
            skill_name = extract_skill_name(content) or skill_name
            is_valid, message = validate_skill_content(content, skill_name)
        except Exception as exc:  # noqa: BLE001
            error_msg = f"Repair LLM call failed for skill '{skill_name}': {exc}"
            logger.warning(error_msg)
            emit_langfuse_event(
                None,
                "skill.repair_llm_failure",
                "ERROR",
                {"skill_name": skill_name, "error": str(exc)},
            )
            return None, error_msg

    if not is_valid:
        error_msg = f"Skill '{skill_name}' failed validation after repair: {message}"
        logger.warning(error_msg)
        emit_langfuse_event(
            None,
            "skill.repair_validation_failure",
            "ERROR",
            {"skill_name": skill_name, "error": message},
        )
        return None, error_msg

    # --- Write to disk ---
    target_dir = (output_dir or SKILLS_OUTPUT_DIR) / skill_name
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "SKILL.md").write_text(content)
    except OSError as exc:
        error_msg = f"Failed to write skill '{skill_name}' to disk: {exc}"
        logger.error(error_msg)
        return None, error_msg
    logger.info("Wrote skill '%s' to %s", skill_name, target_dir)

    # Override whatever name the LLM chose with the deterministic slug
    if skill_name != forced_name:
        logger.debug(
            "Overriding LLM skill name '%s' with stable slug '%s'",
            skill_name, forced_name,
        )

    artifact = GeneratedArtifact(
        capability_name=forced_name,
        capability_type=CapabilityType.SKILL,
        skill_content=content,
        description=extract_skill_description(content),
        gap_query=gap.query,
    )
    return artifact, None


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------


def generate_skill(
    state: TaskState,
) -> Command[Literal["generate_agent"]]:
    """Generate SKILL.md files for all gaps classified as ``SKILL``.

    Skips gaps whose ``classified_type`` is not ``CapabilityType.SKILL``.
    For each skill gap, calls the LLM, validates with ``quick_validate.py``,
    applies a repair pass if needed, and writes valid skills to disk.

    Returns a ``Command`` updating ``generated_artifacts`` and ``errors``,
    then hands off to ``generate_agent`` for the next stage of the chain.
    """
    artifacts: list[GeneratedArtifact] = list(state.generated_artifacts)
    errors: list[str] = list(state.errors)

    skill_gaps = [g for g in state.classified_gaps if g.classified_type == CapabilityType.SKILL]

    # On retry, skip gaps already covered by passing artifacts.
    existing_gap_queries = {a.gap_query for a in artifacts if a.gap_query is not None}

    gaps_to_generate = [g for g in skill_gaps if g.query not in existing_gap_queries]
    for skipped in skill_gaps:
        if skipped.query in existing_gap_queries:
            logger.info("  → skipping (already passed verification): %s", skipped.query)

    logger.info("generate_skill: %d gap(s) to generate (%d skipped)", len(gaps_to_generate), len(skill_gaps) - len(gaps_to_generate))
    task_context = build_task_context(state)

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_GENERATION_WORKERS) as pool:
        futures = {
            pool.submit(_generate_single_skill, gap, task_context): gap
            for gap in gaps_to_generate
        }
        raw_results: list[tuple[GeneratedArtifact | None, str | None]] = []
        for future in concurrent.futures.as_completed(futures):
            try:
                raw_results.append(future.result())
            except Exception as exc:
                gap = futures[future]
                raw_results.append((None, f"Generation failed for skill {gap.query!r}: {exc}"))

    for artifact, error in raw_results:
        if artifact is not None:
            artifacts = [a for a in artifacts if a.capability_name != artifact.capability_name]
            artifacts.append(artifact)
        if error is not None:
            errors.append(error)

    return Command(
        update={
            "generated_artifacts": artifacts,
            "errors": errors,
            "status": TaskStatus.GENERATING,
        },
        goto="generate_agent",
    )
