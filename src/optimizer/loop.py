"""LangGraph optimization loop for the granularity optimizer.

Nodes: compute_metrics -> identify_candidates -> apply_operations -> END
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from langgraph.graph import END, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field
from scipy.stats import entropy

from src.config import AUTO_OPTIMIZER_THRESHOLD
from src.optimizer.operations import merge, prune, split
from src.optimizer.utility import compute_all_utilities
from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry
from src.schemas.metrics import GranularityMetrics
from src.schemas.optimizer_config import OptimizerConfig

logger = logging.getLogger(__name__)


class OptimizerState(BaseModel):
    """State for the optimization loop."""

    capabilities_snapshot: list[str] = Field(
        default_factory=list, description="Current active capability IDs"
    )
    utility_scores: dict[str, float] = Field(
        default_factory=dict, description="Utility scores per capability"
    )
    split_candidates: list[str] = Field(
        default_factory=list, description="Capabilities to split"
    )
    merge_candidates: list[tuple[str, str]] = Field(
        default_factory=list, description="Pairs of capabilities to merge"
    )
    pruned: list[str] = Field(
        default_factory=list, description="Actually pruned capability IDs"
    )
    split_results: dict[str, list[str]] = Field(
        default_factory=dict, description="Mapping of split parent -> new variants"
    )
    merge_results: dict[str, str] = Field(
        default_factory=dict, description="Mapping of 'a+b' -> merged ID"
    )
    kl_divergence: float | None = Field(
        default=None, description="KL divergence between previous and current utility distributions"
    )
    previous_utility_scores: dict[str, float] = Field(
        default_factory=dict, description="Utility scores from previous cycle"
    )
    errors: list[str] = Field(
        default_factory=list, description="Errors encountered during optimization"
    )
    allow_network: bool = Field(
        default=False,
        description=(
            "Network policy inherited from the enclosing TaskState (Step "
            "14.12). When True, merge/split variant verification runs with "
            "``--network bridge`` in Tier 2 / Tier 3, and MCP_TOOL coverage "
            "replay honors the same policy. Offline-by-default preserves "
            "the standard safety posture."
        ),
    )

    model_config = {"extra": "forbid"}


def _compute_kl_divergence(
    prev_scores: dict[str, float], curr_scores: dict[str, float]
) -> float:
    """Compute KL(curr || prev) between two capability utility score distributions.

    Each capability's raw utility score is treated as a point mass; both
    distributions are shifted to be non-negative, epsilon-smoothed, and
    normalised to sum to 1 before KL is applied.
    Returns 0.0 if either distribution is empty or uniform-zero.

    NOTE — this is the *online* convergence metric stored in
    ``OptimizerState.kl_divergence`` and propagated across pipeline
    invocations via ``_prev_optimizer_scores``.  A separate histogram-based
    KL implementation exists in ``simulation.py`` for visualisation; the two
    measure different aspects of convergence (see ``_kl_divergence`` there).
    """
    if not prev_scores or not curr_scores:
        # Empty distribution signals catastrophic failure (e.g. all pruned),
        # NOT convergence.  Return inf to prevent false convergence.
        return float("inf") if (prev_scores and not curr_scores) else 0.0

    # Use union of keys so distributions are comparable
    all_keys = sorted(set(prev_scores) | set(curr_scores))
    prev = np.array([prev_scores.get(k, 0.0) for k in all_keys], dtype=np.float64)
    curr = np.array([curr_scores.get(k, 0.0) for k in all_keys], dtype=np.float64)

    # Shift to non-negative
    min_val = min(prev.min(), curr.min())
    if min_val < 0:
        prev = prev - min_val + 1e-10
        curr = curr - min_val + 1e-10

    # Add small epsilon to avoid zeros
    prev = prev + 1e-10
    curr = curr + 1e-10

    # Normalize to probability distributions
    prev = prev / prev.sum()
    curr = curr / curr.sum()

    # KL(curr || prev): measures surprise of new data given old expectations
    return float(entropy(curr, prev))


def _schema_jaccard(
    cap_a: "CapabilityDescriptor", cap_b: "CapabilityDescriptor"
) -> float:
    """Jaccard similarity over the union of input+output schema key names.

    Implements the proposal's second Merge conjunct: *"overlapping input
    schemas"*. Computes |A ∩ B| / |A ∪ B| where A and B are the sets of
    parameter names + output-key names from each capability's schemas.

    Returns 0.0 if either side has no schema (which disables merge on
    unpopulated capabilities — safe default, because merging two caps
    with unknown contracts risks producing garbage).

    Uses key-name overlap (not type overlap) because type tokens like
    ``int``/``dict`` carry little semantic signal for clustering decisions;
    the stronger signal is that two capabilities share parameter *names*
    like ``file_path`` or ``row_count``.
    """
    a_keys = set((cap_a.input_schema or {}).keys()) | set(
        (cap_a.output_schema or {}).keys()
    )
    b_keys = set((cap_b.input_schema or {}).keys()) | set(
        (cap_b.output_schema or {}).keys()
    )
    if not a_keys or not b_keys:
        return 0.0
    intersection = a_keys & b_keys
    union = a_keys | b_keys
    return len(intersection) / len(union)


def create_optimization_loop(
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    prune_window_days: int = 30,
    split_reuse_threshold: float = 0.6,
    split_success_threshold: float = 0.7,
    merge_reuse_threshold: float = 0.05,
    llm_model: str = "",
    optimizer_config: OptimizerConfig | None = None,
) -> Any:
    """Build the LangGraph optimization loop.

    Args:
        optimizer_config: Per-operation enable/disable config (§12 finding
            #8). When ``None``, defaults to all-``"on"`` — preserves the
            pre-finding-#8 semantic where the loop unconditionally ran
            all three operations whenever invoked. Threshold gating
            belongs to the caller (``create_pipeline`` decides whether to
            invoke the loop at all based on invocation_count vs
            ``AUTO_OPTIMIZER_THRESHOLD``); once invoked, each operation
            honors its per-config setting.

    Returns a compiled StateGraph.
    """
    # Default to all-"on" when caller doesn't specify — preserves the
    # pre-finding-#8 loop semantic (operations always fire when invoked).
    # The threshold gate is enforced upstream by create_pipeline / callers,
    # not by this loop.
    _config = optimizer_config or OptimizerConfig.all_on()

    def compute_metrics_node(state: OptimizerState) -> Command:
        """Compute utility scores for all active capabilities."""
        try:
            scores = compute_all_utilities(registry, metrics_store)
            caps = [cap.name for cap in registry.list_all(is_active=True)]
        except Exception as e:  # noqa: BLE001
            return Command(
                update={"errors": [*state.errors, f"compute_metrics failed: {e}"]},
                goto="identify_candidates",
            )

        # Compute KL divergence against previous cycle
        kl = _compute_kl_divergence(state.previous_utility_scores, scores)

        # Step 13.7 Batch A4 — emit KL as a LangFuse score attached to
        # the active trace so Chapter 5.4 convergence analysis is
        # queryable via LangFuse dashboards (not just DuckDB
        # cycle_utility_scores). Never raises.
        try:
            from langfuse import get_client
            try:
                lf = get_client()
                lf.score_current_trace(
                    name="m6.kl_divergence",
                    value=float(kl),
                    data_type="NUMERIC",
                    comment=f"Optimizer cycle: {len(caps)} active capabilities",
                )
            except AttributeError:
                # Older SDK — fall back to lf.score on current trace
                lf.create_score(
                    name="m6.kl_divergence",
                    value=float(kl),
                    data_type="NUMERIC",
                    comment=f"Optimizer cycle: {len(caps)} active capabilities",
                )
        except Exception:  # noqa: BLE001
            # Never-raises contract — if LangFuse is unavailable, skip.
            pass

        return Command(
            update={
                "capabilities_snapshot": caps,
                "utility_scores": scores,
                "kl_divergence": kl,
            },
            goto="identify_candidates",
        )

    def identify_candidates_node(state: OptimizerState) -> Command:
        """Identify candidates for prune, split, and merge."""
        split_cands: list[str] = []
        merge_cands: list[tuple[str, str]] = []

        # Cache metrics to avoid redundant DuckDB queries for merge candidates
        metrics_cache: dict[str, GranularityMetrics] = {}

        for cap_id in state.capabilities_snapshot:
            try:
                m = metrics_store.compute_granularity_metrics(cap_id)
                metrics_cache[cap_id] = m
                cap = registry.get(cap_id)
                if cap is None:
                    continue

                # Split candidates: high reuse + mediocre success. All three
                # capability types eligible — per-type dispatch in operations.py
                # split() + verify_coverage (MCP_TOOL) / three-tier gate per
                # variant (SKILL/AGENT) enforce correctness downstream.
                if (
                    m.reuse_rate > split_reuse_threshold
                    and m.success_rate < split_success_threshold
                ):
                    split_cands.append(cap_id)

            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to compute metrics for '%s': %s", cap_id, e)

        # Build merge pairs: low-reuse capability pairs with high embedding
        # similarity AND overlapping I/O schemas (the second proposal conjunct).
        # Use registry.search() to find semantically similar partners rather
        # than pairing consecutive capabilities (which would be arbitrary).
        #
        # Type scoping: candidates are collected across all three capability
        # types (MCP_TOOL, SKILL, AGENT). Pairing within the search loop
        # enforces same-type pairs — cross-type merge is not supported
        # because merge() dispatches on type and the combined executable
        # content only makes sense within a type (Ch 7 future work).
        low_reuse: list[str] = []
        for cap_id in state.capabilities_snapshot:
            cap = registry.get(cap_id)
            if cap is None:
                continue
            m = metrics_cache.get(cap_id)
            if m is None:
                continue
            if m.reuse_rate < merge_reuse_threshold:
                low_reuse.append(cap_id)

        low_reuse_set = set(low_reuse)
        # Compute registry size once — registry.count hits ChromaDB on every call.
        total_caps = registry.count or 1
        n_search = min(max(10, len(low_reuse)), total_caps)
        paired: set[str] = set()
        for cap_id in low_reuse:
            if cap_id in paired:
                continue
            cap = registry.get(cap_id)
            if cap is None:
                continue
            try:
                # Search for similar active capabilities in the registry.
                # Ask for enough results to find a partner within the low-reuse set.
                similar = registry.search(
                    cap.description, n_results=n_search
                )
                for similar_cap, score in similar:
                    partner_id = similar_cap.name
                    # First conjunct (proposal): description cosine >= 0.5
                    # Second conjunct (proposal + user safety rule):
                    #     schema Jaccard in [0.5, 1.0)
                    # The exclusive upper bound preserves diversity — two
                    # capabilities with identical schemas may hide different
                    # internal logic, and merging them risks destroying that
                    # diversity. Require some overlap (>= 0.5) but not full
                    # identity (< 1.0).
                    if (
                        partner_id != cap_id
                        and partner_id in low_reuse_set
                        and partner_id not in paired
                        and similar_cap.is_active
                        and similar_cap.capability_type == cap.capability_type
                        and score >= 0.5
                    ):
                        jaccard = _schema_jaccard(cap, similar_cap)
                        if 0.5 <= jaccard < 1.0:
                            merge_cands.append((cap_id, partner_id))
                            paired.add(cap_id)
                            paired.add(partner_id)
                            break
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed similarity search for '%s' (merge): %s", cap_id, e)

        return Command(
            update={
                "split_candidates": split_cands,
                "merge_candidates": merge_cands,
            },
            goto="apply_operations",
        )

    def apply_operations_node(state: OptimizerState) -> Command:
        """Apply prune, split, and merge operations.

        Each operation is gated independently against ``optimizer_config``
        per §12 finding #8. The gate resolves ``"auto"`` against the
        current ``AUTO_OPTIMIZER_THRESHOLD`` using ``invocation_count`` from
        the metrics store; ``"on"`` always runs; ``"off"`` always skips.
        """
        errors = list(state.errors)
        pruned: list[str] = []
        split_results: dict[str, list[str]] = {}
        merge_results: dict[str, str] = {}

        # Resolve the invocation_count once so all three gates see the
        # same snapshot. Falling back to 0 on any error means "auto"
        # effectively becomes "off" — safer than running an unbounded
        # optimizer when the metrics store is broken.
        try:
            invocation_count = metrics_store.invocation_count_total()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Optimizer: failed to read invocation_count_total (%s); "
                "treating 'auto' as 'off'",
                e,
            )
            invocation_count = 0

        # Prune
        if _config.is_enabled(
            "prune", invocation_count=invocation_count, threshold=AUTO_OPTIMIZER_THRESHOLD,
        ):
            try:
                pruned = prune(registry, metrics_store, window_days=prune_window_days)
            except Exception as e:  # noqa: BLE001
                errors.append(f"prune failed: {e}")
        else:
            logger.debug("Optimizer: prune skipped (config=%s)", _config.prune)

        # Filter out pruned capabilities so we don't attempt split/merge on
        # deactivated items (avoids wasted LLM calls and double-deactivation).
        pruned_set = set(pruned)

        # Split
        if _config.is_enabled(
            "split", invocation_count=invocation_count, threshold=AUTO_OPTIMIZER_THRESHOLD,
        ):
            for cap_id in state.split_candidates:
                if cap_id in pruned_set:
                    logger.debug("Skipping split of pruned capability '%s'", cap_id)
                    continue
                try:
                    new_ids = split(
                        registry, metrics_store, cap_id,
                        llm_model=llm_model,
                        allow_network=state.allow_network,
                    )
                    split_results[cap_id] = new_ids
                except Exception as e:  # noqa: BLE001
                    errors.append(f"split '{cap_id}' failed: {e}")
        else:
            logger.debug("Optimizer: split skipped (config=%s)", _config.split)

        # Merge
        if _config.is_enabled(
            "merge", invocation_count=invocation_count, threshold=AUTO_OPTIMIZER_THRESHOLD,
        ):
            for pair in state.merge_candidates:
                if any(cid in pruned_set for cid in pair):
                    logger.debug("Skipping merge of pair %s (one or both pruned)", pair)
                    continue
                try:
                    merged_id = merge(
                        registry, metrics_store, list(pair),
                        llm_model=llm_model,
                        allow_network=state.allow_network,
                    )
                    key = "+".join(pair)
                    merge_results[key] = merged_id
                except Exception as e:  # noqa: BLE001
                    errors.append(f"merge {pair} failed: {e}")
        else:
            logger.debug("Optimizer: merge skipped (config=%s)", _config.merge)

        # Filter utility_scores to only include capabilities that are still
        # active after prune/split/merge, so pipeline.py carries accurate
        # scores for the next cycle's KL divergence calculation.
        active_caps = {cap.name for cap in registry.list_all(is_active=True)}
        filtered_scores = {
            k: v for k, v in state.utility_scores.items() if k in active_caps
        }

        return Command(
            update={
                "pruned": pruned,
                "split_results": split_results,
                "merge_results": merge_results,
                "errors": errors,
                # Explicitly return filtered utility_scores so pipeline.py can
                # reliably read result["utility_scores"] for cross-cycle KL.
                "utility_scores": filtered_scores,
                "previous_utility_scores": filtered_scores,
            },
            goto=END,
        )

    # Build graph
    graph = StateGraph(OptimizerState)
    graph.add_node("compute_metrics", compute_metrics_node)
    graph.add_node("identify_candidates", identify_candidates_node)
    graph.add_node("apply_operations", apply_operations_node)
    graph.set_entry_point("compute_metrics")

    return graph.compile()
