"""Tests for src/optimizer/loop.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.optimizer.loop import (
    OptimizerState,
    _compute_kl_divergence,
    _schema_jaccard,
    create_optimization_loop,
)
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.metrics import GranularityMetrics


def _make_cap(
    name: str,
    cap_type: CapabilityType = CapabilityType.SKILL,
    *,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        name=name,
        description=f"Description for {name}",
        capability_type=cap_type,
        input_schema=input_schema or {},
        output_schema=output_schema or {},
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


class TestSchemaJaccard:
    """Tests for the _schema_jaccard helper (B1 Merge second conjunct)."""

    def test_both_empty_returns_zero(self):
        """Unpopulated schemas must not form merge candidates — safe default."""
        a = _make_cap("a")
        b = _make_cap("b")
        assert _schema_jaccard(a, b) == 0.0

    def test_one_empty_returns_zero(self):
        """Single-sided empty also rejects the merge — both sides must be populated."""
        a = _make_cap("a", input_schema={"x": "int"}, output_schema={"y": "str"})
        b = _make_cap("b")
        assert _schema_jaccard(a, b) == 0.0
        assert _schema_jaccard(b, a) == 0.0  # symmetric

    def test_full_overlap_returns_one(self):
        """Identical schemas return 1.0 — the gate's exclusive upper bound blocks these."""
        a = _make_cap(
            "a", input_schema={"file_path": "str"}, output_schema={"row_count": "int"}
        )
        b = _make_cap(
            "b", input_schema={"file_path": "str"}, output_schema={"row_count": "int"}
        )
        assert _schema_jaccard(a, b) == 1.0

    def test_partial_overlap(self):
        """Half the keys shared → Jaccard = |intersection| / |union|."""
        a = _make_cap(
            "a",
            input_schema={"file_path": "str", "row_count": "int"},
            output_schema={"result": "dict"},
        )
        b = _make_cap(
            "b",
            input_schema={"file_path": "str", "col_count": "int"},
            output_schema={"result": "dict"},
        )
        # Union = {file_path, row_count, col_count, result} = 4
        # Intersection = {file_path, result} = 2
        # Jaccard = 2 / 4 = 0.5
        assert _schema_jaccard(a, b) == 0.5

    def test_symmetric(self):
        """f(a, b) == f(b, a) for any inputs."""
        a = _make_cap(
            "a",
            input_schema={"k1": "int", "k2": "str"},
            output_schema={"o1": "dict"},
        )
        b = _make_cap(
            "b",
            input_schema={"k1": "int", "k3": "float"},
            output_schema={"o2": "list"},
        )
        assert _schema_jaccard(a, b) == _schema_jaccard(b, a)

    def test_disjoint_returns_zero(self):
        """Completely non-overlapping schemas → 0.0, never a merge candidate."""
        a = _make_cap(
            "a", input_schema={"x": "int"}, output_schema={"y": "str"}
        )
        b = _make_cap(
            "b", input_schema={"p": "int"}, output_schema={"q": "str"}
        )
        assert _schema_jaccard(a, b) == 0.0


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
        """Compiled graph exposes the three canonical nodes.

        Guards against a refactor that silently drops a node (e.g.,
        inlines ``identify_candidates`` into ``compute_metrics``) —
        the three-stage shape is load-bearing for the prune/split/merge
        ordering invariant the other tests depend on.
        """
        registry = MagicMock()
        store = MagicMock()
        graph = create_optimization_loop(registry, store)
        assert graph is not None

        node_names = set(graph.get_graph().nodes.keys())
        # __start__ / __end__ plus our three nodes
        assert {"compute_metrics", "identify_candidates", "apply_operations"}.issubset(
            node_names
        )

    @patch("src.optimizer.loop.prune", return_value=[])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    def test_runs_to_completion(self, mock_merge, mock_split, mock_prune):
        """End-to-end with two active caps: all three pipeline stages populate
        their OptimizerState fields and terminate with no errors.

        Tightens the earlier smoke assertion (`"errors" in result`) into a
        functional check that each of the three stage nodes actually ran
        and wrote output — otherwise a node that quietly short-circuited
        on state would still satisfy the old shape assertion.
        """
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
        # compute_metrics_node populated the snapshot
        assert set(result["capabilities_snapshot"]) == {"cap-a", "cap-b"}
        # identify_candidates_node produced candidate lists (empty here because
        # the mocked metrics don't trip either threshold, but the keys must exist)
        assert result["split_candidates"] == []
        assert result["merge_candidates"] == []
        # apply_operations_node populated the result dicts
        assert result["pruned"] == []
        assert result["split_results"] == {}
        assert result["merge_results"] == {}
        # Clean run — no exceptions recorded
        assert result["errors"] == []

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
        """Merge pairs are formed via registry.search similarity AND schema overlap.

        Both conjuncts of the proposal's merge criterion must fire:
        (1) description cosine >= 0.5, (2) schema Jaccard in [0.5, 1.0).
        """
        # Overlapping schemas. Union of input+output keys per cap:
        #   cap_a = {file_path, row_count, extra_a, result}
        #   cap_b = {file_path, row_count, extra_b, result}
        # Intersection = {file_path, row_count, result} = 3
        # Union = {file_path, row_count, extra_a, extra_b, result} = 5
        # Jaccard = 3/5 = 0.6 → in [0.5, 1.0), the gate accepts the pair.
        shared_inputs = {"file_path": "str", "row_count": "int"}
        cap_a = _make_cap(
            "skill-a",
            CapabilityType.SKILL,
            input_schema={**shared_inputs, "extra_a": "str"},
            output_schema={"result": "dict"},
        )
        cap_b = _make_cap(
            "skill-b",
            CapabilityType.SKILL,
            input_schema={**shared_inputs, "extra_b": "str"},
            output_schema={"result": "dict"},
        )
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

    @patch("src.optimizer.loop.prune", return_value=[])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    def test_merge_candidates_rejected_when_schemas_disjoint(
        self, mock_merge, mock_split, mock_prune
    ):
        """B1 second conjunct: high description cosine but disjoint schemas → no pair.

        Protects against regression of the proposal's *"overlapping input schemas"*
        clause. Without this test, silently removing the Jaccard gate in
        ``identify_candidates_node`` would still pass all other merge tests.
        """
        # Description cosine will be mocked high (0.8), but schemas have zero overlap.
        cap_a = _make_cap(
            "skill-a",
            CapabilityType.SKILL,
            input_schema={"path_a": "str", "size_a": "int"},
            output_schema={"out_a": "dict"},
        )
        cap_b = _make_cap(
            "skill-b",
            CapabilityType.SKILL,
            input_schema={"path_b": "str", "size_b": "int"},
            output_schema={"out_b": "dict"},
        )
        caps = {"skill-a": cap_a, "skill-b": cap_b}

        registry = MagicMock()
        registry.list_all.return_value = list(caps.values())
        registry.get.side_effect = lambda n: caps.get(n)
        registry.count = 2
        registry.search.return_value = [(cap_b, 0.8)]  # high cosine

        store = MagicMock()
        store.compute_granularity_metrics.side_effect = lambda cap_id, **kw: _make_metrics(
            cap_id, reuse_rate=0.01
        )

        graph = create_optimization_loop(registry, store, merge_reuse_threshold=0.05)
        result = graph.invoke(OptimizerState())

        # Jaccard = 0.0 (disjoint) → no pair even though cosine is high
        assert result["merge_candidates"] == []

    @patch("src.optimizer.loop.prune", return_value=[])
    @patch("src.optimizer.loop.split")
    @patch("src.optimizer.loop.merge")
    def test_merge_candidates_rejected_when_schemas_identical(
        self, mock_merge, mock_split, mock_prune
    ):
        """B1 safety rule: identical schemas (Jaccard = 1.0) → no merge.

        Two capabilities with identical input/output contracts may hide
        different internal logic (e.g., strict vs. lenient CSV parsing).
        The exclusive upper bound ``Jaccard < 1.0`` preserves diversity.
        """
        shared_inputs = {"file_path": "str", "row_count": "int"}
        shared_outputs = {"result": "dict"}
        cap_a = _make_cap(
            "skill-a",
            CapabilityType.SKILL,
            input_schema=shared_inputs,
            output_schema=shared_outputs,
        )
        cap_b = _make_cap(
            "skill-b",
            CapabilityType.SKILL,
            input_schema=shared_inputs,
            output_schema=shared_outputs,
        )
        caps = {"skill-a": cap_a, "skill-b": cap_b}

        registry = MagicMock()
        registry.list_all.return_value = list(caps.values())
        registry.get.side_effect = lambda n: caps.get(n)
        registry.count = 2
        registry.search.return_value = [(cap_b, 0.9)]  # high cosine

        store = MagicMock()
        store.compute_granularity_metrics.side_effect = lambda cap_id, **kw: _make_metrics(
            cap_id, reuse_rate=0.01
        )

        graph = create_optimization_loop(registry, store, merge_reuse_threshold=0.05)
        result = graph.invoke(OptimizerState())

        # Jaccard = 1.0 → outside [0.5, 1.0), safety rule blocks the merge
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
        # Overlapping schemas so the B1 Jaccard gate accepts the pair.
        # Jaccard = 3/5 = 0.6 — see test_merge_candidates_use_similarity.
        shared_inputs = {"file_path": "str", "row_count": "int"}
        cap_a = _make_cap(
            "skill-a",
            CapabilityType.SKILL,
            input_schema={**shared_inputs, "extra_a": "str"},
            output_schema={"result": "dict"},
        )
        cap_b = _make_cap(
            "skill-b",
            CapabilityType.SKILL,
            input_schema={**shared_inputs, "extra_b": "str"},
            output_schema={"result": "dict"},
        )
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
        """If compute_granularity_metrics raises in identify_candidates_node,
        the node must log a warning and continue — not crash.

        We mock compute_all_utilities so that compute_metrics_node SUCCEEDS
        and populates capabilities_snapshot. Then store.compute_granularity_metrics
        raises: caught by the SPLIT loop's try/except (src/optimizer/loop.py
        L240-256). That try/except skips the cap without populating
        ``metrics_cache``. The subsequent MERGE loop reads `metrics_cache.get(cap_id)`,
        finds None, and `continue`s (L273-275) — no try/except needed because
        the cache miss is handled structurally. The merge loop's own try/except
        (L290, L320) guards `registry.search()` failures, not metrics failures.
        Either way: no crash, empty candidate lists, graceful exit.
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
