from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.registry.metrics import GranularityMetrics, InvocationRecord, MetricsStore

# -- helpers --------------------------------------------------------------


def _make_record(**overrides: object) -> InvocationRecord:
    """Create an InvocationRecord with sensible defaults."""
    defaults: dict = {
        "capability_id": "fibonacci",
        "task_id": "task-1",
        "success": True,
        "token_cost": 100,
        "latency_ms": 50.0,
    }
    defaults.update(overrides)
    return InvocationRecord(**defaults)


# -- fixtures -------------------------------------------------------------


@pytest.fixture
def store():
    """In-memory DuckDB metrics store, auto-closed after test."""
    s = MetricsStore()
    yield s
    s.close()


# -- record & count -------------------------------------------------------


def test_record_and_count_invocation(store: MetricsStore):
    store.record_invocation(_make_record())
    assert store.invocation_count("fibonacci") == 1


def test_record_multiple_invocations(store: MetricsStore):
    for i in range(3):
        store.record_invocation(_make_record(task_id=f"task-{i}"))
    assert store.invocation_count("fibonacci") == 3


# -- reuse rate -----------------------------------------------------------


def test_reuse_rate_single_task(store: MetricsStore):
    store.record_invocation(_make_record(task_id="task-1"))
    # 1 unique task, and it used fibonacci -> 1.0
    assert store.reuse_rate("fibonacci") == 1.0


def test_reuse_rate_multiple_tasks(store: MetricsStore):
    # 4 distinct tasks: task-1..task-4
    # Only task-1 and task-2 use fibonacci
    store.record_invocation(_make_record(capability_id="fibonacci", task_id="task-1"))
    store.record_invocation(_make_record(capability_id="fibonacci", task_id="task-2"))
    store.record_invocation(_make_record(capability_id="csv-parser", task_id="task-3"))
    store.record_invocation(_make_record(capability_id="csv-parser", task_id="task-4"))
    # 4 unique tasks, 2 used fibonacci -> 0.5
    rate = store.reuse_rate("fibonacci")
    assert rate == pytest.approx(0.5)


# -- success rate ---------------------------------------------------------


def test_success_rate_all_success(store: MetricsStore):
    for i in range(5):
        store.record_invocation(_make_record(task_id=f"task-{i}", success=True))
    assert store.success_rate("fibonacci") == pytest.approx(1.0)


def test_success_rate_mixed(store: MetricsStore):
    for i in range(3):
        store.record_invocation(_make_record(task_id=f"s-{i}", success=True))
    for i in range(2):
        store.record_invocation(_make_record(task_id=f"f-{i}", success=False))
    assert store.success_rate("fibonacci") == pytest.approx(0.6)


def test_success_rate_no_invocations(store: MetricsStore):
    assert store.success_rate("nonexistent") == 0.0


# -- avg token cost -------------------------------------------------------


def test_avg_token_cost(store: MetricsStore):
    costs = [100, 200, 300]
    for i, cost in enumerate(costs):
        store.record_invocation(_make_record(task_id=f"task-{i}", token_cost=cost))
    assert store.avg_token_cost("fibonacci") == pytest.approx(200.0)


# -- maintenance burden ---------------------------------------------------


def test_maintenance_burden_no_events(store: MetricsStore):
    assert store.maintenance_burden("fibonacci") == 0.0


def test_maintenance_burden_with_events(store: MetricsStore):
    # 6 maintenance events in 30 days -> 0.2 per day
    for i in range(6):
        store.record_event("fibonacci", "regenerated", f"attempt {i}")
    burden = store.maintenance_burden("fibonacci", window_days=30)
    assert burden == pytest.approx(6.0 / 30.0)


# -- compute_granularity_metrics ------------------------------------------


def test_compute_granularity_metrics(store: MetricsStore):
    # Set up some data
    for i in range(4):
        store.record_invocation(
            _make_record(task_id=f"task-{i}", success=(i % 2 == 0), token_cost=100 * (i + 1))
        )
    store.record_event("fibonacci", "patched")

    metrics = store.compute_granularity_metrics("fibonacci")
    assert isinstance(metrics, GranularityMetrics)
    assert metrics.capability_id == "fibonacci"
    assert 0.0 <= metrics.reuse_rate <= 1.0
    assert metrics.success_rate == pytest.approx(0.5)
    assert metrics.token_cost == pytest.approx(250.0)
    assert metrics.maintenance_burden > 0.0
    assert metrics.window_size == 100


# -- list_capabilities ----------------------------------------------------


def test_list_capabilities(store: MetricsStore):
    store.record_invocation(_make_record(capability_id="fibonacci"))
    store.record_invocation(_make_record(capability_id="csv-parser"))
    store.record_invocation(_make_record(capability_id="date-formatter"))
    caps = store.list_capabilities()
    assert caps == ["csv-parser", "date-formatter", "fibonacci"]


# -- recent_invocations ---------------------------------------------------


def test_recent_invocations_ordering(store: MetricsStore):
    base = datetime(2025, 1, 1, tzinfo=UTC)
    for i in range(5):
        store.record_invocation(
            _make_record(
                task_id=f"task-{i}",
                invoked_at=base + timedelta(hours=i),
            )
        )
    recent = store.recent_invocations("fibonacci", limit=3)
    assert len(recent) == 3
    # Newest first
    assert recent[0].invoked_at > recent[1].invoked_at > recent[2].invoked_at


# -- persistence ----------------------------------------------------------


def test_persistence_with_file(tmp_path):
    db_file = str(tmp_path / "metrics.duckdb")
    with MetricsStore(db_path=db_file) as store:
        store.record_invocation(_make_record())
        assert store.invocation_count("fibonacci") == 1

    # Reopen — data should survive
    with MetricsStore(db_path=db_file) as store2:
        assert store2.invocation_count("fibonacci") == 1


# -- edge cases -----------------------------------------------------------


def test_empty_store_returns_zero_metrics(store: MetricsStore):
    metrics = store.compute_granularity_metrics("nonexistent")
    assert metrics.reuse_rate == 0.0
    assert metrics.success_rate == 0.0
    assert metrics.token_cost == 0.0
    assert metrics.maintenance_burden == 0.0


def test_window_parameter_limits_data(store: MetricsStore):
    # Insert 10 invocations: first 5 succeed, last 5 fail
    base = datetime(2025, 1, 1, tzinfo=UTC)
    for i in range(10):
        store.record_invocation(
            _make_record(
                task_id=f"task-{i}",
                success=(i < 5),
                token_cost=100,
                invoked_at=base + timedelta(hours=i),
            )
        )
    # Window of 5 should only see the last 5 (all failures)
    assert store.success_rate("fibonacci", window=5) == pytest.approx(0.0)
    # Window of 10 should see all (50% success)
    assert store.success_rate("fibonacci", window=10) == pytest.approx(0.5)


def test_record_event(store: MetricsStore):
    store.record_event("fibonacci", "created", "initial creation")
    store.record_event("fibonacci", "regenerated", "fix bug")
    # 'created' is not a maintenance event, 'regenerated' is
    burden = store.maintenance_burden("fibonacci", window_days=30)
    assert burden == pytest.approx(1.0 / 30.0)
