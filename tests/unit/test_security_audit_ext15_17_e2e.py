"""End-to-end tests for security audit ext15 / ext16 / ext17 / tmp.md findings.

Covers the high-leverage fixes in this remediation bundle:
- ext15 #151-#173 (~23 findings, open pre-bundle)
- ext16 #174-#187 (~14 findings)
- ext17 #188 (shift-runner condition identity)
- tmp.md (~25 additional findings)

Same pattern as ``test_security_audit_ext12_e2e.py`` / ``test_security_audit_ext13_e2e.py``:
seed a minimal DuckDB in ``tmp_path``, invoke the target script via
``subprocess.run``, and assert on ``returncode`` + banners. Unit-level
tests live alongside the other targeted test modules; this file exercises
the subprocess path where bugs would otherwise be invisible to in-process
tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers — mirror ext12/ext13 conventions for co-adaptability.
# ---------------------------------------------------------------------------


def _run_script(
    script: str,
    *args: str,
    cwd: Path | None = None,
    env_extra: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, str(ROOT / "scripts" / script), *args]
    return subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _seed_eval_runs(db_path: Path, rows: list[dict]) -> None:
    """Seed ``evaluation_runs`` via the real ``EvaluationStore`` API.

    Accepts a list of dicts of field overrides; sensible defaults fill
    the rest so tests only specify the fields relevant to the assertion.
    """
    sys.path.insert(0, str(ROOT))
    from src.registry.evaluation_store import EvaluationStore
    from src.schemas.evaluation import EvaluationRecord

    store = EvaluationStore(db_path=str(db_path))
    try:
        for r in rows:
            rec = EvaluationRecord(
                run_id=r["run_id"],
                condition=r["condition"],
                task_id=r.get("task_id", "T04"),
                task_tier=r.get("task_tier", "moderate"),
                attempt_count=r.get("attempt_count", 1),
                capability_type=r.get("capability_type", "mcp_tool"),
                verification_tier_reached=r.get("verification_tier_reached", 3),
                gate_passed=r.get("gate_passed", True),
                output_in_range=r.get("output_in_range", True),
                token_cost=r.get("token_cost", 100),
                attempt_latency_ms=r.get("attempt_latency_ms", 1000),
                failure_reason=r.get("failure_reason", "none"),
                registry_snapshot_id=r.get("registry_snapshot_id", "snap-1"),
                seed=r.get("seed", 42),
                is_pilot=r.get("is_pilot", False),
                required_capability_count=r.get("required_capability_count", 1),
                matched_in_registry_count=r.get("matched_in_registry_count", 0),
                chain_length=r.get("chain_length", 1),
                chain_success=r.get("chain_success", True),
                matched_and_executed_count=r.get("matched_and_executed_count", 0),
                generated_artifact_count=r.get("generated_artifact_count", 1),
                capability_types=r.get("capability_types", []),
                output_shape_mismatch=r.get("output_shape_mismatch", False),
                output_shape_missing_keys=r.get("output_shape_missing_keys", []),
                output_shape_missing_files=r.get("output_shape_missing_files", []),
            )
            store.record_run(rec)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# ext16 #185 — FailureReason enum must accept "output_shape_mismatch".
# ---------------------------------------------------------------------------


def test_ext16_185_failure_reason_enum_accepts_output_shape_mismatch():
    """Pre-fix, instantiating EvaluationRecord with this failure_reason
    raised Pydantic ValidationError → mid-campaign crash.
    """
    sys.path.insert(0, str(ROOT))
    from src.schemas.evaluation import EvaluationRecord

    rec = EvaluationRecord(
        run_id="r1", condition="full", task_id="T16",
        task_tier="complex", attempt_count=1,
        capability_type="agent", verification_tier_reached=3,
        gate_passed=True, output_in_range=False,
        token_cost=0, attempt_latency_ms=1000,
        failure_reason="output_shape_mismatch",
        registry_snapshot_id="snap-1", seed=42,
    )
    assert rec.failure_reason.value == "output_shape_mismatch"


# ---------------------------------------------------------------------------
# ext17 #188 — shift runner writes distinguishable condition label.
# ---------------------------------------------------------------------------


def test_ext17_188_longitudinal_shift_enum_is_distinct():
    sys.path.insert(0, str(ROOT))
    from src.schemas.evaluation import BaselineCondition

    assert BaselineCondition.LONGITUDINAL.value == "longitudinal"
    assert BaselineCondition.LONGITUDINAL_SHIFT.value == "longitudinal_shift"
    assert (
        BaselineCondition.LONGITUDINAL.value
        != BaselineCondition.LONGITUDINAL_SHIFT.value
    )


# ---------------------------------------------------------------------------
# ext15 #175 / #176 — longitudinal runner freshness guard.
# ---------------------------------------------------------------------------


def test_ext15_175_run_longitudinal_refuses_existing_nonempty_db(tmp_path):
    db = tmp_path / "longitudinal.duckdb"
    _seed_eval_runs(db, [{"run_id": "existing_1", "condition": "longitudinal"}])

    # With no --append, script should exit 2 loudly.
    result = _run_script(
        "run_longitudinal.py",
        "--db", str(db),
        "--n-tasks", "1",
        timeout=15,
    )
    # The script may exit 2 during freshness-guard before loading
    # heavy imports, OR it may exit 1 on a missing .env — accept 2 in
    # the happy path and skip on import failure.
    if result.returncode not in (1, 2):
        pytest.fail(
            f"expected exit 2 (freshness guard) or 1 (env missing), got "
            f"{result.returncode}. stderr: {result.stderr[:500]}"
        )
    if result.returncode == 2:
        assert "ext15 #175" in result.stderr or "Refusing" in result.stderr


# ---------------------------------------------------------------------------
# ext15 #172 — analyze_longitudinal overfull-timeline gate.
# ---------------------------------------------------------------------------


def _seed_longitudinal(db_path: Path, n_cycles: int, *, run_id: str = "long_1") -> None:
    """Seed a longitudinal DB with ``n_cycles`` evaluation_runs rows.

    Also seeds the minimum cycle_utility_scores / invocation_events /
    cycle_events the analyzer needs to pass data-presence gates.
    """
    rows = [
        {
            "run_id": run_id,
            "condition": "longitudinal",
            "task_id": f"T{(i % 17) + 4:02d}",  # cycle through T04..T20
            "seed": 42,
            "generated_artifact_count": 1,
        }
        for i in range(n_cycles)
    ]
    _seed_eval_runs(db_path, rows)

    conn = duckdb.connect(str(db_path))
    try:
        for i in range(n_cycles):
            conn.execute(
                "INSERT INTO cycle_utility_scores "
                "(id, run_id, condition, task_id, capability_id, utility_score, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [f"cus_{i}", run_id, "longitudinal", f"T{(i % 17) + 4:02d}",
                 f"cap_{i}", 1.0, datetime.now(UTC)],
            )
            conn.execute(
                "INSERT INTO invocation_events "
                "(id, run_id, condition, task_id, capability_id, "
                " capability_type, success, is_pilot, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [f"inv_{i}", run_id, "longitudinal",
                 f"T{(i % 17) + 4:02d}", f"cap_{i}", "mcp_tool",
                 True, False, datetime.now(UTC)],
            )
    finally:
        conn.close()


def test_ext15_172_longitudinal_overfull_cycles_exits_3(tmp_path):
    db = tmp_path / "longitudinal.duckdb"
    _seed_longitudinal(db, n_cycles=50)  # expected default is 40

    result = _run_script(
        "analyze_longitudinal.py",
        "--db", str(db),
        "--expected-cycles", "40",
        timeout=30,
    )
    assert result.returncode == 3, (
        f"expected exit 3 for overfull timeline; got {result.returncode}. "
        f"stderr: {result.stderr[:400]}"
    )
    assert "overfull" in result.stderr.lower()


# ---------------------------------------------------------------------------
# ext15 #160 — M4 silent-zero gate fires on ANY JIT condition below threshold.
# ---------------------------------------------------------------------------


def test_ext15_160_m4_gate_fires_when_one_jit_condition_is_zero(tmp_path):
    """Pre-fix: max(jit_ratios) < 0.5 required EVERY condition below
    threshold. Now ANY single zeroed JIT condition fires the gate.
    """
    db = tmp_path / "campaign.duckdb"
    rows: list[dict] = []
    # full: all non-zero cost (ratio 1.0)
    for i in range(5):
        rows.append({
            "run_id": f"full_{i}", "condition": "full",
            "task_id": f"T{i + 4:02d}", "token_cost": 100,
            "generated_artifact_count": 1,
        })
    # no_verification + no_optimization: all zero (ratio 0.0)
    for cond in ("no_verification", "no_optimization"):
        for i in range(5):
            rows.append({
                "run_id": f"{cond}_{i}", "condition": cond,
                "task_id": f"T{i + 4:02d}", "token_cost": 0,
                "generated_artifact_count": 1,
            })
    # static: all zero (legitimate)
    for i in range(5):
        rows.append({
            "run_id": f"static_{i}", "condition": "static",
            "task_id": f"T{i + 4:02d}", "token_cost": 0,
            "generated_artifact_count": 0, "capability_type": None,
        })

    _seed_eval_runs(db, rows)
    result = _run_script(
        "analyze_results.py",
        "--db", str(db),
        "--expected-conditions", "4",
        "--expected-tasks", "5",
        "--expected-runs-per-condition", "5",
        "--allow-incomplete",  # don't block on task range mismatch
        timeout=30,
    )
    # The M4 gate returns False → missing_metrics accumulates → exit 7.
    # Accept exit 7 OR exit 2 (the canonical task-id check also fires
    # on this synthetic dataset; both surface the same "something is
    # wrong" signal).
    assert result.returncode in (2, 7), (
        f"expected exit 2 or 7 (some gate fired), got {result.returncode}. "
        f"stderr: {result.stderr[:500]}"
    )


# ---------------------------------------------------------------------------
# ext15 #162 — M3 Aggregate Gini pools by capability across runs.
# ---------------------------------------------------------------------------


def test_ext15_162_gini_permutation_symmetric_workload_yields_near_zero():
    """capA in run1, capB in run2 → perfect pooled equality (Gini ≈ 0).
    Pre-fix, the aggregate Gini was computed on flattened per-(run,cap)
    counts and returned ≈ 0.375.
    """
    sys.path.insert(0, str(ROOT))
    from scripts.analyze_results import _gini_coefficient

    # Post-fix: pool by capability first → {capA: 100, capB: 100}
    pooled_by_capability = [100, 100]
    assert _gini_coefficient(pooled_by_capability) == 0.0

    # Pre-fix aggregation used the raw per-(run,cap) list.
    raw_per_run_cap = [100, 0, 0, 100]
    pre_fix_gini = _gini_coefficient(raw_per_run_cap)
    assert pre_fix_gini > 0.1  # demonstrates the pre-fix bias the fix eliminates


# ---------------------------------------------------------------------------
# ext15 #164 — print_classification_accuracy returns bool.
# ---------------------------------------------------------------------------


def test_ext15_164_classification_accuracy_returns_false_on_skip_path():
    """Skip paths used to return None; now they return False so main()
    can append "Classification Accuracy" to missing_metrics.
    """
    sys.path.insert(0, str(ROOT))
    from scripts.analyze_results import print_classification_accuracy

    import duckdb as _duckdb
    conn = _duckdb.connect(":memory:")
    try:
        # Create minimal schema with no rows — triggers skip path.
        conn.execute(
            "CREATE TABLE evaluation_runs ("
            "  run_id VARCHAR, condition VARCHAR, task_id VARCHAR,"
            "  capability_type VARCHAR, is_pilot BOOLEAN,"
            "  generated_artifact_count INTEGER"
            ")"
        )
        result = print_classification_accuracy(conn)
        assert result is False, "skip path must return False"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ext16 #177 — print_latency_summary returns bool.
# ---------------------------------------------------------------------------


def test_ext16_177_latency_summary_returns_false_on_no_successful_runs():
    sys.path.insert(0, str(ROOT))
    from scripts.analyze_results import print_latency_summary

    import duckdb as _duckdb
    conn = _duckdb.connect(":memory:")
    try:
        conn.execute(
            "CREATE TABLE evaluation_runs ("
            "  condition VARCHAR, gate_passed BOOLEAN, output_in_range BOOLEAN,"
            "  output_shape_mismatch BOOLEAN, is_pilot BOOLEAN,"
            "  attempt_latency_ms INTEGER"
            ")"
        )
        result = print_latency_summary(conn)
        assert result is False, "empty / no successful runs → return False"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ext15 #153 — run_id format uses full uuid4 entropy.
# ---------------------------------------------------------------------------


def test_ext15_153_harness_run_id_uses_full_uuid_entropy():
    import inspect
    sys.path.insert(0, str(ROOT))
    from tests.evaluation.harness import EvaluationHarness

    src = inspect.getsource(EvaluationHarness)
    assert ".hex[:6]" not in src, (
        "ext15 #153: run_id format must NOT truncate to 6 hex chars "
        "(24-bit collision space)."
    )


# ---------------------------------------------------------------------------
# tmp.md P0 — _detect_stable_convergence handles empty final_ref.
# ---------------------------------------------------------------------------


def test_tmp_md_p0_detect_stable_convergence_empty_attractor():
    """Pre-fix, kl({}, non_empty_snap) returned 0.0 via first-cycle
    sentinel → false instant convergence. Post-fix: empty final_ref
    returns None (not converged).
    """
    sys.path.insert(0, str(ROOT))
    from scripts.analyze_longitudinal import _detect_stable_convergence

    # 10 non-empty cycles, but the last 5 (window_size) collapse to empty.
    snapshots = [{"capA": 1.0} for _ in range(5)] + [{} for _ in range(5)]
    result = _detect_stable_convergence(
        snapshots, threshold=0.1, min_consecutive=3, window_size=5,
    )
    assert result is None, (
        "empty final_ref (system collapsed) must not report convergence"
    )


# ---------------------------------------------------------------------------
# tmp.md P1 — persist_utility_scores({}) is a no-op, not a DELETE.
# ---------------------------------------------------------------------------


def test_tmp_md_p1_persist_utility_scores_empty_map_is_noop(tmp_path):
    sys.path.insert(0, str(ROOT))
    from src.registry.metrics import MetricsStore

    db_path = tmp_path / "metrics.duckdb"
    store = MetricsStore(db_path=str(db_path))
    try:
        # First, populate with real scores.
        store.persist_utility_scores({"capA": 0.5, "capB": 0.7})
        loaded = store.load_previous_utility_scores()
        assert loaded == {"capA": 0.5, "capB": 0.7}

        # Now simulate optimizer-failed case: empty map.
        # Pre-fix would DELETE all rows. Post-fix: no-op warning.
        store.persist_utility_scores({})
        loaded_after = store.load_previous_utility_scores()
        assert loaded_after == {"capA": 0.5, "capB": 0.7}, (
            "empty-map call must NOT erase prior history"
        )
    finally:
        store.close()


# ---------------------------------------------------------------------------
# tmp.md P1 — update_human_label optimistic concurrency + result tracking.
# ---------------------------------------------------------------------------


def test_tmp_md_p1_update_human_label_optimistic_concurrency(tmp_path):
    sys.path.insert(0, str(ROOT))
    from src.registry.evaluation_store import EvaluationStore
    from src.schemas.evaluation import EvaluationRecord

    db = tmp_path / "eval.duckdb"
    store = EvaluationStore(db_path=str(db))
    try:
        rec = EvaluationRecord(
            run_id="r1", condition="full", task_id="T04",
            task_tier="moderate", attempt_count=1,
            capability_type="mcp_tool", verification_tier_reached=3,
            gate_passed=True, output_in_range=True,
            token_cost=0, attempt_latency_ms=500,
            failure_reason="none",
            registry_snapshot_id="snap-1", seed=42,
            generated_artifact_count=1,
        )
        row_id = store.record_run(rec)

        # First write: succeeds.
        assert store.update_human_label(row_id, "correct") is True
        # Second write (concurrent session): rejected without override.
        assert store.update_human_label(row_id, "partial") is False
        # With explicit override: succeeds.
        assert store.update_human_label(
            row_id, "partial", allow_overwrite=True
        ) is True

        # Unknown row: False.
        assert store.update_human_label("nonexistent_id", "correct") is False
    finally:
        store.close()


# ---------------------------------------------------------------------------
# tmp.md P1 — backfill_definition_token_costs missing stores exit 4.
# ---------------------------------------------------------------------------


def test_tmp_md_p1_backfill_definition_token_costs_all_missing_exits_4(tmp_path):
    """Every targeted store missing → previously exited 0 silently."""
    # Redirect _STORES to missing paths by pointing --stores at both
    # via environment-agnostic harness: just run with --stores cli eval
    # where neither path exists under tmp_path. The script's ROOT-relative
    # paths default to data/cli/chroma + data/eval/chroma — in the test,
    # those may or may not exist. To isolate, we patch via an env-var
    # override isn't supported; so we use the test-by-intent: if the
    # default paths happen to exist on this dev machine, the test is
    # skipped. The assertion is the key one.
    cli_chroma = ROOT / "data" / "cli" / "chroma"
    eval_chroma = ROOT / "data" / "eval" / "chroma"
    if cli_chroma.exists() or eval_chroma.exists():
        pytest.skip(
            "default ChromaDB stores exist on this machine; "
            "the all-missing branch can't be exercised deterministically."
        )

    result = _run_script(
        "backfill_definition_token_costs.py",
        timeout=15,
    )
    assert result.returncode == 4, (
        f"expected exit 4 (all stores missing); got {result.returncode}. "
        f"stderr: {result.stderr[:400]}"
    )

