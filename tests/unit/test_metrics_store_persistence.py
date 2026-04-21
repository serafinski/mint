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


# ---------------------------------------------------------------------------
# ext11 #113 — longitudinal runners must reset the metrics_store DB on
# invocation unless ``--reuse-metrics-store`` is passed.
# ---------------------------------------------------------------------------


def test_longitudinal_runners_declare_reuse_metrics_store_flag():
    """Both longitudinal runners must expose ``--reuse-metrics-store`` so
    cross-invocation accumulation is opt-in (ext11 #113). Default = fresh
    baseline per invocation.
    """
    runners = [
        ROOT / "scripts" / "run_longitudinal.py",
        ROOT / "scripts" / "run_longitudinal_shift.py",
    ]
    for path in runners:
        src = path.read_text()
        assert '"--reuse-metrics-store"' in src, (
            f"ext11 #113: {path.name} must declare a --reuse-metrics-store "
            "CLI flag so callers can opt into cross-invocation state "
            "accumulation. Default behaviour must wipe the DB on start."
        )


def test_longitudinal_runners_reset_metrics_store_by_default():
    """Both longitudinal runners must wipe their persistent MetricsStore DB
    at the top of ``main()`` unless the caller passed
    ``--reuse-metrics-store``. Pre-ext11-#113 the DB was never reset;
    cycle-1 KL + prune thresholds silently inherited prior invocation's
    state and Chapter 6 reproducibility broke across back-to-back runs.
    """
    import ast

    runners = [
        ROOT / "scripts" / "run_longitudinal.py",
        ROOT / "scripts" / "run_longitudinal_shift.py",
    ]
    for path in runners:
        src = path.read_text()
        tree = ast.parse(src)
        # Scan the ``main`` function for an ``unlink()`` call guarded by
        # ``not args.reuse_metrics_store``. We don't execute it — AST
        # shape is enough to prevent regression.
        unlink_guarded = False
        for fn_node in ast.walk(tree):
            if not (
                isinstance(fn_node, ast.FunctionDef) and fn_node.name == "main"
            ):
                continue
            for node in ast.walk(fn_node):
                if not isinstance(node, ast.If):
                    continue
                guard_src = ast.unparse(node.test)
                if "reuse_metrics_store" not in guard_src:
                    continue
                body_src = "\n".join(ast.unparse(stmt) for stmt in node.body)
                if "unlink" in body_src:
                    unlink_guarded = True
                    break
        assert unlink_guarded, (
            f"ext11 #113: {path.name} must wipe its MetricsStore DB at "
            "invocation start when --reuse-metrics-store is NOT passed. "
            "Expected pattern: "
            "``if not args.reuse_metrics_store and <path>.exists(): "
            "<path>.unlink()``."
        )


def test_longitudinal_reset_is_isolated_from_sibling_runner_store(tmp_path):
    """Behavioural cross-check on the ext11 #113 reset semantics: wiping
    the DB at invocation start clears every persisted row without affecting
    a sibling DB at a different path.

    Mirrors the runner's logic (``unlink()`` + open fresh ``MetricsStore``)
    without spawning the full pipeline.
    """
    from src.registry.metrics import MetricsStore
    from src.schemas.metrics import InvocationRecord

    db_a = tmp_path / "a.duckdb"
    db_b = tmp_path / "b.duckdb"

    # Session 1 — populate both stores with a row.
    for p in (db_a, db_b):
        ms = MetricsStore(db_path=str(p))
        ms.record_invocation(InvocationRecord(
            capability_id="cap-x", task_id="T01", success=True,
            token_cost=10, latency_ms=5,
        ))
        ms.close()
    assert db_a.exists() and db_b.exists()

    # Simulate runner A's invocation-start reset — B must be untouched.
    if db_a.exists():
        db_a.unlink()
    ms_a = MetricsStore(db_path=str(db_a))
    a_rows = ms_a._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM invocations WHERE capability_id = 'cap-x'"
    ).fetchone()[0]
    ms_a.close()

    ms_b = MetricsStore(db_path=str(db_b))
    b_rows = ms_b._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM invocations WHERE capability_id = 'cap-x'"
    ).fetchone()[0]
    ms_b.close()

    assert a_rows == 0, (
        "ext11 #113: wiped DB must be empty after re-open "
        f"(got {a_rows} rows)."
    )
    assert b_rows == 1, (
        "ext11 #113: wiping runner A's DB must not affect runner B's DB "
        f"(got {b_rows} rows, expected 1)."
    )
