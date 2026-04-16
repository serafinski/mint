"""Task decomposition via DeepAgent.

Wraps a DeepAgent (``create_deep_agent``) in a LangGraph node function.
The agent uses ``write_todos`` for planning and ``response_format`` for
structured subtask output.

Operational boundaries (from CLAUDE.md):
- ``recursion_limit=50`` (max steps)
- Node-level timeout: 120 s
- Graph is a stateless CompiledStateGraph; state is fresh per ``.invoke()`` call
- On timeout / error: return partial results with error flag
"""

from __future__ import annotations

import concurrent.futures
import logging
from pathlib import Path
from typing import Literal

from deepagents import create_deep_agent
from langfuse.langchain import CallbackHandler as LangFuseCallbackHandler
from langgraph.types import Command

from src.orchestrator.prompts.decomposition import DECOMPOSITION_SYSTEM_PROMPT
from src.orchestrator.tracing import emit_langfuse_event
from src.schemas.task import DecompositionResult, SubTask, TaskState, TaskStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Operational boundaries
# ---------------------------------------------------------------------------

# Bumped 120 → 240 on 2026-04-14 after a test_mint run surfaced repeated
# timeouts on the T04 5-stage inventory reorder task. Empirical data (LangFuse
# traces across April 7 → April 14) shows the DeepAgent routinely completes
# its real work in 87–180s of LLM wall time on complex tasks, but tail variance
# in gpt-5-mini reasoning mode pushes wall clock to 200–400s intermittently.
# The 120s ceiling was tipping over on the unlucky tail. Reducing reasoning
# effort on the decomposer itself was rejected because it affects every task
# (including the easy ones that currently work) with quality impact on the
# entire downstream pipeline. Bumping the timeout is surgical — only the tail
# pays. See docs/perf_fixes_2026_04_14.md §2b.
DECOMPOSITION_TIMEOUT_S = 240
DECOMPOSITION_RECURSION_LIMIT = 50

# Two-stage circuit breaker for capability count.
# WARN: emit visibility signal but proceed intact — genuine 6-7 step tasks survive.
# TRUNCATE: pathological 14-step decompositions are caught before they explode
#           generation and verification time.
WARN_CAPS = 5
MAX_CAPS_HARD = 8


# ---------------------------------------------------------------------------
# Agent factory — creates a fresh agent per invocation
# ---------------------------------------------------------------------------


def _get_agent():
    """Create a fresh decomposition DeepAgent for each invocation.

    A new ``CompiledStateGraph`` is returned on every call so no state is
    carried between pipeline runs (CLAUDE.md: "Fresh every invocation —
    No state carried between calls").  ``create_deep_agent()`` compiles a
    LangGraph graph without making any API calls, so the overhead is minimal.
    """
    from src.config import DEFAULT_LLM_MODEL

    return create_deep_agent(
        model=DEFAULT_LLM_MODEL,
        system_prompt=DECOMPOSITION_SYSTEM_PROMPT,
        response_format=DecompositionResult,
        name="task-decomposer",
    )


# ---------------------------------------------------------------------------
# Chain coherence validation (Phase 2 Step 14 — decomposition coherence check)
# ---------------------------------------------------------------------------
#
# The decomposer can produce a DAG of subtasks that is internally incoherent:
# a downstream subtask claims to consume an ``upstream_data`` key that no
# transitive predecessor produces in its ``output_schema``. Each subtask
# passes verification in isolation, but the chain crashes at execution time
# with errors like ``upstream_data must include a 'transactions' key``.
#
# The canonical failure (T14 TSV task):
#   Step 1 ``parse-csv-file`` → output_schema={'departments': ...}
#   Step 2 ``aggregate-by-department`` with depends_on=[0],
#          requires_upstream_keys=['departments', 'transactions'].
#          Step 1 produces 'departments' but nothing produces 'transactions'.
#
# Implementation: **structured contract**, not prose parsing. The subtask
# schema carries an explicit ``requires_upstream_keys: list[str]`` field.
# The decomposer fills it in; validation is a set comparison against the
# union of transitive predecessors' ``output_schema`` keys. No regex, no
# natural-language heuristics.
#
# This aligns with the "Structured State Communication" section of the
# thesis proposal (docs/thesis_proposal.md): typed contracts over lossy
# natural-language parsing. An earlier version of this check parsed
# subtask descriptions for ``upstream_data['key']`` patterns, which
# false-positived on nested accesses (T04 inventory, 2026-04-14) and was
# fundamentally brittle — the LLM could defeat the check by rephrasing
# the same dependency in prose. The structured field removes that failure
# mode entirely.
#
# Fail-open semantics: validation failures append to ``state.errors`` and
# set ``decomposition_succeeded=False`` (Fix 2b sticky flag), but the
# pipeline continues so downstream nodes can produce partial results.


