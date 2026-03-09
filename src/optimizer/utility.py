"""Utility function computation for the granularity optimizer.

U(c) = alpha * reuse(c) * success(c) - beta * token_cost(c) - gamma * maintenance(c)
"""

from __future__ import annotations

from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry


def compute_utility(
    capability_id: str,
    metrics_store: MetricsStore,
    *,
    alpha: float = 1.0,
    beta: float = 0.1,
    gamma: float = 0.1,
) -> float:
    """Compute utility score for a single capability.

    Args:
        capability_id: Registry ID of the capability.
        metrics_store: DuckDB metrics store to pull metrics from.
        alpha: Weight for reuse * success (positive contribution).
        beta: Weight for token cost (negative contribution).
        gamma: Weight for maintenance burden (negative contribution).

    Returns:
        Utility score (higher is better).
    """
    m = metrics_store.compute_granularity_metrics(capability_id)
    return alpha * m.reuse_rate * m.success_rate - beta * m.token_cost - gamma * m.maintenance_burden


def compute_all_utilities(
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    alpha: float = 1.0,
    beta: float = 0.1,
    gamma: float = 0.1,
) -> dict[str, float]:
    """Compute utility scores for all active capabilities in the registry.

    Returns:
        Mapping of capability_id to utility score.
    """
    capabilities = registry.list_all(is_active=True)
    return {
        cap.name: compute_utility(
            cap.name, metrics_store, alpha=alpha, beta=beta, gamma=gamma
        )
        for cap in capabilities
    }
