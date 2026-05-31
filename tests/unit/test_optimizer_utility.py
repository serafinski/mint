"""Tests for src/optimizer/utility.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.optimizer.utility import compute_all_utilities, compute_utility
from src.schemas.metrics import GranularityMetrics


def _make_metrics(
    capability_id: str = "test-cap",
    reuse_rate: float = 0.5,
    success_rate: float = 0.8,
    token_cost: float = 100.0,
    maintenance_burden: float = 0.2,
) -> GranularityMetrics:
    return GranularityMetrics(
        capability_id=capability_id,
        reuse_rate=reuse_rate,
        success_rate=success_rate,
        token_cost=token_cost,
        maintenance_burden=maintenance_burden,
        window_size=100,
    )


class TestComputeUtility:
    def test_default_weights(self):
        store = MagicMock()
        store.compute_granularity_metrics.return_value = _make_metrics(
            reuse_rate=0.5, success_rate=0.8, token_cost=100.0, maintenance_burden=0.2
        )
        # U = 1.0 * 0.5 * 0.8 - 0.1 * 100.0 - 0.1 * 0.2
        # U = 0.4 - 10.0 - 0.02 = -9.62
        result = compute_utility("test-cap", store)
        assert result == pytest.approx(-9.62)

    def test_custom_weights(self):
        store = MagicMock()
        store.compute_granularity_metrics.return_value = _make_metrics(
            reuse_rate=0.8, success_rate=1.0, token_cost=10.0, maintenance_burden=0.0
        )
        # U = 2.0 * 0.8 * 1.0 - 0.5 * 10.0 - 0.3 * 0.0
        # U = 1.6 - 5.0 - 0.0 = -3.4
        result = compute_utility("test-cap", store, alpha=2.0, beta=0.5, gamma=0.3)
        assert result == pytest.approx(-3.4)

    def test_zero_metrics(self):
        store = MagicMock()
        store.compute_granularity_metrics.return_value = _make_metrics(
            reuse_rate=0.0, success_rate=0.0, token_cost=0.0, maintenance_burden=0.0
        )
        result = compute_utility("test-cap", store)
        assert result == pytest.approx(0.0)

    def test_high_utility(self):
        store = MagicMock()
        store.compute_granularity_metrics.return_value = _make_metrics(
            reuse_rate=1.0, success_rate=1.0, token_cost=0.0, maintenance_burden=0.0
        )
        # U = 1.0 * 1.0 * 1.0 - 0 - 0 = 1.0
        result = compute_utility("test-cap", store)
        assert result == pytest.approx(1.0)

    def test_pulls_correct_capability_id(self):
        store = MagicMock()
        store.compute_granularity_metrics.return_value = _make_metrics()
        compute_utility("my-cap", store)
        store.compute_granularity_metrics.assert_called_once_with("my-cap")


class TestComputeAllUtilities:
    def test_multiple_capabilities(self):
        registry = MagicMock()
        cap1 = MagicMock()
        cap1.name = "cap-a"
        cap1.definition_token_cost = 0
        cap2 = MagicMock()
        cap2.name = "cap-b"
        cap2.definition_token_cost = 0
        # registry.get(name) must return the matching cap mock.
        registry.get.side_effect = lambda n: {"cap-a": cap1, "cap-b": cap2}.get(n)
        registry.list_all.return_value = [cap1, cap2]

        store = MagicMock()
        store.compute_granularity_metrics.side_effect = [
            _make_metrics(
                "cap-a",
                reuse_rate=0.5,
                success_rate=0.8,
                token_cost=100.0,
                maintenance_burden=0.2,
            ),
            _make_metrics(
                "cap-b",
                reuse_rate=1.0,
                success_rate=1.0,
                token_cost=0.0,
                maintenance_burden=0.0,
            ),
        ]

        result = compute_all_utilities(registry, store)
        assert len(result) == 2
        assert "cap-a" in result
        assert "cap-b" in result
        assert result["cap-b"] == pytest.approx(1.0)
        registry.list_all.assert_called_once_with(is_active=True)

    def test_empty_registry(self):
        registry = MagicMock()
        registry.list_all.return_value = []
        store = MagicMock()

        result = compute_all_utilities(registry, store)
        assert result == {}

    def test_custom_weights_forwarded(self):
        registry = MagicMock()
        cap = MagicMock()
        cap.name = "cap-x"
        cap.definition_token_cost = 0
        registry.get.return_value = cap
        registry.list_all.return_value = [cap]

        store = MagicMock()
        store.compute_granularity_metrics.return_value = _make_metrics(
            "cap-x", reuse_rate=0.8, success_rate=1.0, token_cost=10.0, maintenance_burden=0.0
        )

        result = compute_all_utilities(registry, store, alpha=2.0, beta=0.5, gamma=0.3)
        # U = 2.0 * 0.8 * 1.0 - 0.5 * 10.0 - 0.3 * 0.0 - delta * 0 = -3.4
        assert result["cap-x"] == pytest.approx(-3.4)


class TestDefinitionTokenCostPenalty:
    """Definition-token cost lowers utility when registry metadata is available."""

    def test_larger_definition_lowers_utility(self):
        """Two caps with identical metrics but different definition sizes:
        larger-definition cap MUST have lower utility.
        """
        registry = MagicMock()

        cap_small = MagicMock()
        cap_small.name = "cap-small"
        cap_small.definition_token_cost = 50

        cap_huge = MagicMock()
        cap_huge.name = "cap-huge"
        cap_huge.definition_token_cost = 5000

        registry.list_all.return_value = [cap_small, cap_huge]
        registry.get.side_effect = lambda n: {
            "cap-small": cap_small, "cap-huge": cap_huge,
        }.get(n)

        store = MagicMock()
        # Identical metrics for both caps — only def-cost differs.
        store.compute_granularity_metrics.return_value = _make_metrics(
            reuse_rate=0.5, success_rate=0.9, token_cost=0.0, maintenance_burden=0.0,
        )

        result = compute_all_utilities(registry, store)
        assert result["cap-small"] > result["cap-huge"], (
            "larger definition_token_cost must lower U(c). "
            f"cap-small={result['cap-small']} vs cap-huge={result['cap-huge']}"
        )
        # Exact arithmetic with default delta=0.01:
        # cap-small: 1.0*0.5*0.9 - 0 - 0 - 0.01*50   = 0.45 - 0.5   = -0.05
        # cap-huge:  1.0*0.5*0.9 - 0 - 0 - 0.01*5000 = 0.45 - 50.0  = -49.55
        assert result["cap-small"] == pytest.approx(-0.05)
        assert result["cap-huge"] == pytest.approx(-49.55)

    def test_no_registry_uses_metrics_only_formula(self):
        """No registry means no definition-token-cost term."""
        store = MagicMock()
        store.compute_granularity_metrics.return_value = _make_metrics(
            reuse_rate=0.8, success_rate=1.0, token_cost=10.0, maintenance_burden=0.0,
        )
        # With no registry: delta term is 0.
        u = compute_utility("cap-x", store, registry=None)
        # U = 1.0*0.8*1.0 - 0.1*10.0 - 0.1*0.0 = -0.2
        assert u == pytest.approx(-0.2)

    def test_delta_weight_configurable(self):
        """Passing delta=0.0 disables the Context Economy term explicitly."""
        registry = MagicMock()
        cap = MagicMock()
        cap.name = "cap"
        cap.definition_token_cost = 10000
        registry.get.return_value = cap

        store = MagicMock()
        store.compute_granularity_metrics.return_value = _make_metrics(
            reuse_rate=0.5, success_rate=1.0, token_cost=0.0, maintenance_burden=0.0,
        )
        u0 = compute_utility("cap", store, registry, delta=0.0)
        u1 = compute_utility("cap", store, registry, delta=0.01)
        # delta=0 → no penalty; delta=0.01 → penalty of 100.
        assert u0 == pytest.approx(0.5)
        assert u1 == pytest.approx(0.5 - 100.0)
