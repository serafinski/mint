"""Simulation harness for the Granularity Optimizer (Pillar 3).

Generates synthetic registries, runs optimization cycles, measures convergence
via KL divergence, and produces publication-quality plots.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from pydantic import BaseModel, Field

from src.optimizer.utility import compute_utility as _real_compute_utility
from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.metrics import InvocationRecord

matplotlib.use("Agg")  # non-interactive backend for headless environments


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class SimConfig(BaseModel, extra="forbid"):
    """Configuration for a simulation run."""

    n_capabilities: int = Field(default=20, description="Starting registry size")
    n_cycles: int = Field(default=50, description="Optimization cycles to run")
    tasks_per_cycle: int = Field(default=10, description="Synthetic tasks per cycle")
    success_rate_mean: float = Field(default=0.75, description="Mean success rate")
    token_cost_mean: float = Field(default=100.0, description="Mean token cost")
    convergence_threshold: float = Field(
        default=0.01, description="KL divergence convergence threshold"
    )
    convergence_window: int = Field(
        default=5, description="Consecutive cycles below threshold to declare convergence"
    )
    seed: int = Field(default=42, description="Random seed for reproducibility")
    output_dir: str = Field(default="docs/figures", description="Where to save plots")


class SimResult(BaseModel, extra="forbid"):
    """Result of a single simulation cycle."""

    cycle: int = Field(..., description="Cycle number (0-indexed)")
    registry_size: int = Field(..., description="Number of active capabilities")
    utility_scores: list[float] = Field(
        ..., description="All utility scores in registry this cycle"
    )
    kl_divergence: float | None = Field(
        ..., description="KL divergence vs previous cycle distribution"
    )
    n_pruned: int = Field(default=0, description="Capabilities pruned this cycle")
    n_split: int = Field(default=0, description="Capabilities split this cycle")
    n_merged: int = Field(default=0, description="Capabilities merged this cycle")
    converged: bool = Field(default=False, description="Whether convergence was detected")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CAPABILITY_TYPES = [CapabilityType.MCP_TOOL, CapabilityType.SKILL, CapabilityType.AGENT]
_DOMAINS = ["data-processing", "analytics", "mathematics", "reporting", "text-analysis"]


def _kl_divergence(p: np.ndarray, q: np.ndarray, epsilon: float = 1e-10) -> float:
    """Compute KL(P || Q) with smoothing to avoid log(0).

    NOTE — two KL metrics coexist in this codebase:
    - This function operates on *histograms* of utility scores (via
      ``_scores_to_histogram``), measuring how the *shape of the score
      distribution* shifts cycle to cycle.  Used only in the simulation harness
      for convergence visualisation.
    - ``_compute_kl_divergence`` in ``loop.py`` treats each capability's raw
      score as a point mass and normalises to a probability distribution.  This
      is the *online* metric used inside the LangGraph optimization loop and
      reported in ``OptimizerState.kl_divergence``.
    The thesis evaluation should specify which metric is the primary convergence
    indicator; both are retained because they serve different purposes.
    """
    p = np.asarray(p, dtype=np.float64) + epsilon
    q = np.asarray(q, dtype=np.float64) + epsilon
    p = p / p.sum()
    q = q / q.sum()
    return float(np.sum(p * np.log(p / q)))


def _scores_to_histogram(scores: list[float], n_bins: int = 20) -> np.ndarray:
    """Convert utility scores to a histogram for KL computation.

    Uses a fixed wide range (-200.0, 5.0) that covers extreme utility values.
    With default weights (beta=0.1) and max token_cost=1000 the minimum
    possible score is roughly -100; the -200 lower bound provides a safe margin.
    A consistent range is required so histograms from consecutive cycles
    are over the same support and KL divergence is meaningful.
    """
    if not scores:
        return np.ones(n_bins) / n_bins
    hist, _ = np.histogram(scores, bins=n_bins, range=(-200.0, 5.0))
    return hist.astype(np.float64)


# ---------------------------------------------------------------------------
# Synthetic registry generation
# ---------------------------------------------------------------------------


def generate_synthetic_registry(
    config: SimConfig,
    registry: SkillRegistry,
    metrics_store: MetricsStore,
) -> None:
    """Populate registry with N synthetic capabilities and invocation history.

    Creates a mix of MCP_TOOL, SKILL, and AGENT capabilities with varied
    reuse/success rates and token costs, seeded for reproducibility.
    """
    rng = np.random.default_rng(config.seed)

    type_weights = [0.4, 0.35, 0.25]  # MCP_TOOL, SKILL, AGENT
    types = rng.choice(len(_CAPABILITY_TYPES), size=config.n_capabilities, p=type_weights)
    domains = rng.choice(len(_DOMAINS), size=config.n_capabilities)

    for i in range(config.n_capabilities):
        cap_type = _CAPABILITY_TYPES[int(types[i])]
        domain = _DOMAINS[int(domains[i])]
        name = f"sim-cap-{i:03d}"

        descriptor = CapabilityDescriptor(
            name=name,
            description=f"Synthetic {cap_type.value} capability #{i} for {domain}",
            capability_type=cap_type,
            domain=domain,
            tags=["synthetic", cap_type.value, domain],
        )
        registry.add(descriptor)

        # Generate synthetic invocation history (5-30 invocations per capability)
        n_invocations = int(rng.integers(5, 31))
        cap_success_rate = float(np.clip(rng.normal(config.success_rate_mean, 0.15), 0.1, 1.0))
        cap_token_cost = float(np.clip(rng.normal(config.token_cost_mean, 30.0), 10.0, 500.0))

        base_time = datetime.now(UTC) - timedelta(days=30)
        for j in range(n_invocations):
            record = InvocationRecord(
                capability_id=name,
                task_id=f"sim-task-{rng.integers(0, 100):03d}",
                success=bool(rng.random() < cap_success_rate),
                token_cost=int(np.clip(rng.normal(cap_token_cost, 20.0), 1, 1000)),
                latency_ms=float(np.clip(rng.normal(200.0, 80.0), 10.0, 2000.0)),
                invoked_at=base_time + timedelta(hours=j * 2),
            )
            metrics_store.record_invocation(record)


# ---------------------------------------------------------------------------
# Utility computation (local helper, exposed for direct testing)
# ---------------------------------------------------------------------------


def _compute_utility(
    metrics_store: MetricsStore,
    capability_id: str,
    *,
    alpha: float = 1.0,
    beta: float = 0.1,
    gamma: float = 0.1,
) -> float:
    """Compute utility score for a capability.

    U(c) = alpha * reuse(c) * success(c) - beta * token_cost(c) - gamma * maintenance(c)

    Weights match the canonical defaults in src/optimizer/utility.py.
    Used directly in tests; ``run_simulation`` delegates to the canonical
    ``_real_compute_utility`` (imported from ``src.optimizer.utility``) and
    falls back to this function only if that call raises.
    """
    m = metrics_store.compute_granularity_metrics(capability_id)
    return alpha * m.reuse_rate * m.success_rate - beta * m.token_cost - gamma * m.maintenance_burden


# ---------------------------------------------------------------------------
# Core simulation loop
# ---------------------------------------------------------------------------


def run_simulation(
    config: SimConfig,
    *,
    with_optimization: bool = True,
) -> list[SimResult]:
    """Run the simulation for config.n_cycles cycles.

    Args:
        config: Simulation configuration.
        with_optimization: If True, apply prune/split/merge operations each cycle.
            If False, only generate invocations (baseline).

    Returns:
        List of SimResult, one per cycle.
    """
    # Fresh instances per simulation run
    collection_name = f"sim_{uuid4().hex[:8]}"
    registry = SkillRegistry(collection_name=collection_name)
    metrics_store = MetricsStore(db_path=":memory:")

    generate_synthetic_registry(config, registry, metrics_store)

    if with_optimization:
        from src.optimizer import operations as ops

    rng = np.random.default_rng(config.seed + 1000)  # different seed from generation

    results: list[SimResult] = []
    prev_histogram: np.ndarray | None = None
    consecutive_below_threshold = 0

    for cycle in range(config.n_cycles):
        # 1. Generate synthetic invocations for this cycle
        active_caps = registry.list_all(is_active=True)
        if not active_caps:
            results.append(
                SimResult(
                    cycle=cycle,
                    registry_size=0,
                    utility_scores=[],
                    kl_divergence=None,
                    converged=False,
                )
            )
            continue

        cap_names = [c.name for c in active_caps]

        # Each task invokes 1-3 random capabilities
        for _t in range(config.tasks_per_cycle):
            task_id = f"cycle-{cycle}-task-{_t}"
            n_invoked = int(rng.integers(1, min(4, len(cap_names) + 1)))
            chosen = rng.choice(len(cap_names), size=n_invoked, replace=False)
            for idx in chosen:
                cap_name = cap_names[int(idx)]
                record = InvocationRecord(
                    capability_id=cap_name,
                    task_id=task_id,
                    success=bool(rng.random() < config.success_rate_mean),
                    token_cost=int(np.clip(rng.normal(config.token_cost_mean, 30.0), 1, 1000)),
                    latency_ms=float(np.clip(rng.normal(200.0, 80.0), 10.0, 2000.0)),
                )
                metrics_store.record_invocation(record)

        # 2. Apply optimization operations if enabled
        n_pruned = 0
        n_split = 0
        n_merged = 0

        if with_optimization:
            # Prune: call once per cycle — removes zero-invocation capabilities.
            # window_days=5 approximates "inactive in recent cycles" since all
            # within-simulation invocations carry current timestamps.
            try:
                pruned_ids = ops.prune(registry, metrics_store, window_days=5)
                n_pruned = len(pruned_ids)
            except Exception:
                pass

            # Split candidates: SKILL/AGENT with reuse > 0.6 and success < 0.7.
            # Requires LLM call; silently skipped without a valid API key.
            active_caps = registry.list_all(is_active=True)
            for cap in active_caps:
                if cap.capability_type not in (CapabilityType.SKILL, CapabilityType.AGENT):
                    continue
                m = metrics_store.compute_granularity_metrics(cap.name)
                if m.reuse_rate > 0.6 and m.success_rate < 0.7:
                    try:
                        new_ids = ops.split(registry, metrics_store, cap.name)
                        if new_ids:
                            n_split += 1
                    except Exception:
                        pass

            # Merge candidates: SKILL pairs with reuse < 0.05 AND high embedding
            # similarity (score >= 0.5).  Uses registry.search() to find partners,
            # matching the strategy in loop.py identify_candidates_node.
            # Requires LLM call; silently skipped without a valid API key.
            active_caps = registry.list_all(is_active=True)
            low_reuse_skills: list[str] = []
            for cap in active_caps:
                if cap.capability_type != CapabilityType.SKILL:
                    continue
                m = metrics_store.compute_granularity_metrics(cap.name)
                if m.reuse_rate < 0.05:
                    low_reuse_skills.append(cap.name)

            low_reuse_set = set(low_reuse_skills)
            paired: set[str] = set()
            for skill_id in low_reuse_skills:
                if skill_id in paired:
                    continue
                cap_obj = registry.get(skill_id)
                if cap_obj is None:
                    continue
                try:
                    n_search = min(max(10, len(low_reuse_skills)), registry.count or 1)
                    similar = registry.search(cap_obj.description, n_results=n_search)
                    for similar_cap, score in similar:
                        partner_id = similar_cap.name
                        if (
                            partner_id != skill_id
                            and partner_id in low_reuse_set
                            and partner_id not in paired
                            and similar_cap.is_active
                            and score >= 0.5
                        ):
                            try:
                                merged_id = ops.merge(
                                    registry, metrics_store, [skill_id, partner_id]
                                )
                                if merged_id:
                                    n_merged += 1
                            except Exception:
                                pass
                            paired.add(skill_id)
                            paired.add(partner_id)
                            break
                except Exception:
                    pass

        # 3. Compute utility scores for all active capabilities
        active_caps = registry.list_all(is_active=True)
        utility_scores: list[float] = []
        for cap in active_caps:
            try:
                score = _real_compute_utility(cap.name, metrics_store)
                utility_scores.append(float(score))
            except Exception:
                utility_scores.append(_compute_utility(metrics_store, cap.name))

        # 4. Compute KL divergence vs previous cycle
        current_histogram = _scores_to_histogram(utility_scores)
        kl_div: float | None = None
        if prev_histogram is not None:
            kl_div = _kl_divergence(current_histogram, prev_histogram)
        prev_histogram = current_histogram

        # 5. Check convergence
        if kl_div is not None and kl_div < config.convergence_threshold:
            consecutive_below_threshold += 1
        else:
            consecutive_below_threshold = 0
        converged = consecutive_below_threshold >= config.convergence_window

        results.append(
            SimResult(
                cycle=cycle,
                registry_size=len(active_caps),
                utility_scores=utility_scores,
                kl_divergence=kl_div,
                n_pruned=n_pruned,
                n_split=n_split,
                n_merged=n_merged,
                converged=converged,
            )
        )

    metrics_store.close()
    return results


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------


def plot_results(
    results: list[SimResult],
    config: SimConfig,
    label: str = "",
) -> None:
    """Generate 4 academic-style plots from simulation results.

    Saves PNG + PDF to config.output_dir.
    """
    import pathlib

    out = pathlib.Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    suffix = f"_{label}" if label else ""
    cycles = [r.cycle for r in results]

    # Common style
    plt.rcParams.update({
        "font.size": 11,
        "axes.labelsize": 12,
        "axes.titlesize": 13,
        "legend.fontsize": 10,
    })

    # 1. Registry size over cycles
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(cycles, [r.registry_size for r in results], linewidth=1.5, color="#2c3e50")
    ax.set_xlabel("Cycle")
    ax.set_ylabel("Registry Size")
    ax.set_title("Registry Size Over Optimization Cycles")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out / f"registry_size{suffix}.{ext}", dpi=150)
    plt.close(fig)

    # 2. Utility distribution (box plot, sampled every 5 cycles)
    sample_interval = max(1, len(results) // 10)
    sampled = [r for r in results if r.cycle % sample_interval == 0]
    fig, ax = plt.subplots(figsize=(10, 4))
    if sampled:
        box_data = [r.utility_scores for r in sampled]
        positions = [r.cycle for r in sampled]
        bp = ax.boxplot(box_data, positions=positions, widths=max(1, sample_interval * 0.6))
        for element in ["boxes", "whiskers", "caps", "medians"]:
            plt.setp(bp[element], color="#2c3e50")
    ax.set_xlabel("Cycle")
    ax.set_ylabel("Utility Score")
    ax.set_title("Utility Score Distribution Over Cycles")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out / f"utility_distribution{suffix}.{ext}", dpi=150)
    plt.close(fig)

    # 3. KL divergence convergence curve
    kl_cycles = [r.cycle for r in results if r.kl_divergence is not None]
    kl_values = [r.kl_divergence for r in results if r.kl_divergence is not None]
    fig, ax = plt.subplots(figsize=(8, 4))
    if kl_values:
        ax.plot(kl_cycles, kl_values, linewidth=1.5, color="#2c3e50")
        ax.axhline(
            y=config.convergence_threshold,
            color="#e74c3c",
            linestyle="--",
            linewidth=1,
            label=f"Threshold ({config.convergence_threshold})",
        )
        ax.legend()
    ax.set_xlabel("Cycle")
    ax.set_ylabel("KL Divergence")
    ax.set_title("KL Divergence Convergence")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out / f"kl_divergence{suffix}.{ext}", dpi=150)
    plt.close(fig)

    # 4. Operation counts (stacked bar)
    fig, ax = plt.subplots(figsize=(8, 4))
    pruned = [r.n_pruned for r in results]
    split = [r.n_split for r in results]
    merged = [r.n_merged for r in results]
    ax.bar(cycles, pruned, label="Pruned", color="#3498db", width=0.8)
    ax.bar(cycles, split, bottom=pruned, label="Split", color="#e67e22", width=0.8)
    bottom_merged = [p + s for p, s in zip(pruned, split)]
    ax.bar(cycles, merged, bottom=bottom_merged, label="Merged", color="#2ecc71", width=0.8)
    ax.set_xlabel("Cycle")
    ax.set_ylabel("Count")
    ax.set_title("Optimization Operations Per Cycle")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out / f"operation_counts{suffix}.{ext}", dpi=150)
    plt.close(fig)


def compare_baselines(
    config: SimConfig,
) -> tuple[list[SimResult], list[SimResult]]:
    """Run simulation with and without optimization, plot comparison.

    Returns:
        (no_opt_results, opt_results)
    """
    no_opt = run_simulation(config, with_optimization=False)
    opt = run_simulation(config, with_optimization=True)

    import pathlib

    out = pathlib.Path(config.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    plt.rcParams.update({
        "font.size": 11,
        "axes.labelsize": 12,
        "axes.titlesize": 13,
        "legend.fontsize": 10,
    })

    cycles_no = [r.cycle for r in no_opt]
    cycles_opt = [r.cycle for r in opt]

    # Registry size comparison
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(
        cycles_no,
        [r.registry_size for r in no_opt],
        linewidth=1.5,
        color="#95a5a6",
        linestyle="--",
        label="No Optimization",
    )
    ax.plot(
        cycles_opt,
        [r.registry_size for r in opt],
        linewidth=1.5,
        color="#2c3e50",
        label="With Optimization",
    )
    ax.set_xlabel("Cycle")
    ax.set_ylabel("Registry Size")
    ax.set_title("Registry Size: Optimization vs Baseline")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out / f"comparison_registry_size.{ext}", dpi=150)
    plt.close(fig)

    # Mean utility comparison
    fig, ax = plt.subplots(figsize=(8, 4))
    mean_no = [np.mean(r.utility_scores) if r.utility_scores else 0.0 for r in no_opt]
    mean_opt = [np.mean(r.utility_scores) if r.utility_scores else 0.0 for r in opt]
    ax.plot(cycles_no, mean_no, linewidth=1.5, color="#95a5a6", linestyle="--", label="No Optimization")
    ax.plot(cycles_opt, mean_opt, linewidth=1.5, color="#2c3e50", label="With Optimization")
    ax.set_xlabel("Cycle")
    ax.set_ylabel("Mean Utility Score")
    ax.set_title("Mean Utility: Optimization vs Baseline")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out / f"comparison_mean_utility.{ext}", dpi=150)
    plt.close(fig)

    # KL divergence comparison
    fig, ax = plt.subplots(figsize=(8, 4))
    kl_no = [(r.cycle, r.kl_divergence) for r in no_opt if r.kl_divergence is not None]
    kl_opt = [(r.cycle, r.kl_divergence) for r in opt if r.kl_divergence is not None]
    if kl_no:
        ax.plot(
            [c for c, _ in kl_no],
            [v for _, v in kl_no],
            linewidth=1.5,
            color="#95a5a6",
            linestyle="--",
            label="No Optimization",
        )
    if kl_opt:
        ax.plot(
            [c for c, _ in kl_opt],
            [v for _, v in kl_opt],
            linewidth=1.5,
            color="#2c3e50",
            label="With Optimization",
        )
    ax.axhline(
        y=config.convergence_threshold,
        color="#e74c3c",
        linestyle=":",
        linewidth=1,
        label=f"Threshold ({config.convergence_threshold})",
    )
    ax.set_xlabel("Cycle")
    ax.set_ylabel("KL Divergence")
    ax.set_title("KL Divergence: Optimization vs Baseline")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out / f"comparison_kl_divergence.{ext}", dpi=150)
    plt.close(fig)

    return no_opt, opt
