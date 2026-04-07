"""Tests for src/optimizer/loop.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.optimizer.loop import (
    OptimizerState,
    _compute_kl_divergence,
    create_optimization_loop,
)
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.metrics import GranularityMetrics


def _make_cap(name: str, cap_type: CapabilityType = CapabilityType.SKILL) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name=name,
        description=f"Description for {name}",
        capability_type=cap_type,
    )


def _make_metrics(
    capability_id: str,
    reuse_rate: float = 0.0,
    success_rate: float = 1.0,
    token_cost: float = 0.0,
    maintenance_burden: float = 0.0,
) -> GranularityMetrics:
    return GranularityMetrics(
        capability_id=capability_id,
        reuse_rate=reuse_rate,
        success_rate=success_rate,
        token_cost=token_cost,
        maintenance_burden=maintenance_burden,
        window_size=100,
    )


class TestOptimizerState:
    def test_default_values(self):
        state = OptimizerState()
        assert state.capabilities_snapshot == []
        assert state.utility_scores == {}
        assert state.kl_divergence is None
        assert state.errors == []

    def test_extra_forbid(self):
        with pytest.raises(Exception):
            OptimizerState(unknown_field="bad")


class TestKLDivergence:
    def test_empty_distributions(self):
        assert _compute_kl_divergence({}, {}) == 0.0
        # prev non-empty, curr empty → catastrophic (all pruned) → inf
        assert _compute_kl_divergence({"a": 1.0}, {}) == float("inf")
        # prev empty, curr non-empty → first cycle, no comparison → 0.0
        assert _compute_kl_divergence({}, {"a": 1.0}) == 0.0

    def test_identical_distributions(self):
        scores = {"a": 0.5, "b": 0.3}
        kl = _compute_kl_divergence(scores, scores)
        assert kl == pytest.approx(0.0, abs=1e-6)

    def test_different_distributions(self):
        prev = {"a": 0.8, "b": 0.2}
        curr = {"a": 0.2, "b": 0.8}
        kl = _compute_kl_divergence(prev, curr)
        assert kl > 0.0

    def test_negative_scores_handled(self):
        prev = {"a": -1.0, "b": 0.5}
        curr = {"a": 0.5, "b": -1.0}
        kl = _compute_kl_divergence(prev, curr)
        assert kl > 0.0


def _build_mock_registry_and_store(cap_names, cap_types=None, metrics_kwargs=None):
    """Build registry and store mocks that won't exhaust side_effect iterators."""
    caps = {}
    for i, name in enumerate(cap_names):
        ct = (cap_types or {}).get(name, CapabilityType.SKILL)
        caps[name] = _make_cap(name, ct)

    registry = MagicMock()
    registry.list_all.return_value = list(caps.values())
    registry.get.side_effect = lambda n: caps.get(n)
    # Provide a real integer so n_search computation doesn't raise TypeError.
    registry.count = len(cap_names)

    store = MagicMock()
    _metrics_kwargs = metrics_kwargs or {}

    def _compute_metrics(cap_id, **kw):
        kw_for_cap = _metrics_kwargs.get(cap_id, {})
        return _make_metrics(cap_id, **kw_for_cap)

    store.compute_granularity_metrics.side_effect = _compute_metrics

    return registry, store