def _collect_transitive_upstream_keys(
    subtasks: list[SubTask], subtask_index: int
) -> set[str]:
    """Return the union of output_schema keys from every transitive predecessor.

    Walks the ``depends_on`` DAG breadth-first starting from the subtask at
    ``subtask_index``, collecting the keys declared in each predecessor's
    ``output_schema``. Protects against cycles with a visited set.
    """
    if subtask_index < 0 or subtask_index >= len(subtasks):
        return set()

    visited: set[int] = set()
    queue: list[int] = list(subtasks[subtask_index].depends_on)
    upstream_keys: set[str] = set()

    while queue:
        i = queue.pop(0)
        if i in visited:
            continue
        visited.add(i)
        if i < 0 or i >= len(subtasks):
            # Invalid depends_on index — skip. The circuit breaker earlier
            # in decomposition_node doesn't validate indices, so a pathological
            # decomposition could reference a non-existent predecessor.
            continue
        predecessor = subtasks[i]
        upstream_keys.update(predecessor.output_schema.keys())
        queue.extend(predecessor.depends_on)

    return upstream_keys


def _validate_chain_coherence(subtasks: list[SubTask]) -> list[str]:
    """Return a list of coherence violation messages for the subtask DAG.

    For each subtask, every key listed in ``requires_upstream_keys`` must
    appear in the ``output_schema`` of at least one transitive predecessor
    (walked via ``depends_on``). Empty list means the chain is coherent.

    Single-subtask chains and subtasks with empty ``requires_upstream_keys``
    are trivially coherent — the check returns early.

    **This function reports HARD violations only** — chains that will crash
    at execution time because a required upstream key has no producer. Soft
    warnings (e.g. upstream key collisions where two predecessors produce
    the same key, causing silent last-write-wins data loss) are reported
    separately by :func:`_detect_upstream_key_collisions`.
    """
    violations: list[str] = []

    if len(subtasks) <= 1:
        return violations

    for i, subtask in enumerate(subtasks):
        required = list(subtask.requires_upstream_keys)
        if not required:
            continue

        if not subtask.depends_on:
            # Root subtask with upstream requirements is inherently broken:
            # nothing runs before it, so no output_schema could satisfy the
            # requirement. Flag it explicitly rather than silently ignoring.
            cap = subtask.required_capabilities[0] if subtask.required_capabilities else "?"
            violations.append(
                f"Subtask {i} ({cap}) has no depends_on but declares "
                f"requires_upstream_keys={sorted(required)}. Root subtasks "
                f"must not require upstream keys."
            )
            continue

        available_keys = _collect_transitive_upstream_keys(subtasks, i)
        missing = set(required) - available_keys

        if missing:
            cap = subtask.required_capabilities[0] if subtask.required_capabilities else "?"
            violations.append(
                f"Subtask {i} ({cap}) requires upstream key(s) {sorted(missing)}, "
                f"but no transitive predecessor's output_schema produces them. "
                f"Available upstream keys from predecessors: {sorted(available_keys)}. "
                f"This chain is incoherent and will crash at execution time."
            )

    return violations


def _collect_transitive_key_producers(
    subtasks: list[SubTask], subtask_index: int
) -> dict[str, list[int]]:
    """Return a dict mapping each top-level output_schema key to the sorted
    list of transitive predecessor indices that produce it.

    Walks the ``depends_on`` DAG breadth-first from ``subtask_index``, the
    same way :func:`_collect_transitive_upstream_keys` does, but preserves
    producer attribution instead of collapsing to a set. A key with more
    than one producer is a collision — at runtime the MCP_TOOL executor's
    ``_upstream.update(_data)`` merge will silently drop all but the
    last-writing producer's value.

    Single-producer keys are still returned (with a one-element list) so
    callers can distinguish "no producer" (missing entry) from "exactly one
    producer" (single-element list) without an extra check.
    """
    if subtask_index < 0 or subtask_index >= len(subtasks):
        return {}

    visited: set[int] = set()
    queue: list[int] = list(subtasks[subtask_index].depends_on)
    producers: dict[str, list[int]] = {}

    while queue:
        i = queue.pop(0)
        if i in visited:
            continue
        visited.add(i)
        if i < 0 or i >= len(subtasks):
            continue
        predecessor = subtasks[i]
        for key in predecessor.output_schema.keys():
            producers.setdefault(key, []).append(i)
        queue.extend(predecessor.depends_on)

    # Sort producer lists for deterministic output (tests and log messages).
    return {k: sorted(v) for k, v in producers.items()}


