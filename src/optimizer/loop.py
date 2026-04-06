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

from src.optimizer.operations import merge, prune, split
from src.optimizer.utility import compute_all_utilities
from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry
from src.schemas.capability import CapabilityType

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
        return 0.0

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


def create_optimization_loop(
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    prune_window_days: int = 30,
    split_reuse_threshold: float = 0.6,
    split_success_threshold: float = 0.7,
    merge_reuse_threshold: float = 0.05,
    llm_model: str = "openai:gpt-5-mini",
) -> Any:
    """Build the LangGraph optimization loop.

    Returns a compiled StateGraph.
    """

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

        for cap_id in state.capabilities_snapshot:
            try:
                m = metrics_store.compute_granularity_metrics(cap_id)
                cap = registry.get(cap_id)
                if cap is None:
                    continue

                # Split candidates: high reuse + mediocre success, non-MCP_TOOL
                if (
                    m.reuse_rate > split_reuse_threshold
                    and m.success_rate < split_success_threshold
                    and cap.capability_type != CapabilityType.MCP_TOOL
                ):
                    split_cands.append(cap_id)

            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to compute metrics for '%s': %s", cap_id, e)

        # Build merge pairs: low-reuse SKILL pairs with high embedding similarity.
        # Use registry.search() to find semantically similar partners rather than
        # pairing consecutive skills (which would be arbitrary).
        low_reuse_skills: list[str] = []
        for cap_id in state.capabilities_snapshot:
            cap = registry.get(cap_id)
            if cap is None or cap.capability_type != CapabilityType.SKILL:
                continue
            try:
                m = metrics_store.compute_granularity_metrics(cap_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to compute metrics for '%s' (merge): %s", cap_id, e)
                continue
            if m.reuse_rate < merge_reuse_threshold:
                low_reuse_skills.append(cap_id)

        low_reuse_set = set(low_reuse_skills)
        # Compute registry size once — registry.count hits ChromaDB on every call.
        total_caps = registry.count or 1
        n_search = min(max(10, len(low_reuse_skills)), total_caps)
        paired: set[str] = set()
        for skill_id in low_reuse_skills:
            if skill_id in paired:
                continue
            cap = registry.get(skill_id)
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
                    if (
                        partner_id != skill_id
                        and partner_id in low_reuse_set  # only active caps are in low_reuse_set
                        and partner_id not in paired
                        and similar_cap.is_active  # defensive: search() does not filter by is_active
                        and score >= 0.5  # meaningful semantic similarity
                    ):
                        merge_cands.append((skill_id, partner_id))
                        paired.add(skill_id)
                        paired.add(partner_id)
                        break
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed similarity search for '%s' (merge): %s", skill_id, e)

        return Command(
            update={
                "split_candidates": split_cands,
                "merge_candidates": merge_cands,
            },
            goto="apply_operations",
        )

    def apply_operations_node(state: OptimizerState) -> Command:
        """Apply prune, split, and merge operations."""
        errors = list(state.errors)
        pruned: list[str] = []
        split_results: dict[str, list[str]] = {}
        merge_results: dict[str, str] = {}

        # Prune
        try:
            pruned = prune(registry, metrics_store, window_days=prune_window_days)
        except Exception as e:  # noqa: BLE001
            errors.append(f"prune failed: {e}")

        # Filter out pruned capabilities so we don't attempt split/merge on
        # deactivated items (avoids wasted LLM calls and double-deactivation).
        pruned_set = set(pruned)

        # Split
        for cap_id in state.split_candidates:
            if cap_id in pruned_set:
                logger.debug("Skipping split of pruned capability '%s'", cap_id)
                continue
            try:
                new_ids = split(
                    registry, metrics_store, cap_id, llm_model=llm_model
                )
                split_results[cap_id] = new_ids
            except Exception as e:  # noqa: BLE001
                errors.append(f"split '{cap_id}' failed: {e}")

        # Merge
        for pair in state.merge_candidates:
            if any(cid in pruned_set for cid in pair):
                logger.debug("Skipping merge of pair %s (one or both pruned)", pair)
                continue
            try:
                merged_id = merge(
                    registry, metrics_store, list(pair), llm_model=llm_model
                )
                key = "+".join(pair)
                merge_results[key] = merged_id
            except Exception as e:  # noqa: BLE001
                errors.append(f"merge {pair} failed: {e}")

        return Command(
            update={
                "pruned": pruned,
                "split_results": split_results,
                "merge_results": merge_results,
                "errors": errors,
                # Stored for reference only — cross-pipeline persistence is via
                # _prev_optimizer_scores in pipeline.py, not this field.
                "previous_utility_scores": state.utility_scores,
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