class TestCreateOptimizationLoop:
    def test_compiles_without_error(self):
        registry = MagicMock()
        store = MagicMock()
        graph = create_optimization_loop(registry, store)
        assert graph is not None

    @patch("src.optimizer.loop.prune", return_value=[])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    def test_runs_to_completion(self, mock_merge, mock_split, mock_prune):
        registry, store = _build_mock_registry_and_store(
            ["cap-a", "cap-b"],
            metrics_kwargs={
                "cap-a": {"reuse_rate": 0.1, "success_rate": 0.9, "token_cost": 10.0},
                "cap-b": {"reuse_rate": 0.1, "success_rate": 0.9, "token_cost": 10.0},
            },
        )

        graph = create_optimization_loop(registry, store)
        result = graph.invoke(OptimizerState())

        assert isinstance(result, dict)
        assert "errors" in result

    @patch("src.optimizer.loop.prune", return_value=[])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    def test_kl_divergence_computed(self, mock_merge, mock_split, mock_prune):
        registry, store = _build_mock_registry_and_store(
            ["cap-a"],
            metrics_kwargs={"cap-a": {"reuse_rate": 0.5, "success_rate": 1.0}},
        )

        # First run — no previous scores, KL should be 0
        graph = create_optimization_loop(registry, store)
        result = graph.invoke(OptimizerState())
        assert result["kl_divergence"] == pytest.approx(0.0, abs=1e-6)

    @patch("src.optimizer.loop.prune", return_value=[])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    def test_kl_divergence_with_previous_scores(self, mock_merge, mock_split, mock_prune):
        registry, store = _build_mock_registry_and_store(
            ["cap-a", "cap-b"],
            metrics_kwargs={
                "cap-a": {"reuse_rate": 0.9, "success_rate": 1.0},
                "cap-b": {"reuse_rate": 0.1, "success_rate": 1.0},
            },
        )

        graph = create_optimization_loop(registry, store)
        # Provide different previous scores
        initial = OptimizerState(
            previous_utility_scores={"cap-a": 0.1, "cap-b": 0.9}
        )
        result = graph.invoke(initial)
        # KL should be > 0 because distributions differ
        assert result["kl_divergence"] > 0.0

    @patch("src.optimizer.loop.prune", return_value=["stale-cap"])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    def test_prune_results_captured(self, mock_merge, mock_split, mock_prune):
        registry, store = _build_mock_registry_and_store([])

        graph = create_optimization_loop(registry, store)
        result = graph.invoke(OptimizerState())
        assert result["pruned"] == ["stale-cap"]

    @patch("src.optimizer.loop.prune", return_value=[])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    def test_merge_candidates_use_similarity(self, mock_merge, mock_split, mock_prune):
        """Merge pairs are formed via registry.search similarity, not consecutive indexing."""
        cap_a = _make_cap("skill-a", CapabilityType.SKILL)
        cap_b = _make_cap("skill-b", CapabilityType.SKILL)
        caps = {"skill-a": cap_a, "skill-b": cap_b}

        registry = MagicMock()
        registry.list_all.return_value = list(caps.values())
        registry.get.side_effect = lambda n: caps.get(n)
        registry.count = 2
        # skill-a searches and finds skill-b as similar (score 0.8)
        registry.search.return_value = [(cap_b, 0.8)]

        store = MagicMock()
        # Both skills have low reuse → both are merge candidates
        store.compute_granularity_metrics.side_effect = lambda cap_id, **kw: _make_metrics(
            cap_id, reuse_rate=0.01
        )

        graph = create_optimization_loop(registry, store, merge_reuse_threshold=0.05)
        result = graph.invoke(OptimizerState())

        assert isinstance(result, dict)
        # Both skills identified as low-reuse, skill-a finds skill-b as similar → one pair
        merge_cands = result["merge_candidates"]
        assert len(merge_cands) == 1
        assert set(merge_cands[0]) == {"skill-a", "skill-b"}
        # registry.search was called with skill-a's description
        registry.search.assert_called()

    @patch("src.optimizer.loop.prune", return_value=[])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    def test_merge_candidates_not_formed_when_similarity_below_threshold(
        self, mock_merge, mock_split, mock_prune
    ):
        """Skills with low similarity (score < 0.5) are not paired for merge."""
        cap_a = _make_cap("skill-a", CapabilityType.SKILL)
        cap_b = _make_cap("skill-b", CapabilityType.SKILL)
        caps = {"skill-a": cap_a, "skill-b": cap_b}

        registry = MagicMock()
        registry.list_all.return_value = list(caps.values())
        registry.get.side_effect = lambda n: caps.get(n)
        registry.count = 2
        # skill-b found but similarity is low (0.3 < 0.5 threshold)
        registry.search.return_value = [(cap_b, 0.3)]

        store = MagicMock()
        store.compute_granularity_metrics.side_effect = lambda cap_id, **kw: _make_metrics(
            cap_id, reuse_rate=0.01
        )

        graph = create_optimization_loop(registry, store, merge_reuse_threshold=0.05)
        result = graph.invoke(OptimizerState())

        # Low similarity → no pairs formed
        assert result["merge_candidates"] == []

    @patch("src.optimizer.loop.prune", return_value=["skill-a"])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    @patch("src.optimizer.loop.compute_all_utilities", return_value={"skill-a": 0.1, "skill-b": 0.1})
    def test_pruned_capabilities_skipped_for_merge(
        self, mock_compute, mock_merge, mock_split, mock_prune
    ):
        """apply_operations_node must not attempt merge on capabilities already pruned.

        The test drives identify_candidates_node to produce a real merge pair by:
        - making both skills low-reuse (< merge_reuse_threshold)
        - making registry.search return skill-b as similar to skill-a (score 0.8)
        - making registry.count an integer (2) so n_search is computed correctly

        prune() is mocked to return ["skill-a"].  apply_operations_node must then
        skip the ("skill-a", "skill-b") pair because skill-a is in pruned_set.
        """
        cap_a = _make_cap("skill-a", CapabilityType.SKILL)
        cap_b = _make_cap("skill-b", CapabilityType.SKILL)
        caps = {"skill-a": cap_a, "skill-b": cap_b}

        registry = MagicMock()
        registry.list_all.return_value = list(caps.values())
        registry.get.side_effect = lambda n: caps.get(n)
        # Provide a real integer so n_search = min(max(10, 2), 2) = 2
        registry.count = 2
        # skill-a's search finds skill-b as a similar, active, low-reuse partner
        registry.search.return_value = [(cap_b, 0.8)]

        store = MagicMock()
        # Both skills have reuse_rate=0.01 < merge_reuse_threshold=0.05
        store.compute_granularity_metrics.side_effect = lambda cap_id, **kw: _make_metrics(
            cap_id, reuse_rate=0.01
        )

        graph = create_optimization_loop(registry, store, merge_reuse_threshold=0.05)
        result = graph.invoke(OptimizerState())

        # identify_candidates_node produced [("skill-a", "skill-b")]
        assert result["merge_candidates"] == [("skill-a", "skill-b")]
        # prune returned ["skill-a"] → merge must be skipped
        mock_merge.assert_not_called()
        assert result["merge_results"] == {}

    @patch("src.optimizer.loop.prune", return_value=["skill-a"])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    @patch("src.optimizer.loop.compute_all_utilities", return_value={"skill-a": 0.9})
    def test_pruned_capabilities_skipped_for_split(
        self, mock_compute, mock_merge, mock_split, mock_prune
    ):
        """apply_operations_node must not attempt split on capabilities already pruned.

        The test drives identify_candidates_node to produce skill-a as a split
        candidate by giving it reuse_rate=0.8 (> 0.6) and success_rate=0.5 (< 0.7).
        prune() is then mocked to return ["skill-a"] so the prune-skip guard must
        block the split call.
        """
        cap_a = _make_cap("skill-a", CapabilityType.SKILL)
        caps = {"skill-a": cap_a}

        registry = MagicMock()
        registry.list_all.return_value = list(caps.values())
        registry.get.side_effect = lambda n: caps.get(n)
        registry.count = 1

        store = MagicMock()
        # High reuse + low success → split candidate
        store.compute_granularity_metrics.side_effect = lambda cap_id, **kw: _make_metrics(
            cap_id, reuse_rate=0.8, success_rate=0.5
        )

        graph = create_optimization_loop(
            registry, store,
            split_reuse_threshold=0.6,
            split_success_threshold=0.7,
        )
        result = graph.invoke(OptimizerState())

        # identify_candidates_node identified skill-a as a split candidate
        assert result["split_candidates"] == ["skill-a"]
        # prune returned ["skill-a"] → split must be skipped
        mock_split.assert_not_called()
        assert result["split_results"] == {}

    @patch("src.optimizer.loop.prune", return_value=[])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    @patch("src.optimizer.loop.compute_all_utilities", return_value={"skill-a": 0.5})
    def test_merge_candidate_metrics_failure_does_not_crash(
        self, mock_compute_all, mock_merge, mock_split, mock_prune
    ):
        """If compute_granularity_metrics raises in the merge candidate loop,
        identify_candidates_node must log a warning and continue — not crash.

        We mock compute_all_utilities so that compute_metrics_node SUCCEEDS
        and populates capabilities_snapshot.  Then store.compute_granularity_metrics
        raises in identify_candidates_node, hitting both the split loop try/except
        AND the merge loop try/except (the fix for the missing try/except bug).
        """
        caps = {"skill-a": _make_cap("skill-a", CapabilityType.SKILL)}
        registry = MagicMock()
        registry.list_all.return_value = list(caps.values())
        registry.get.side_effect = lambda n: caps.get(n)
        registry.count = len(caps)

        store = MagicMock()
        # Raises in identify_candidates_node (both split and merge loops)
        store.compute_granularity_metrics.side_effect = RuntimeError("DB error")

        graph = create_optimization_loop(registry, store)
        # Should not raise — both loops catch the exception and continue
        result = graph.invoke(OptimizerState())
        assert isinstance(result, dict)
        # compute_metrics_node succeeded → snapshot populated
        assert result["capabilities_snapshot"] == ["skill-a"]
        # No compute_metrics error (it was mocked to succeed)
        assert not any("compute_metrics failed" in e for e in result.get("errors", []))
        # Both loops found no candidates (exceptions skipped all caps)
        assert result["split_candidates"] == []
        assert result["merge_candidates"] == []
