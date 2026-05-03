"""Utility function computation for the granularity optimizer.

U(c) = alpha * reuse(c) * success(c)
       - beta  * token_cost(c)           (per-invocation execution cost)
       - gamma * maintenance(c)
       - delta * definition_token_cost(c)  (prompt-bloat / Context Economy)

ext8 #66 (2026-04-20): added the ``delta * definition_token_cost`` term so
the formula can optimize for the proposal's "Context Economy" tension
(§Bootstrapping #3 + §Pillar 3 four-metric table line 170). Pre-fix the
optimizer was blind to prompt bloat — whatever the observed registry
evolution did, it wasn't optimizing for definition-token-cost even though
the proposal names it as a core input. ``CapabilityDescriptor.
definition_token_cost`` is populated when capabilities are registered
(seeded, generated, loaded, or optimizer-created).
"""

from __future__ import annotations

from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry


def compute_utility(
    capability_id: str,
    metrics_store: MetricsStore,
    registry: SkillRegistry | None = None,
    *,
    alpha: float = 1.0,
    beta: float = 0.1,
    gamma: float = 0.1,
    delta: float = 0.01,
) -> float:
    """Compute utility score for a single capability.

    Args:
        capability_id: Registry ID of the capability.
        metrics_store: DuckDB metrics store to pull metrics from.
        registry: Optional SkillRegistry; used to read
            ``definition_token_cost`` off the CapabilityDescriptor (ext8
            #66 Context Economy term). When ``None``, the delta term
            contributes 0 (backward-compat for legacy callers without
            registry visibility).
        alpha: Weight for reuse * success (positive contribution).
        beta: Weight for per-invocation token cost (negative contribution).
        gamma: Weight for maintenance burden (negative contribution).
        delta: Weight for definition-token cost (negative contribution).
            Small default — definition cost is measured in tokens per
            capability (often 50-5000) while execution cost is per-
            invocation; delta * definition keeps the term comparable
            in magnitude with the other penalties.

    Returns:
        Utility score (higher is better).
    """
    m = metrics_store.compute_granularity_metrics(capability_id)
    # ext8 #66: Context Economy term. Nonzero only when a registry is
    # supplied — defensive so tests that only exercise metrics-side
    # behavior don't need registry plumbing.
    def_cost = 0
    if registry is not None:
        cap = registry.get(capability_id)
        if cap is not None:
            def_cost = int(getattr(cap, "definition_token_cost", 0) or 0)
    return (
        alpha * m.reuse_rate * m.success_rate
        - beta * m.token_cost
        - gamma * m.maintenance_burden
        - delta * def_cost
    )


def compute_all_utilities(
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    alpha: float = 1.0,
    beta: float = 0.1,
    gamma: float = 0.1,
    delta: float = 0.01,
) -> dict[str, float]:
    """Compute utility scores for all active capabilities in the registry.

    Returns:
        Mapping of capability name to utility score.
    """
    capabilities = registry.list_all(is_active=True)
    return {
        cap.name: compute_utility(
            cap.name, metrics_store, registry,
            alpha=alpha, beta=beta, gamma=gamma, delta=delta,
        )
        for cap in capabilities
    }
