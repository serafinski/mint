"""ext8 #71 — MetricsStore persistence across runs.

Pre-fix the harness + longitudinal runners instantiated ``MetricsStore()``
with the default ``:memory:`` backend. Every run wiped ``maintenance_burden``
+ any accumulated cross-run state, breaking the Pillar 3 "learning over
time" claim — the optimizer had no memory of prior runs' verification
failures.

Post-fix: the harness accepts ``metrics_store_path`` via ``HarnessConfig``
and the longitudinal runners use a per-campaign persistent DB co-located
with their longitudinal.duckdb. Tests here:

- ``HarnessConfig`` exposes the new field.
- ``MetricsStore`` reads back data written by a prior session when given a
  filesystem path.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


def test_harness_config_has_metrics_store_path_field():
    """ext8 #71: HarnessConfig exposes a metrics_store_path field."""
    import dataclasses

    from tests.evaluation.harness import HarnessConfig

    fields = {f.name for f in dataclasses.fields(HarnessConfig)}
    assert "metrics_store_path" in fields, (
        "ext8 #71: HarnessConfig must expose `metrics_store_path` so "
        "campaigns can opt into persistent MetricsStore."
    )


def test_metrics_store_persists_invocations_across_sessions(tmp_path):
    """A store opened at a filesystem path reads back rows from a prior
    session against the same path.
    """
    from src.registry.metrics import MetricsStore
    from src.schemas.metrics import InvocationRecord

    db_path = str(tmp_path / "metrics.duckdb")

    # Session 1: write an invocation.
    ms1 = MetricsStore(db_path=db_path)
    ms1.record_invocation(InvocationRecord(
        capability_id="cap-x", task_id="T01", success=True,
        token_cost=100, latency_ms=50,
    ))
    ms1.close()

    # Session 2: open the same path, confirm the row persists.
    ms2 = MetricsStore(db_path=db_path)
    rows = ms2._conn.execute(  # noqa: SLF001 — test-only direct read
        "SELECT capability_id, task_id, success FROM invocations "
        "WHERE capability_id = 'cap-x'"
    ).fetchall()
    assert len(rows) == 1, (
        "ext8 #71: MetricsStore must persist invocations when given a "
        "filesystem path. Pre-fix the default :memory: backend dropped "
        "this row between sessions."
    )
    assert rows[0][0] == "cap-x"
    ms2.close()


def test_default_in_memory_still_works():
    """Legacy callers (tests without a path) still get the in-memory backend."""
    from src.registry.metrics import MetricsStore

    ms = MetricsStore()  # default :memory:
    # Writing + reading within one session still works — no regression.
    from src.schemas.metrics import InvocationRecord
    ms.record_invocation(InvocationRecord(
        capability_id="cap-y", task_id="T01", success=True,
        token_cost=0, latency_ms=0,
    ))
    rows = ms._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM invocations"
    ).fetchone()
    assert rows[0] == 1
    ms.close()


def test_longitudinal_runners_use_persistent_store_path():
    """ext8 #71 regression guard: both longitudinal runner files MUST
    construct MetricsStore with a non-default path.
    """
    runners = [
        ROOT / "scripts" / "run_longitudinal.py",
        ROOT / "scripts" / "run_longitudinal_shift.py",
    ]
    for path in runners:
        src = path.read_text()
        # Either explicit db_path= kwarg with a DATA_DIR reference,
        # or a literal path string.
        assert "MetricsStore(db_path=" in src, (
            f"ext8 #71: {path.name} must pass db_path= to MetricsStore "
            "(not the default :memory:) so maintenance_burden persists "
            "across runs."
        )
        # Further: the path should not be literal ":memory:"
        assert 'MetricsStore(db_path=":memory:")' not in src


# ---------------------------------------------------------------------------
# ext8 #62 — optimizer previous-utility scores persist via MetricsStore
# ---------------------------------------------------------------------------


def test_load_previous_utility_scores_returns_empty_on_fresh_store():
    """A freshly-constructed MetricsStore has no previous scores."""
    from src.registry.metrics import MetricsStore

    ms = MetricsStore()
    assert ms.load_previous_utility_scores() == {}
    ms.close()


def test_persist_and_reload_utility_scores_roundtrip():
    """persist_utility_scores writes; load_previous_utility_scores reads back."""
    from src.registry.metrics import MetricsStore

    ms = MetricsStore()
    ms.persist_utility_scores({"cap-a": 1.5, "cap-b": -0.3})
    loaded = ms.load_previous_utility_scores()
    assert loaded == {"cap-a": 1.5, "cap-b": -0.3}
    ms.close()


def test_persist_utility_scores_is_replace_not_append():
    """A second persist call REPLACES the prior map (it's the current
    snapshot, not an accumulator).
    """
    from src.registry.metrics import MetricsStore

    ms = MetricsStore()
    ms.persist_utility_scores({"cap-a": 1.0, "cap-b": 2.0})
    ms.persist_utility_scores({"cap-c": 3.0})  # cap-a, cap-b should be gone
    loaded = ms.load_previous_utility_scores()
    assert loaded == {"cap-c": 3.0}
    ms.close()


def test_utility_scores_survive_across_sessions_with_persistent_path(tmp_path):
    """The combination of #62 + #71: persisted scores survive a close()/re-open."""
    from src.registry.metrics import MetricsStore

    db_path = str(tmp_path / "metrics.duckdb")
    ms1 = MetricsStore(db_path=db_path)
    ms1.persist_utility_scores({"cap-x": 42.0})
    ms1.close()

    ms2 = MetricsStore(db_path=db_path)
    assert ms2.load_previous_utility_scores() == {"cap-x": 42.0}
    ms2.close()


def test_harness_default_preserves_legacy_behavior():
    """If HarnessConfig.metrics_store_path is None (default), the harness's
    MetricsStore falls back to :memory: (backward compat with unit tests).
    """
    import dataclasses

    from tests.evaluation.harness import HarnessConfig

    default_cfg = HarnessConfig(
        chroma_dir=Path("/tmp"),
        mcp_tools_dir=Path("/tmp"),
        skills_dir=Path("/tmp"),
    )
    # Default must be None so legacy callers see no behavior change.
    path_field = next(
        f for f in dataclasses.fields(HarnessConfig)
        if f.name == "metrics_store_path"
    )
    assert path_field.default is None
    assert default_cfg.metrics_store_path is None