def _ensure_file_writer_last_step(
    subtasks: list[SubTask],
    required_files: list[str],
) -> list[SubTask]:
    """Ensure the chain ends in a deterministic file-writer step per Rule 15.

    **Why this exists.** `docs/thesis_proposal.md` §Pillar 1 classifies
    file writing as *"deterministic + stateless → MCP tool"*. SKILL and
    AGENT executors cannot write to the Docker workspace, so a chain ending
    in a SKILL or AGENT last step cannot produce file outputs. The
    decomposer prompt Rule 15 instructs the LLM to always append a writer
    subtask when the task requires a file output; this helper is the
    safety net for decomposer non-compliance — it appends a synthetic
    writer subtask when the LLM forgot.

    **When it fires.**
    - ``required_files`` is non-empty (task declared file outputs)
    - The last subtask's description does NOT mention any of the required
      filenames (case-insensitive substring match)

    **What it appends** (one subtask per missing required file, in order):
    - ``description``: ``"Write the upstream result to <filename> as
      <format>."``
    - ``required_capabilities``: ``["write-<ext>-file"]`` — short, reusable,
      and a natural merge candidate under Pillar 3.
    - ``depends_on``: ``[<index of previous last subtask>]``.
    - ``output_schema``: ``{"output_file": "str — path of the written
      file"}``.
    - ``requires_upstream_keys``: the TOP-LEVEL keys of the predecessor's
      ``output_schema`` (satisfies the chain coherence contract from
      Rule 13 / ``_validate_chain_coherence``).

    **Why a helper instead of prompt-only.** Per §12 finding #10, relying
    on prompt compliance alone is the Option-A-as-fallback pattern that
    drifted. The validator makes the rule structurally enforced: either
    the decomposer does it (happy path — Rule 15 fires) or this helper
    does it (rescue path). Either way, the chain ends in an MCP_TOOL
    writer that goes through Pillar 2 verification and enters the
    registry as a reusable, Pillar-3-eligible capability.
    """
    if not required_files or not subtasks:
        return subtasks

    last = subtasks[-1]
    last_desc = last.description.lower()
    if any(fn.lower() in last_desc for fn in required_files):
        # Decomposer already appended a writer (Rule 15 compliance).
        return subtasks

    # Decomposer non-compliance — synthesize writer step(s).
    augmented = list(subtasks)
    predecessor_index = len(subtasks) - 1
    predecessor_keys = list(subtasks[-1].output_schema.keys())

    _EXT_TO_FORMAT = {
        ".csv": "CSV",
        ".xlsx": "Excel",
        ".xls": "Excel",
        ".json": "JSON",
        ".md": "Markdown",
        ".markdown": "Markdown",
        ".txt": "plain text",
        ".pdf": "PDF",
        ".docx": "Word",
        ".parquet": "Parquet",
    }

    for filename in required_files:
        ext = "".join(Path(filename).suffixes[-1:]).lower() or ""
        ext_slug = ext.lstrip(".") or "file"
        fmt_label = _EXT_TO_FORMAT.get(ext, ext_slug.upper())
        writer = SubTask(
            description=(
                f"Write the upstream result to {filename} as {fmt_label}, "
                "serializing the per-key upstream data into the requested "
                "file format and returning the output file path."
            ),
            required_capabilities=[f"write-{ext_slug}-file"],
            depends_on=[predecessor_index],
            output_schema={"output_file": "str — path of the written file"},
            requires_upstream_keys=predecessor_keys,
        )
        augmented.append(writer)
        # Each appended writer chains off the previous one so multi-file
        # asks (e.g. output.csv AND report.md) serialize cleanly.
        predecessor_index = len(augmented) - 1
        predecessor_keys = ["output_file"]

    return augmented


