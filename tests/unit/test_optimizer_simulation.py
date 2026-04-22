"""Tests for src/optimizer/simulation.py — simulation harness."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.optimizer.simulation import (
    SimConfig,
    SimResult,
    _compute_utility,
    _kl_divergence,
    _scores_to_histogram,
    compare_baselines,
    generate_synthetic_registry,
    plot_results,
    run_simulation,
)
from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def short_config() -> SimConfig:
    """Short config for fast tests."""
    return SimConfig(
        n_capabilities=10,
        n_cycles=5,
        tasks_per_cycle=5,
        seed=42,
        convergence_threshold=0.01,
        convergence_window=3,
    )


@pytest.fixture
def fresh_registry() -> SkillRegistry:
    # Per-test unique collection name — avoids cross-test / cross-session
    # leakage if a concrete ChromaDB backend persists to disk.
    return SkillRegistry(collection_name=f"test_sim_{uuid.uuid4().hex[:8]}")


@pytest.fixture
def fresh_metrics() -> MetricsStore:
    store = MetricsStore(db_path=":memory:")
    yield store
    store.close()


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestSimConfig:
    def test_defaults(self):
        c = SimConfig()
        assert c.n_capabilities == 20
        assert c.n_cycles == 50
        assert c.tasks_per_cycle == 10
        assert c.seed == 42
        assert c.output_dir == "docs/figures"

    def test_extra_forbid(self):
        with pytest.raises(Exception):
            SimConfig(unknown_field="bad")


class TestSimResult:
    def test_minimal(self):
        r = SimResult(cycle=0, registry_size=10, utility_scores=[0.5], kl_divergence=None)
        assert r.cycle == 0
        assert r.n_pruned == 0
        assert r.converged is False

    def test_extra_forbid(self):
        with pytest.raises(Exception):
            SimResult(
                cycle=0,
                registry_size=10,
                utility_scores=[],
                kl_divergence=None,
                extra="bad",
            )


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestKLDivergence:
    def test_identical_distributions(self):
        import numpy as np

        p = np.array([0.2, 0.3, 0.5])
        kl = _kl_divergence(p, p)
        assert kl < 0.001  # Should be ~0

    def test_different_distributions(self):
        import numpy as np

        p = np.array([0.9, 0.05, 0.05])
        q = np.array([0.1, 0.1, 0.8])
        kl = _kl_divergence(p, q)
        assert kl > 0.5  # Should be significantly different

    def test_handles_zeros(self):
        import numpy as np

        p = np.array([1.0, 0.0, 0.0])
        q = np.array([0.0, 0.0, 1.0])
        kl = _kl_divergence(p, q)
        assert kl > 0  # Epsilon prevents log(0)


class TestScoresToHistogram:
    def test_empty_scores(self):
        h = _scores_to_histogram([])
        assert len(h) == 20  # default bins
        assert abs(h.sum() - 1.0) < 0.01  # uniform

    def test_produces_histogram(self):
        scores = [0.1, 0.2, 0.3, 0.4, 0.5]
        h = _scores_to_histogram(scores)
        assert len(h) == 20
        assert h.sum() == len(scores)  # raw counts


# ---------------------------------------------------------------------------
# generate_synthetic_registry
# ---------------------------------------------------------------------------


class TestGenerateSyntheticRegistry:
    def test_creates_n_capabilities(self, short_config, fresh_registry, fresh_metrics):
        generate_synthetic_registry(short_config, fresh_registry, fresh_metrics)
        assert fresh_registry.count == short_config.n_capabilities

    def test_creates_all_three_types(self, short_config, fresh_registry, fresh_metrics):
        generate_synthetic_registry(short_config, fresh_registry, fresh_metrics)
        all_caps = fresh_registry.list_all(is_active=None)
        types = {c.capability_type for c in all_caps}
        # With 10 capabilities and weighted sampling, very likely to have at least 2 types
        assert len(types) >= 2

    def test_creates_invocations(self, short_config, fresh_registry, fresh_metrics):
        generate_synthetic_registry(short_config, fresh_registry, fresh_metrics)
        all_caps = fresh_registry.list_all(is_active=None)
        total_invocations = sum(
            fresh_metrics.invocation_count(c.name) for c in all_caps
        )
        assert total_invocations > 0

    def test_reproducible(self, short_config):
        """Same seed produces same registry.

        Uses UUID-scoped collection names so two parallel test sessions (or a
        dirty ChromaDB on disk from a prior run) cannot collide on the
        previously hardcoded ``repro_test_1`` / ``repro_test_2`` names.
        """
        r1 = SkillRegistry(collection_name=f"repro_{uuid.uuid4().hex[:8]}")
        m1 = MetricsStore(db_path=":memory:")
        generate_synthetic_registry(short_config, r1, m1)

        r2 = SkillRegistry(collection_name=f"repro_{uuid.uuid4().hex[:8]}")
        m2 = MetricsStore(db_path=":memory:")
        generate_synthetic_registry(short_config, r2, m2)

        caps1 = sorted(r1.list_all(is_active=None), key=lambda c: c.name)
        caps2 = sorted(r2.list_all(is_active=None), key=lambda c: c.name)

        assert len(caps1) == len(caps2)
        for c1, c2 in zip(caps1, caps2):
            assert c1.name == c2.name
            assert c1.capability_type == c2.capability_type
            assert c1.domain == c2.domain

        m1.close()
        m2.close()


# ---------------------------------------------------------------------------
# run_simulation
# ---------------------------------------------------------------------------


class TestRunSimulation:
    def test_no_optimization_returns_correct_cycles(self, short_config):
        results = run_simulation(short_config, with_optimization=False)
        assert len(results) == short_config.n_cycles
        for i, r in enumerate(results):
            assert r.cycle == i

    def test_no_optimization_no_operations(self, short_config):
        results = run_simulation(short_config, with_optimization=False)
        for r in results:
            assert r.n_pruned == 0
            assert r.n_split == 0
            assert r.n_merged == 0

    def test_with_optimization_runs_without_error(self, short_config):
        """``run_simulation(with_optimization=True)`` completes the full cycle
        budget and returns well-shaped SimResult rows.

        Without an ``OPENAI_API_KEY`` the real LLM-gated merge/split ops
        silently skip (generators short-circuit), but the cycle loop itself
        must still advance through all n_cycles — guards the cycle-index
        contract + per-cycle operation accounting.

        Note: prune runs with a ``window_days=5`` cutoff, and the
        synthetic registry seeds invocations at ``now - 30 days``, so
        cycle 0's prune deactivates the entire synthetic registry before
        any invocations accumulate. We assert on the cycle-budget shape
        and on operation accounting instead of registry_size — a strict
        registry_size > 0 check would be misleading here.
        """
        results = run_simulation(short_config, with_optimization=True)
        assert len(results) == short_config.n_cycles
        # Cycles increment monotonically from 0
        assert [r.cycle for r in results] == list(range(short_config.n_cycles))
        # Every result row has well-typed operation counts (no silent NaN /
        # None leakage even after the registry is emptied)
        for r in results:
            assert isinstance(r.n_pruned, int) and r.n_pruned >= 0
            assert isinstance(r.n_split, int) and r.n_split >= 0
            assert isinstance(r.n_merged, int) and r.n_merged >= 0
        # With optimization on, cycle 0's prune sweep must actually fire —
        # otherwise the optimizer path is silently a no-op and the test is
        # indistinguishable from with_optimization=False.
        assert results[0].n_pruned > 0

    def test_registry_size_positive(self, short_config):
        results = run_simulation(short_config, with_optimization=False)
        for r in results:
            assert r.registry_size > 0

    def test_utility_scores_present(self, short_config):
        results = run_simulation(short_config, with_optimization=False)
        for r in results:
            assert len(r.utility_scores) > 0

    def test_kl_divergence_none_for_first_cycle(self, short_config):
        results = run_simulation(short_config, with_optimization=False)
        assert results[0].kl_divergence is None

    def test_kl_divergence_present_after_first_cycle(self, short_config):
        results = run_simulation(short_config, with_optimization=False)
        for r in results[1:]:
            assert r.kl_divergence is not None
            assert r.kl_divergence >= 0


class TestConvergenceDetection:
    def test_convergence_detected_when_kl_below_threshold(self):
        """With enough cycles and stable distribution, convergence should occur."""
        config = SimConfig(
            n_capabilities=10,
            n_cycles=30,
            tasks_per_cycle=20,
            convergence_threshold=1.0,  # very generous threshold
            convergence_window=3,
            seed=42,
        )
        results = run_simulation(config, with_optimization=False)
        # With a high threshold and stable distribution, should converge
        converged_any = any(r.converged for r in results)
        assert converged_any, "Expected convergence with generous threshold"

    def test_no_convergence_with_strict_threshold(self):
        """Very strict threshold should not converge in few cycles."""
        config = SimConfig(
            n_capabilities=10,
            n_cycles=5,
            tasks_per_cycle=5,
            convergence_threshold=1e-15,  # impossibly strict
            convergence_window=10,  # larger than n_cycles, so impossible to converge
            seed=42,
        )
        results = run_simulation(config, with_optimization=False)
        converged_any = any(r.converged for r in results)
        assert not converged_any


# ---------------------------------------------------------------------------
# compute_utility (standalone)
# ---------------------------------------------------------------------------


class TestComputeUtility:
    def test_matches_canonical_formula(self, fresh_registry, fresh_metrics):
        """``simulation._compute_utility`` is a local fallback mirror of
        ``src.optimizer.utility.compute_utility`` — the two must stay
        numerically identical under the default weights. ``isinstance(float)``
        alone would miss a silent drift in the formula (e.g., sign flip on
        the token_cost term).
        """
        config = SimConfig(n_capabilities=5, seed=99)
        generate_synthetic_registry(config, fresh_registry, fresh_metrics)
        caps = fresh_registry.list_all(is_active=None)
        cap_name = caps[0].name
        score = _compute_utility(cap_name, fresh_metrics)
        assert isinstance(score, float)

        # Reproduce the formula directly and compare.
        m = fresh_metrics.compute_granularity_metrics(cap_name)
        expected = (
            1.0 * m.reuse_rate * m.success_rate
            - 0.1 * m.token_cost
            - 0.1 * m.maintenance_burden
        )
        assert score == pytest.approx(expected)


# ---------------------------------------------------------------------------
# plot_results (mocked matplotlib)
# ---------------------------------------------------------------------------


class TestPlotResults:
    @pytest.mark.parametrize(
        "label,expected_suffix",
        [
            ("test", "_test"),  # non-empty label → suffix prefixed with "_"
            ("", ""),           # empty label → no suffix
        ],
        ids=["with_label", "empty_label"],
    )
    @patch("src.optimizer.simulation.plt")
    def test_plot_results_creates_figures(
        self, mock_plt, short_config, label, expected_suffix, tmp_path
    ):
        """``plot_results`` emits exactly 4 figures (registry_size /
        utility_distribution / kl_divergence / operation_counts) and the
        empty-vs-non-empty label correctly drives the filename suffix.

        Parametrized to fold the prior ``test_plot_results_with_empty_label``
        redundant variant into one case while also asserting suffix semantics
        the prior tests skipped entirely.
        """
        mock_fig = MagicMock()
        mock_ax = MagicMock()
        mock_plt.subplots.return_value = (mock_fig, mock_ax)

        # Pin output_dir to tmp_path so the test never touches repo figures.
        cfg = short_config.model_copy(update={"output_dir": str(tmp_path)})
        results = run_simulation(cfg, with_optimization=False)
        plot_results(results, cfg, label=label) if label else plot_results(results, cfg)

        # 4 subplot blocks, 4 matching closes
        assert mock_plt.subplots.call_count == 4
        assert mock_plt.close.call_count == 4

        # Each of the 4 figures is saved twice (png + pdf) → 8 savefig calls.
        # Every saved path must carry the expected suffix (or lack thereof).
        saved_paths = [
            call_args[0][0] for call_args in mock_fig.savefig.call_args_list
        ]
        assert len(saved_paths) == 8
        expected_stems = {
            f"registry_size{expected_suffix}",
            f"utility_distribution{expected_suffix}",
            f"kl_divergence{expected_suffix}",
            f"operation_counts{expected_suffix}",
        }
        actual_stems = {p.stem for p in saved_paths}
        assert actual_stems == expected_stems


# ---------------------------------------------------------------------------
# compare_baselines
# ---------------------------------------------------------------------------


class TestCompareBaselines:
    @patch("src.optimizer.simulation.plt")
    def test_returns_two_result_lists(self, mock_plt, short_config):
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        no_opt, opt = compare_baselines(short_config)
        assert len(no_opt) == short_config.n_cycles
        assert len(opt) == short_config.n_cycles

    @patch("src.optimizer.simulation.plt")
    def test_no_opt_has_no_operations(self, mock_plt, short_config):
        mock_plt.subplots.return_value = (MagicMock(), MagicMock())
        no_opt, _ = compare_baselines(short_config)
        for r in no_opt:
            assert r.n_pruned == 0
            assert r.n_split == 0
            assert r.n_merged == 0
