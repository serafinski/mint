"""Tests for MetricsStore persistence across evaluation runs."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from src.registry.metrics import MetricsStore
from src.schemas.metrics import InvocationRecord
from tests.evaluation.harness import HarnessConfig

ROOT = Path(__file__).parent.parent.parent


def test_harness_config_has_metrics_store_path_field():
    """HarnessConfig exposes a filesystem path for persistent MetricsStore state."""
    fields = {f.name for f in dataclasses.fields(HarnessConfig)}
    assert "metrics_store_path" in fields, (
        "HarnessConfig must expose `metrics_store_path` so "
        "campaigns can opt into persistent MetricsStore."
    )


def test_metrics_store_persists_invocations_across_sessions(tmp_path):
    """A store opened at a filesystem path reads back rows from a prior
    session against the same path.
    """
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
        "MetricsStore must persist invocations when given a filesystem path."
    )
    assert rows[0][0] == "cap-x"
    ms2.close()


def test_default_in_memory_still_works():
    """Callers without a path still get the in-memory backend."""
    ms = MetricsStore()  # default :memory:
    # Writing + reading within one session still works — no regression.
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
    """The longitudinal runner must construct MetricsStore with a non-default path."""
    runners = [
        ROOT / "scripts" / "run_longitudinal.py",
    ]
    for path in runners:
        src = path.read_text()
        # Either explicit db_path= kwarg with a DATA_DIR reference,
        # or a literal path string.
        assert "MetricsStore(db_path=" in src, (
            f"{path.name} must pass db_path= to MetricsStore "
            "(not the default :memory:) so maintenance_burden persists "
            "across runs."
        )
        # Further: the path should not be literal ":memory:"
        assert 'MetricsStore(db_path=":memory:")' not in src


def test_load_previous_utility_scores_returns_empty_on_fresh_store():
    """A freshly-constructed MetricsStore has no previous scores."""
    ms = MetricsStore()
    assert ms.load_previous_utility_scores() == {}
    ms.close()


def test_persist_and_reload_utility_scores_roundtrip():
    """persist_utility_scores writes; load_previous_utility_scores reads back."""
    ms = MetricsStore()
    ms.persist_utility_scores({"cap-a": 1.5, "cap-b": -0.3})
    loaded = ms.load_previous_utility_scores()
    assert loaded == {"cap-a": 1.5, "cap-b": -0.3}
    ms.close()


def test_persist_utility_scores_is_replace_not_append():
    """A second persist call REPLACES the prior map (it's the current
    snapshot, not an accumulator).
    """
    ms = MetricsStore()
    ms.persist_utility_scores({"cap-a": 1.0, "cap-b": 2.0})
    ms.persist_utility_scores({"cap-c": 3.0})  # cap-a, cap-b should be gone
    loaded = ms.load_previous_utility_scores()
    assert loaded == {"cap-c": 3.0}
    ms.close()


def test_utility_scores_survive_across_sessions_with_persistent_path(tmp_path):
    """Persisted utility scores survive a close/reopen cycle."""
    db_path = str(tmp_path / "metrics.duckdb")
    ms1 = MetricsStore(db_path=db_path)
    ms1.persist_utility_scores({"cap-x": 42.0})
    ms1.close()

    ms2 = MetricsStore(db_path=db_path)
    assert ms2.load_previous_utility_scores() == {"cap-x": 42.0}
    ms2.close()


def test_harness_default_uses_in_memory_metrics_store():
    """If HarnessConfig.metrics_store_path is None (default), the harness's
    MetricsStore falls back to :memory:.
    """
    default_cfg = HarnessConfig(
        chroma_dir=Path("/tmp"),
        skills_dir=Path("/tmp"),
    )
    # Default must be None so tests and isolated debug runs remain in-memory.
    path_field = next(
        f for f in dataclasses.fields(HarnessConfig)
        if f.name == "metrics_store_path"
    )
    assert path_field.default is None
    assert default_cfg.metrics_store_path is None


def test_longitudinal_runners_declare_reuse_metrics_store_flag():
    """The longitudinal runner must expose ``--reuse-metrics-store`` so
    cross-invocation accumulation is opt-in.
    """
    runners = [
        ROOT / "scripts" / "run_longitudinal.py",
    ]
    for path in runners:
        src = path.read_text()
        assert '"--reuse-metrics-store"' in src, (
            f"{path.name} must declare a --reuse-metrics-store "
            "flag so callers can opt into cross-invocation state "
            "accumulation. Default behaviour must wipe the DB on start."
        )


def test_longitudinal_runners_reset_metrics_store_by_default():
    """The longitudinal runner must wipe its persistent MetricsStore DB
    at the top of ``main()`` unless the caller passed
    ``--reuse-metrics-store``.
    """
    runners = [
        ROOT / "scripts" / "run_longitudinal.py",
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
            f"{path.name} must wipe its MetricsStore DB at "
            "invocation start when --reuse-metrics-store is NOT passed. "
            "Expected pattern: "
            "``if not args.reuse_metrics_store and <path>.exists(): "
            "<path>.unlink()``."
        )


def test_longitudinal_reset_is_isolated_from_sibling_runner_store(tmp_path):
    """Resetting one metrics DB does not affect a sibling metrics DB.

    Mirrors the runner's logic (``unlink()`` + open fresh ``MetricsStore``)
    without spawning the full pipeline.
    """
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
        f"Wiped DB must be empty after re-open (got {a_rows} rows)."
    )
    assert b_rows == 1, (
        "Wiping runner A's DB must not affect runner B's DB "
        f"(got {b_rows} rows, expected 1)."
    )