def _detect_upstream_key_collisions(subtasks: list[SubTask]) -> list[str]:
    """Return a list of soft-warning messages for upstream key collisions.

    A collision is a top-level ``output_schema`` key produced by more than
    one transitive predecessor of some downstream subtask. At runtime the
    MCP_TOOL executor merges predecessor outputs into a flat ``upstream_data``
    dict via ``_upstream.update(_data)`` in the glob-sorted order
    ``step_0_output.json``, ``step_1_output.json``, ... — last-write-wins.
    Earlier producers' values for the colliding key are silently dropped.

    **These are soft warnings, not hard violations.** A colliding chain can
    accidentally work at runtime if the downstream subtask happens to want
    the last-writing producer's value. The check is still useful because:

    - It surfaces decomposition-quality issues (two predecessors naming
      different semantic outputs the same top-level key).
    - It explains otherwise-mysterious silent data loss when two parses
      of different inputs both land under e.g. ``'records'``.
    - It's free: the infrastructure added for
      :func:`_validate_chain_coherence` (the ``requires_upstream_keys``
      structured contract) makes this detection a simple walk of the DAG.

    **Deduplication.** The same collision (same key, same set of producers)
    is reported at most once — at the FIRST downstream subtask whose
    transitive predecessor walk encounters both producers. If subtasks 2
    and 3 both have 0 and 1 as transitive predecessors and 0/1 collide on
    key ``'records'``, the collision is reported at subtask 2 only.

    See ``docs/MASTER_PLAN_2026_04_13.md`` §14.10 for the full rationale
    and the Option A vs Option B decision. Current implementation is
    Option A (warning-only); post-eval data will determine whether to
    upgrade to Option B (hard violation).
    """
    warnings_list: list[str] = []

    if len(subtasks) <= 1:
        return warnings_list

    # Deduplicate collisions across subtasks: the same (key, frozen_producers)
    # pair is reported only at the first successor that sees it.
    seen: set[tuple[str, tuple[int, ...]]] = set()

    for i, subtask in enumerate(subtasks):
        if not subtask.depends_on:
            # Root subtasks have no transitive predecessors — no collisions
            # possible.
            continue

        producers = _collect_transitive_key_producers(subtasks, i)
        collision_items: list[tuple[str, list[int]]] = [
            (key, prods) for key, prods in sorted(producers.items())
            if len(prods) > 1
        ]
        if not collision_items:
            continue

        # Filter out collisions already reported at an earlier successor.
        new_collisions: list[tuple[str, list[int]]] = []
        for key, prods in collision_items:
            fingerprint = (key, tuple(prods))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            new_collisions.append((key, prods))

        if not new_collisions:
            continue

        cap = subtask.required_capabilities[0] if subtask.required_capabilities else "?"
        collision_descriptions = [
            f"'{key}' (produced by subtasks {prods})"
            for key, prods in new_collisions
        ]
        warnings_list.append(
            f"Subtask {i} ({cap}) has transitive predecessors with colliding "
            f"output_schema keys: {', '.join(collision_descriptions)}. "
            f"At runtime the MCP_TOOL executor merges predecessor outputs via "
            f"last-write-wins (``_upstream.update``), so earlier producers' "
            f"values for these keys will be silently overwritten. Rename one "
            f"of the colliding keys in the decomposition to disambiguate."
        )

    return warnings_list


# ---------------------------------------------------------------------------
# LangGraph node
# ---------------------------------------------------------------------------


