"""Task decomposition via DeepAgent.

Wraps a DeepAgent (``create_deep_agent``) in a LangGraph node function.
The agent uses ``write_todos`` for planning and ``response_format`` for
structured subtask output.

Operational boundaries:
- ``recursion_limit=50`` (max steps)
- Node-level timeout: 240 s
- Graph is a stateless CompiledStateGraph; state is fresh per ``.invoke()`` call
- On repeated timeout/error/empty output: fail closed with ``decomposition_succeeded=False``
"""

from __future__ import annotations

import concurrent.futures
import graphlib
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

# Complex decompositions can take several minutes of LLM wall time, so the
# timeout is intentionally higher than the rest of the synchronous nodes.
DECOMPOSITION_TIMEOUT_S = 240
DECOMPOSITION_RECURSION_LIMIT = 50
# Retry timeout / exception / empty result before failing closed.
MAX_DECOMPOSITION_ATTEMPTS = 3

# Two-stage circuit breaker for capability count.
# WARN: emit visibility signal but proceed intact — genuine 6-7 step tasks survive.
# TRUNCATE: pathological 14-step decompositions are caught before they explode
#           generation and verification time.
WARN_CAPS = 5
MAX_CAPS_HARD = 8


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def _get_agent():
    """Create a fresh decomposition DeepAgent for each invocation.

    A new ``CompiledStateGraph`` is returned on every call so no state is
    carried between pipeline runs. ``create_deep_agent()`` compiles a
    LangGraph graph without making API calls, so the overhead is minimal.
    """
    from src.config import DEFAULT_LLM_MODEL

    return create_deep_agent(
        model=DEFAULT_LLM_MODEL,
        system_prompt=DECOMPOSITION_SYSTEM_PROMPT,
        response_format=DecompositionResult,
        name="task-decomposer",
    )


# ---------------------------------------------------------------------------
# Chain coherence validation
# ---------------------------------------------------------------------------
#
# The decomposer can produce a DAG of subtasks that is internally incoherent:
# a downstream subtask claims to consume an ``upstream_data`` key that no
# transitive predecessor produces in its ``output_schema``. Each subtask
# passes verification in isolation, but the chain crashes at execution time
# with errors like ``upstream_data must include a 'transactions' key``.
#
# Implementation: **structured contract**, not prose parsing. The subtask
# schema carries an explicit ``requires_upstream_keys: list[str]`` field.
# The decomposer fills it in; validation is a set comparison against the
# union of transitive predecessors' ``output_schema`` keys. No regex, no
# natural-language heuristics.
#
# Aligned with the thesis proposal's "Structured State Communication" section:
# typed contracts over lossy natural-language parsing.
#
# Fail-open semantics: validation failures append to ``state.errors`` and
# set ``decomposition_succeeded=False``, but the pipeline continues so
# downstream nodes can produce partial results.


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
            # Keep this helper defensive; structural DAG validation reports
            # invalid indices separately.
            continue
        predecessor = subtasks[i]
        upstream_keys.update(predecessor.output_schema.keys())
        queue.extend(predecessor.depends_on)

    return upstream_keys


def _validate_depends_on_indices(subtasks: list[SubTask]) -> list[str]:
    """Return a list of structural DAG violations in ``subtasks``.

    Checks:
    - Every ``depends_on`` index ``dep`` must satisfy ``0 <= dep < len(subtasks)``.
    - No subtask may depend on itself.
    - The DAG must be acyclic (``graphlib.CycleError`` → violation).

    Returns an empty list on clean DAGs.
    """
    violations: list[str] = []
    n = len(subtasks)

    for i, st in enumerate(subtasks):
        for dep in st.depends_on:
            if not isinstance(dep, int) or dep < 0 or dep >= n:
                violations.append(
                    f"Subtask {i} has out-of-range depends_on index "
                    f"{dep!r} (valid range: 0..{n - 1})."
                )
            if dep == i:
                violations.append(f"Subtask {i} depends on itself (self-loop).")

    if violations:
        # Cycle check is ill-defined when indices are already invalid.
        return violations

    try:
        graph = {i: set(st.depends_on) for i, st in enumerate(subtasks)}
        list(graphlib.TopologicalSorter(graph).static_order())
    except graphlib.CycleError as exc:
        violations.append(
            f"Subtask dependency cycle detected: {exc.args}. "
            "Chain cannot execute in any topological order."
        )

    return violations