def decomposition_node(
    state: TaskState,
) -> Command[Literal["gap_detection"]]:
    """Decompose a task into subtasks using a DeepAgent.

    The agent is invoked with a 120 s timeout and a recursion limit of 50.
    On timeout or error the node returns partial results (empty subtasks)
    with the error recorded in ``state.errors``.
    """
    agent = _get_agent()

    # Inherit LangFuse tags from the pipeline config so the decomposition
    # DeepAgent trace is tagged with task_id, condition, tier, etc.
    # Must extract BEFORE entering the ThreadPoolExecutor (get_config()
    # only works in the LangGraph execution thread).
    langfuse_handler = None
    langfuse_metadata: dict = {}
    try:
        from langgraph.config import get_config as _get_langgraph_config

        parent_config = _get_langgraph_config()
        parent_meta = (parent_config or {}).get("metadata", {})
        langfuse_metadata = {
            k: v for k, v in parent_meta.items()
            if k.startswith("langfuse_") or k in ("task_id", "condition", "run_id", "tier")
        }
        logger.debug("Inherited LangFuse metadata: %s", langfuse_metadata)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not inherit LangFuse metadata: %s", exc)

    try:
        langfuse_handler = LangFuseCallbackHandler()
        callbacks = [langfuse_handler]
    except Exception as exc:  # noqa: BLE001
        logger.debug("LangFuse handler creation failed (tracing disabled): %s", exc)
        callbacks = []

    config: dict = {
        "recursion_limit": DECOMPOSITION_RECURSION_LIMIT,
        "callbacks": callbacks,
    }
    if langfuse_metadata:
        config["metadata"] = langfuse_metadata

    subtasks: list[SubTask] = []
    required_caps: list[str] = []
    errors: list[str] = list(state.errors)

    # Use manual executor management so we can call shutdown(wait=False) on
    # timeout — the context-manager form calls shutdown(wait=True) from __exit__,
    # which blocks until the thread finishes even after TimeoutError is raised.
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(
            agent.invoke,
            {"messages": [{"role": "user", "content": state.task_description}]},
            config,
        )
        result = future.result(timeout=DECOMPOSITION_TIMEOUT_S)
        pool.shutdown(wait=False)

        structured: DecompositionResult | None = result.get("structured_response")

        if structured is not None:
            for item in structured.subtasks:
                subtasks.append(
                    SubTask(
                        description=item.description,
                        required_capabilities=item.required_capabilities,
                        depends_on=item.depends_on,
                        output_schema=item.output_schema,
                        requires_upstream_keys=item.requires_upstream_keys,
                    )
                )
                required_caps.extend(item.required_capabilities)
            if not subtasks:
                errors.append(
                    "Decomposition agent returned 0 subtasks; treating whole task as one capability"
                )
                logger.warning("DecompositionAgent: structured response contained 0 subtasks")
                emit_langfuse_event(
                    langfuse_handler,
                    "decomposition.empty_subtasks",
                    "WARNING",
                    {"task_description": state.task_description[:200]},
                )
        else:
            errors.append("Decomposition agent returned no structured response")
            logger.warning("DecompositionAgent: no structured_response in result")
            emit_langfuse_event(
                langfuse_handler,
                "decomposition.no_structured_response",
                "WARNING",
                {"task_description": state.task_description[:200]},
            )

    except concurrent.futures.TimeoutError:
        pool.shutdown(wait=False)
        errors.append(f"Decomposition agent timed out after {DECOMPOSITION_TIMEOUT_S}s")
        logger.error("DecompositionAgent: timed out after %ds", DECOMPOSITION_TIMEOUT_S)
        emit_langfuse_event(
            langfuse_handler,
            "decomposition.timeout",
            "ERROR",
            {"timeout_s": DECOMPOSITION_TIMEOUT_S},
        )
    except Exception as exc:  # noqa: BLE001
        pool.shutdown(wait=False)
        errors.append(f"Decomposition agent error: {exc}")
        logger.exception("DecompositionAgent: unexpected error")
        emit_langfuse_event(
            langfuse_handler,
            "decomposition.error",
            "ERROR",
            {"error": str(exc)},
        )

    # Deduplicate required_caps while preserving insertion order.
    # Multiple subtasks may reference the same capability; searching/classifying
    # duplicates wastes tokens and can produce duplicate artifacts.
    required_caps = list(dict.fromkeys(required_caps))

    # Two-stage circuit breaker: warn at WARN_CAPS, hard truncate at MAX_CAPS_HARD.
    if len(required_caps) > WARN_CAPS:
        logger.warning(
            "Decomposition produced %d required capabilities (warn threshold %d). "
            "Prompt constraints may not have been followed.",
            len(required_caps), WARN_CAPS,
        )
        emit_langfuse_event(
            langfuse_handler,
            "decomposition.over_warn_limit",
            "WARNING",
            {"cap_count": len(required_caps), "warn_limit": WARN_CAPS, "caps": required_caps},
        )

    if len(required_caps) > MAX_CAPS_HARD:
        logger.error(
            "Decomposition produced %d capabilities — truncating to %d (hard limit). "
            "This is a pathological decomposition; the prompt may not be working.",
            len(required_caps), MAX_CAPS_HARD,
        )
        emit_langfuse_event(
            langfuse_handler,
            "decomposition.hard_truncation",
            "CRITICAL",
            {
                "original_count": len(required_caps),
                "truncated_to": MAX_CAPS_HARD,
                "dropped": required_caps[MAX_CAPS_HARD:],
            },
        )
        required_caps = required_caps[:MAX_CAPS_HARD]

    # Fallback: if no subtasks were produced, use the first sentence of the task
    # description as a capability query (the full description is too long for
    # effective semantic search in the registry).
    #
    # Sticky-failure flag: if we reach this branch, decomposition effectively
    # failed — the pipeline will continue running through the fallback path so
    # the user sees whatever partial output is achievable, but the terminal CLI
    # display and the evaluation harness's success classifier MUST classify the
    # run as failed. We carry this as a dedicated boolean on TaskState because
    # `status` is transitioned forward by every downstream node and cannot
    # carry a sticky signal. See docs/perf_fixes_2026_04_14.md §2b.
    decomposition_succeeded = bool(subtasks)
    if not subtasks:
        first_sentence = state.task_description.split(".")[0].strip()
        required_caps = [first_sentence[:200] if first_sentence else state.task_description[:200]]

    # Chain coherence validation (Phase 2 Step 14). Only meaningful when we
    # have a real multi-subtask decomposition to validate. Walks the DAG and
    # flags subtasks that reference ``upstream_data`` keys no predecessor
    # produces. On violation: extend errors, clear the sticky success flag,
    # and emit a LangFuse event — but KEEP the subtasks so the pipeline can
    # still run through classification + generation and surface whatever
    # partial output is achievable. This matches the Fix 2b fail-open design:
    # decomposition failures are visible, not silent, but they don't abort
    # the pipeline mid-flight.
    if decomposition_succeeded:
        # Phase 3 Step 12 / §12 finding #10: if the task's OutputContract
        # declares required_files and the decomposer's last subtask doesn't
        # mention any of them, append a deterministic writer subtask. The
        # appended writer becomes a regular gap — classified as MCP_TOOL
        # (file writing is deterministic), generated by the MCP_TOOL
        # generator, verified by the three-tier gate, and registered for
        # reuse. This keeps file output inside the proposal's Pillar 1 + 2
        # + 3 story instead of short-circuiting it with executor-level
        # synthesis. See master plan §12 finding #10 for the pivot
        # rationale (Option A → Option B).
        if state.output_contract is not None and state.output_contract.required_files:
            augmented = _ensure_file_writer_last_step(
                subtasks, state.output_contract.required_files,
            )
            if len(augmented) != len(subtasks):
                added = len(augmented) - len(subtasks)
                logger.info(
                    "Decomposition: appended %d writer subtask(s) to satisfy "
                    "required_files=%s (decomposer did not append one per Rule 15)",
                    added,
                    state.output_contract.required_files,
                )
                emit_langfuse_event(
                    langfuse_handler,
                    "decomposition.writer_appended",
                    "INFO",
                    {
                        "added_subtasks": added,
                        "required_files": state.output_contract.required_files,
                    },
                )
                for writer in augmented[len(subtasks):]:
                    required_caps.extend(writer.required_capabilities)
                subtasks = augmented
                required_caps = list(dict.fromkeys(required_caps))

        coherence_violations = _validate_chain_coherence(subtasks)
        if coherence_violations:
            decomposition_succeeded = False
            for violation in coherence_violations:
                errors.append(f"Chain coherence violation: {violation}")
                logger.warning("DecompositionAgent: %s", violation)
            emit_langfuse_event(
                langfuse_handler,
                "decomposition.incoherent_chain",
                "ERROR",
                {
                    "violation_count": len(coherence_violations),
                    "violations": coherence_violations,
                    "subtask_count": len(subtasks),
                },
            )

        # Soft warnings — upstream key collisions that won't necessarily
        # break the chain but indicate silent last-write-wins data loss at
        # runtime. Reported as warnings in the errors list and logged, but
        # deliberately do NOT trip ``decomposition_succeeded=False`` (Option
        # A per master plan §14.10). If post-eval data shows colliding
        # chains actually firing in production, upgrade to Option B (hard
        # violation).
        collision_warnings = _detect_upstream_key_collisions(subtasks)
        if collision_warnings:
            for warning in collision_warnings:
                errors.append(f"Chain coherence warning: {warning}")
                logger.warning("DecompositionAgent: %s", warning)
            emit_langfuse_event(
                langfuse_handler,
                "decomposition.upstream_key_collision",
                "WARNING",
                {
                    "warning_count": len(collision_warnings),
                    "warnings": collision_warnings,
                    "subtask_count": len(subtasks),
                },
            )

    return Command(
        update={
            "subtasks": subtasks,
            "required_capabilities": required_caps,
            "status": TaskStatus.DECOMPOSING,
            "errors": errors,
            "decomposition_succeeded": decomposition_succeeded,
        },
        goto="gap_detection",
    )