def _validate_single_capability_per_subtask(subtasks: list[SubTask]) -> list[str]:
    """Return warnings for subtasks that declare more than one capability.

    The executor resolves one capability per subtask. Multi-capability
    subtasks are therefore risky, but they are currently treated as soft
    warnings because multi-file writer cases can legitimately produce more
    than one writer capability.
    """
    violations: list[str] = []
    for i, st in enumerate(subtasks):
        if len(st.required_capabilities) > 1:
            violations.append(
                f"Subtask {i} declares {len(st.required_capabilities)} "
                f"required_capabilities ({st.required_capabilities!r}); "
                f"decomposer contract expects exactly one per subtask "
                f"— multi-cap subtasks would silently drop caps 2..N "
                f"at execution time."
            )
    return violations


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


def _validate_output_contract_coverage(
    subtasks: list[SubTask],
    required_keys: list[str],
) -> list[str]:
    """Return violations when the decomposition cannot satisfy required_keys.

    The task-level OutputContract defines the minimum top-level keys that must
    appear in the final result dict. A valid decomposition may produce those
    keys across multiple subtasks, but the union of all declared
    ``output_schema`` keys must still cover the contract. If it does not, the
    chain cannot satisfy the task regardless of downstream generation quality.
    """
    if not subtasks or not required_keys:
        return []

    produced_keys: set[str] = set()
    for subtask in subtasks:
        produced_keys.update(subtask.output_schema.keys())

    missing = sorted(set(required_keys) - produced_keys)
    if not missing:
        return []

    return [
        "Decomposition does not cover output_contract.required_keys. "
        f"Missing keys: {missing}. Produced keys across all subtasks: "
        f"{sorted(produced_keys)}."
    ]


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

    File writing is deterministic and must run as an MCP tool because SKILL
    and AGENT executors cannot write to the Docker workspace that the chain
    collector scans. The decomposer prompt instructs the LLM to append a
    writer subtask when the task requires a file output; this helper enforces
    that rule structurally when the LLM forgets.

    **When it fires.**
    - ``required_files`` is non-empty (task declared file outputs)
    - The last subtask's description does NOT mention any of the required
      filenames (case-insensitive substring match)

    **What it appends** (one subtask per missing required file):
    - ``description``: ``"Write the upstream result to <filename> as
      <format>."``
    - ``required_capabilities``: ``["write-<ext>-file"]`` — short, reusable,
      and a natural registry reuse candidate.
    - ``depends_on``: ``[<index of the original last subtask>]`` — every
      writer depends on the SAME original predecessor in parallel
      (NOT chained writer-after-writer). For multi-file asks each writer
      reads the same upstream data and serializes its own format
      independently. CompositionalExecutor's topological sort handles
      parallel branches fine.
    - ``output_schema``: ``{"output_file": "str — path of the written
      file"}``.
    - ``requires_upstream_keys``: the TOP-LEVEL keys of the original
      predecessor's ``output_schema`` (satisfies the chain coherence
      contract enforced by ``_validate_chain_coherence``).

    **Why parallel and not chained.** Multiple file writers need the same
    original upstream data. Chaining writers would make writer N+1 consume
    only writer N's ``output_file`` path instead of the original result.
    """
    if not required_files or not subtasks:
        return subtasks

    last = subtasks[-1]
    last_desc = last.description.lower()
    if any(fn.lower() in last_desc for fn in required_files):
        # Decomposer already appended a writer (Rule 15 compliance).
        return subtasks

    # Decomposer non-compliance — synthesize writer step(s).
    # All writers depend on the ORIGINAL last subtask in parallel — see
    # docstring for why chained writers were wrong for multi-file.
    augmented = list(subtasks)
    original_predecessor_index = len(subtasks) - 1
    original_predecessor_keys = list(subtasks[-1].output_schema.keys())

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
        # Include the /workspace prefix so the MCP tool generator writes
        # inside the sandbox mount that the compositional executor collects.
        writer = SubTask(
            description=(
                f"Write the upstream result to /workspace/{filename} as "
                f"{fmt_label}, serializing the per-key upstream data into "
                "the requested file format and returning the output file "
                f"path. The file MUST be written to /workspace/{filename} "
                "(NOT /{filename} or any other absolute path) so the "
                "compositional executor can collect it from the sandbox "
                "and expose it through ChainResult.output_files."
            ),
            required_capabilities=[f"write-{ext_slug}-file"],
            depends_on=[original_predecessor_index],
            output_schema={"output_file": "str — path of the written file"},
            requires_upstream_keys=original_predecessor_keys,
        )
        augmented.append(writer)

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
    still work at runtime if the downstream subtask happens to want the
    last-writing producer's value. The check is still useful because:

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

    The current behavior is warning-only so legitimate chains are not blocked
    by a collision that may be harmless for the downstream consumer.
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

    The agent is invoked with the module-level timeout and recursion limit.
    Retryable failures are retried; exhaustion leaves subtasks empty, records
    errors in state, and flips ``decomposition_succeeded`` to ``False``.
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

    # Retry timeout / exception / empty structured response before giving up.
    # Each attempt uses a fresh ThreadPoolExecutor and the full timeout budget.
    # The pool is manually managed so timeout can call shutdown(wait=False);
    # the context-manager form would wait for the worker thread to finish.
    # submit_with_context propagates tracing ContextVars into the worker thread.
    from src.orchestrator.tracing import submit_with_context
    for attempt in range(1, MAX_DECOMPOSITION_ATTEMPTS + 1):
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = submit_with_context(
                pool,
                agent.invoke,
                {"messages": [{"role": "user", "content": state.task_description}]},
                config,
            )
            result = future.result(timeout=DECOMPOSITION_TIMEOUT_S)
            pool.shutdown(wait=False)

            structured: DecompositionResult | None = result.get("structured_response")

            if structured is not None and structured.subtasks:
                # Stage the current attempt in locals so failed DAG validation
                # can trigger a retry without contaminating accumulated state.
                attempt_subtasks: list[SubTask] = []
                attempt_caps: list[str] = []
                for item in structured.subtasks:
                    attempt_subtasks.append(
                        SubTask(
                            description=item.description,
                            required_capabilities=item.required_capabilities,
                            depends_on=item.depends_on,
                            output_schema=item.output_schema,
                            requires_upstream_keys=item.requires_upstream_keys,
                        )
                    )
                    attempt_caps.extend(item.required_capabilities)

                # DAG validation stays inside the retry loop so malformed
                # outputs get the full retry budget.
                attempt_dag_violations = _validate_depends_on_indices(
                    attempt_subtasks
                )
                if attempt_dag_violations and attempt < MAX_DECOMPOSITION_ATTEMPTS:
                    logger.warning(
                        "DecompositionAgent attempt %d/%d: DAG violations "
                        "%s — discarding attempt and retrying.",
                        attempt, MAX_DECOMPOSITION_ATTEMPTS,
                        attempt_dag_violations,
                    )
                    errors.append(
                        f"Decomposition attempt {attempt}/"
                        f"{MAX_DECOMPOSITION_ATTEMPTS}: invalid DAG "
                        f"({len(attempt_dag_violations)} violations)"
                    )
                    emit_langfuse_event(
                        langfuse_handler,
                        "decomposition.invalid_dag_retry",
                        "WARNING",
                        {
                            "attempt": attempt,
                            "violation_count": len(attempt_dag_violations),
                            "violations": attempt_dag_violations,
                        },
                    )
                    # Discard attempt — leave subtasks/required_caps empty
                    # so the next iteration re-attempts cleanly.
                    continue

                # Accept the attempt (DAG either valid, or this is the
                # final attempt and the post-loop validator will surface
                # the violation via the sticky flag).
                subtasks.extend(attempt_subtasks)
                required_caps.extend(attempt_caps)
                # Success — break out of retry loop.
                break

            # Successful invocation but no structured_response or empty
            # subtasks — treat as retryable.
            if structured is None:
                logger.warning(
                    "DecompositionAgent attempt %d/%d: no structured_response",
                    attempt, MAX_DECOMPOSITION_ATTEMPTS,
                )
                errors.append(
                    f"Decomposition attempt {attempt}/{MAX_DECOMPOSITION_ATTEMPTS}: "
                    "no structured response"
                )
                emit_langfuse_event(
                    langfuse_handler,
                    "decomposition.no_structured_response",
                    "WARNING",
                    {
                        "attempt": attempt,
                        "task_description": state.task_description[:200],
                    },
                )
            else:
                logger.warning(
                    "DecompositionAgent attempt %d/%d: 0 subtasks",
                    attempt, MAX_DECOMPOSITION_ATTEMPTS,
                )
                errors.append(
                    f"Decomposition attempt {attempt}/{MAX_DECOMPOSITION_ATTEMPTS}: "
                    "empty subtask list"
                )
                emit_langfuse_event(
                    langfuse_handler,
                    "decomposition.empty_subtasks",
                    "WARNING",
                    {
                        "attempt": attempt,
                        "task_description": state.task_description[:200],
                    },
                )

        except concurrent.futures.TimeoutError:
            pool.shutdown(wait=False)
            errors.append(
                f"Decomposition attempt {attempt}/{MAX_DECOMPOSITION_ATTEMPTS}: "
                f"timed out after {DECOMPOSITION_TIMEOUT_S}s"
            )
            logger.error(
                "DecompositionAgent attempt %d/%d: timed out after %ds",
                attempt, MAX_DECOMPOSITION_ATTEMPTS, DECOMPOSITION_TIMEOUT_S,
            )
            emit_langfuse_event(
                langfuse_handler,
                "decomposition.timeout",
                "ERROR",
                {"attempt": attempt, "timeout_s": DECOMPOSITION_TIMEOUT_S},
            )
        except Exception as exc:  # noqa: BLE001
            pool.shutdown(wait=False)
            errors.append(
                f"Decomposition attempt {attempt}/{MAX_DECOMPOSITION_ATTEMPTS}: "
                f"error: {exc}"
            )
            logger.exception(
                "DecompositionAgent attempt %d/%d: unexpected error",
                attempt, MAX_DECOMPOSITION_ATTEMPTS,
            )
            emit_langfuse_event(
                langfuse_handler,
                "decomposition.error",
                "ERROR",
                {"attempt": attempt, "error": str(exc)},
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
        # Prune required_caps and subtasks consistently. Any surviving
        # depends_on indices are remapped so the post-truncation DAG remains
        # structurally valid.
        dropped_caps = set(required_caps[MAX_CAPS_HARD:])
        original_cap_count = len(required_caps)
        required_caps = required_caps[:MAX_CAPS_HARD]

        kept_subtasks: list[SubTask] = []
        index_remap: dict[int, int] = {}
        for old_idx, st in enumerate(subtasks):
            references_dropped = any(
                cap in dropped_caps for cap in st.required_capabilities
            )
            if references_dropped:
                continue
            index_remap[old_idx] = len(kept_subtasks)
            kept_subtasks.append(st)

        # Remap depends_on for surviving subtasks: drop indices that
        # pointed at removed subtasks, rewrite the rest to their new
        # positions. Order-preserving so the topological structure of
        # the surviving DAG is unchanged.
        remapped_subtasks: list[SubTask] = []
        for st in kept_subtasks:
            new_deps = [
                index_remap[d]
                for d in st.depends_on
                if d in index_remap
            ]
            remapped_subtasks.append(st.model_copy(update={"depends_on": new_deps}))

        dropped_subtask_count = len(subtasks) - len(remapped_subtasks)
        subtasks = remapped_subtasks

        logger.error(
            "Decomposition produced %d capabilities — truncating to %d "
            "(hard limit) and dropping %d subtask(s) referencing the "
            "%d truncated cap(s). This is a pathological decomposition; "
            "the prompt may not be working.",
            original_cap_count, MAX_CAPS_HARD,
            dropped_subtask_count, len(dropped_caps),
        )
        emit_langfuse_event(
            langfuse_handler,
            "decomposition.hard_truncation",
            "CRITICAL",
            {
                "original_count": original_cap_count,
                "truncated_to": MAX_CAPS_HARD,
                "dropped": sorted(dropped_caps),
                "dropped_subtask_count": dropped_subtask_count,
                "surviving_subtask_count": len(subtasks),
            },
        )

    # If no subtasks were produced after retries, fail closed instead of
    # synthesizing a fallback single-gap decomposition.
    decomposition_succeeded = bool(subtasks)

    # Chain coherence validation is meaningful only when we have a real
    # decomposition. Violations append errors, clear the sticky success flag,
    # and emit LangFuse events, but the subtasks are kept so downstream nodes
    # can still surface partial output.
    if decomposition_succeeded:
        # If the task declares required_files and the decomposer's last
        # subtask does not mention them, append deterministic writer subtasks.
        # Each appended writer becomes a normal MCP_TOOL gap.
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

        # Structural DAG integrity is checked before semantic coherence.
        # Violations flip the sticky flag and emit a LangFuse event.
        dag_violations = _validate_depends_on_indices(subtasks)
        if dag_violations:
            decomposition_succeeded = False
            for violation in dag_violations:
                errors.append(f"Invalid DAG: {violation}")
                logger.warning("DecompositionAgent: %s", violation)
            emit_langfuse_event(
                langfuse_handler,
                "decomposition.invalid_dag",
                "ERROR",
                {
                    "violation_count": len(dag_violations),
                    "violations": dag_violations,
                    "subtask_count": len(subtasks),
                },
            )

        # Multi-capability subtasks are soft warnings. The executor resolves
        # one capability per subtask, but parallel multi-file writer cases can
        # legitimately carry multiple writer capabilities.
        multi_cap_violations = _validate_single_capability_per_subtask(subtasks)
        if multi_cap_violations:
            for violation in multi_cap_violations:
                errors.append(f"Multi-cap subtask warning: {violation}")
                logger.warning("DecompositionAgent: %s", violation)
            emit_langfuse_event(
                langfuse_handler,
                "decomposition.multi_cap_subtask",
                "WARNING",
                {
                    "violation_count": len(multi_cap_violations),
                    "violations": multi_cap_violations,
                    "subtask_count": len(subtasks),
                },
            )

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

        if state.output_contract is not None and state.output_contract.required_keys:
            coverage_violations = _validate_output_contract_coverage(
                subtasks, state.output_contract.required_keys,
            )
            if coverage_violations:
                decomposition_succeeded = False
                for violation in coverage_violations:
                    errors.append(f"Output contract coverage violation: {violation}")
                    logger.warning("DecompositionAgent: %s", violation)
                emit_langfuse_event(
                    langfuse_handler,
                    "decomposition.output_contract_uncovered",
                    "ERROR",
                    {
                        "violation_count": len(coverage_violations),
                        "violations": coverage_violations,
                        "required_keys": state.output_contract.required_keys,
                        "subtask_count": len(subtasks),
                    },
                )

        # Upstream key collisions are soft warnings: they indicate possible
        # last-write-wins data loss but may be harmless for the consumer.
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
