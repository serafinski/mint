"""End-to-end tests for active security audit ext12 script findings.

These tests invoke the analyzer / runner scripts via ``subprocess.run``
(real process, real argparse, real ``sys.exit`` propagation) rather than
importing them as modules. That closes the gap between "source-level
contract pin" and "the orchestrator / run sheet will see the expected
``echo $?`` value."

Each test seeds a minimal DuckDB in a ``tmp_path`` and runs with
``--help`` or a short-circuit flag so wall-clock stays well under the
``testing-conventions`` 5s soft cap.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _run_script(
    script: str,
    *args: str,
    cwd: Path | None = None,
    env_extra: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Invoke a script under ``uv run python`` and return the completed process.

    Uses the project's .venv so imports resolve. Captures stdout + stderr.
    Never raises on non-zero exit — tests inspect ``returncode`` directly.
    """
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


def _seed_evaluation_runs(
    db_path: Path,
    *,
    rows: list[dict],
) -> None:
    """Create a minimal evaluation_runs DB with the given rows.

    Uses the real schema via ``EvaluationStore`` so we don't duplicate
    DDL here and drift from production.
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
                task_id=r.get("task_id", "T01"),
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
            )
            # Override the auto-generated timestamp if provided.
            if "timestamp" in r:
                rec.timestamp = r["timestamp"]
            store.record_run(rec)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# #116 — analyze_longitudinal completeness guard e2e
# ---------------------------------------------------------------------------


def test_analyze_longitudinal_exits_3_on_partial_db(tmp_path):
    """ext12 #116 e2e: a 5-cycle stub DB with --expected-cycles 40 must
    exit 3. Pre-fix this passed as "converged" with exit 0.
    """
    db = tmp_path / "longitudinal_partial.duckdb"
    base_ts = datetime(2026, 4, 21, 10, 0, 0)
    rows = [
        {
            "run_id": "lg_partial",
            "condition": "longitudinal",
            "task_id": f"T{i+1:02d}",
            "timestamp": base_ts + timedelta(seconds=i),
        }
        for i in range(5)
    ]
    _seed_evaluation_runs(db, rows=rows)

    result = _run_script(
        "analyze_longitudinal.py",
        "--db", str(db),
        "--expected-cycles", "40",
    )
    assert result.returncode == 3, (
        f"Expected exit 3 (incomplete campaign), got {result.returncode}. "
        f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    )
    assert "5 cycles, expected 40" in result.stderr or "expected 40" in result.stderr


def test_analyze_longitudinal_allow_incomplete_returns_nonzero_with_banner(tmp_path):
    """ext12 #116: --allow-incomplete lets the analyzer proceed but the
    exit code still signals the shortfall AND the [INCOMPLETE] banner
    must appear in stdout (or stderr)."""
    db = tmp_path / "longitudinal_partial.duckdb"
    base_ts = datetime(2026, 4, 21, 10, 0, 0)
    rows = [
        {
            "run_id": "lg_partial",
            "condition": "longitudinal",
            "task_id": f"T{i+1:02d}",
            "timestamp": base_ts + timedelta(seconds=i),
        }
        for i in range(5)
    ]
    _seed_evaluation_runs(db, rows=rows)

    result = _run_script(
        "analyze_longitudinal.py",
        "--db", str(db),
        "--expected-cycles", "40",
        "--allow-incomplete",
    )
    # Must be non-zero (3 per the contract) even with --allow-incomplete,
    # OR 4 if the zero-evidence gate fires first — either is a non-success
    # exit that the orchestrator can detect.
    assert result.returncode != 0, (
        f"--allow-incomplete must still exit non-zero. Got {result.returncode}."
    )
    combined = result.stdout + result.stderr
    # Banner OR zero-evidence error is acceptable — both indicate the shortfall.
    assert (
        "INCOMPLETE" in combined
        or "zero-evidence" in combined.lower()
        or "no invocation" in combined.lower()
    ), f"Expected INCOMPLETE banner or zero-evidence error. stdout+stderr:\n{combined}"


def test_analyze_longitudinal_exits_1_on_empty_db(tmp_path):
    """ext12 #116: an empty DB is still exit 1 (pre-existing behavior;
    regression guard)."""
    db = tmp_path / "empty.duckdb"
    # Create the DB with just the schema, no rows.
    _seed_evaluation_runs(db, rows=[])

    result = _run_script("analyze_longitudinal.py", "--db", str(db))
    assert result.returncode == 1
    assert "no longitudinal rows" in result.stdout.lower() or "no longitudinal rows" in result.stderr.lower()


# ---------------------------------------------------------------------------
# #118 — zero-evidence DB exit code
# ---------------------------------------------------------------------------


def test_analyze_longitudinal_exits_4_on_zero_evidence_db(tmp_path):
    """ext12 #118 e2e: a DB with evaluation_runs rows but NO
    invocation_events / utility_scores / cycle_events must exit 4 rather
    than report false-positive convergence.
    """
    db = tmp_path / "zero_evidence.duckdb"
    base_ts = datetime(2026, 4, 21, 10, 0, 0)
    rows = [
        {
            "run_id": "zero_ev",
            "condition": "longitudinal",
            "task_id": f"T{(i % 20) + 1:02d}",
            "timestamp": base_ts + timedelta(seconds=i),
        }
        for i in range(40)
    ]
    _seed_evaluation_runs(db, rows=rows)

    result = _run_script(
        "analyze_longitudinal.py",
        "--db", str(db),
        "--expected-cycles", "40",
    )
    assert result.returncode == 4, (
        f"Expected exit 4 (zero-evidence), got {result.returncode}. "
        f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
    )
    combined = result.stderr + result.stdout
    assert (
        "NO invocation" in combined
        or "zero-evidence" in combined.lower()
        or "no signal" in combined.lower()
    )


# ---------------------------------------------------------------------------
# #115 — run_pilot.py --help exit code (smoke test for sys.exit wrapper)
# ---------------------------------------------------------------------------


def test_run_pilot_help_exits_zero_via_sys_exit_wrapper():
    """ext12 #115: ``sys.exit(main() or 0)`` at the entrypoint must
    propagate main()'s integer return. ``--help`` raises SystemExit(0)
    from argparse, which should reach the shell as exit 0.
    """
    result = _run_script("run_pilot.py", "--help", timeout=10)
    assert result.returncode == 0
    # Sanity: the help text should mention at least one of our new-ish flags.
    assert "--condition" in result.stdout


# ---------------------------------------------------------------------------
# #119 — longitudinal runner --help smoke tests (watchdog flags visible)
# ---------------------------------------------------------------------------


def test_run_longitudinal_help_exposes_watchdog_flags():
    result = _run_script("run_longitudinal.py", "--help", timeout=10)
    assert result.returncode == 0
    assert "--per-task-budget-seconds" in result.stdout
    assert "--campaign-budget-hours" in result.stdout


# ---------------------------------------------------------------------------
# #114 — analyze_results.py --help + run on real-shape DB
# ---------------------------------------------------------------------------


def test_analyze_results_help_smoke():
    """Sanity smoke: analyze_results.py parses --help without import errors."""
    result = _run_script("analyze_results.py", "--help", timeout=15)
    assert result.returncode == 0


def test_analyze_results_auto_select_at_production_density_e2e(tmp_path):
    """ext12 #114 e2e: run the real script against a 400-row canonical-shape
    DB without --run-ids. The WARNING line must cite 20 selected run_ids
    across 4 conditions, not 4.
    """
    db = tmp_path / "metrics.duckdb"
    base_ts = datetime(2026, 4, 20, 10, 0, 0)
    rows: list[dict] = []
    # Seed 8 run_ids per condition (32 total) with 20 tasks each (640
    # rows). We pass --per-condition-cap 5 explicitly so this test keeps
    # exercising the original ext12 #114 production-density shape even
    # though the script default is now 3 per condition.
    for c_idx, condition in enumerate(
        ("full", "no_verification", "no_optimization", "static")
    ):
        for run_idx in range(8):
            rid = f"{condition}_run_{run_idx:02d}"
            for task_idx in range(20):
                ts = base_ts + timedelta(
                    seconds=c_idx * 10000 + run_idx * 100 + task_idx
                )
                rows.append({
                    "run_id": rid,
                    "condition": condition,
                    "task_id": f"T{task_idx + 1:02d}",
                    "timestamp": ts,
                })
    _seed_evaluation_runs(db, rows=rows)

    result = _run_script(
        "analyze_results.py",
        "--db", str(db),
        "--per-condition-cap", "5",
        timeout=60,
    )
    # We don't care about the script's exit code for this assertion (it
    # may exit non-zero if some downstream completeness check complains);
    # we care that the WARNING reflects the CORRECTED selection count
    # AND — more importantly — that scoping selected 20 run_ids, not 4.
    combined = result.stdout + result.stderr
    # ext12 #114 post-fix WARNING: "auto-selected 20 total across 4 conditions".
    assert "auto-selected 20 total" in combined, (
        f"ext12 #114 WARNING text must cite 20 selected run_ids. "
        f"Pre-fix it would have said 'auto-selected 4 total' or "
        f"'auto-selecting 5 ... (4 total)'. Got:\n{combined[:3000]}"
    )
    assert "across 4 conditions" in combined


# ---------------------------------------------------------------------------
# #115 / #119 — runner abort-path subprocess e2e
#
# The longitudinal runners + run_pilot do heavy setup at main() import time
# (ChromaDB, snapshot take/restore, registry seeding). These tests use a
# tiny wrapper script that stubs the heavy bits BEFORE importing the
# runner, then invokes main() and exits with its integer return. We
# observe the REAL process exit code via subprocess.returncode — the same
# value a cron / CI orchestrator would see in ``echo $?``.
# ---------------------------------------------------------------------------


def _write_wrapper(path: Path, body: str) -> None:
    """Write a subprocess wrapper script with the project root on sys.path."""
    path.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        + body
    )


def _run_wrapper(wrapper: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(wrapper)],
        capture_output=True, text=True, timeout=timeout,
        env=os.environ.copy(),
    )


def test_run_pilot_exits_7_on_watchdog_abort(tmp_path):
    """ext12 #115 e2e: mock harness.run_condition to raise BudgetExceeded;
    assert the process exits with code 7 (watchdog abort).
    """
    db = tmp_path / "pilot.duckdb"
    wrapper = tmp_path / "wrap_run_pilot_abort.py"
    _write_wrapper(wrapper, f"""
from unittest.mock import MagicMock
import sys

# Patch the harness module BEFORE scripts.run_pilot imports from it.
import tests.evaluation.harness as _h
import scripts._eval_registry_lock as _lock
import scripts.run_pilot as _rp

_h.EvaluationHarness = MagicMock()
_h.HarnessConfig = MagicMock()
_lock.acquire_runner_lock = lambda **kw: MagicMock()

_inst = MagicMock()
_inst.prepare.return_value = "snap-id"
_inst.run_condition.side_effect = _rp.BudgetExceeded("stub abort")
_inst.run_pilot.side_effect = _rp.BudgetExceeded("stub abort")
_h.EvaluationHarness.return_value.__enter__.return_value = _inst
_h.EvaluationHarness.return_value.__exit__.return_value = False

sys.argv = [
    "run_pilot.py",
    "--db", r"{db}", "--fresh-db",
    "--condition", "full",
    "--n-runs", "1",
    "--condition-budget-hours", "1",
]
sys.exit(_rp.main() or 0)
""")
    result = _run_wrapper(wrapper)
    assert result.returncode == 7, (
        f"Expected exit 7 on watchdog abort, got {result.returncode}. "
        f"stderr:\n{result.stderr[:2000]}\nstdout:\n{result.stdout[:2000]}"
    )


def test_run_pilot_exits_4_on_empty_records_no_abort(tmp_path):
    """ext12 #115 e2e: harness returns empty records list with NO
    watchdog abort recorded; expect exit 4 (zero-records).
    """
    db = tmp_path / "pilot_empty.duckdb"
    wrapper = tmp_path / "wrap_run_pilot_empty.py"
    _write_wrapper(wrapper, f"""
from unittest.mock import MagicMock
import sys

import tests.evaluation.harness as _h
import scripts._eval_registry_lock as _lock
import scripts.run_pilot as _rp

_h.EvaluationHarness = MagicMock()
_h.HarnessConfig = MagicMock()
_lock.acquire_runner_lock = lambda **kw: MagicMock()

_inst = MagicMock()
_inst.prepare.return_value = "snap-id"
_inst.run_condition.return_value = []
_inst.run_pilot.return_value = []
_h.EvaluationHarness.return_value.__enter__.return_value = _inst
_h.EvaluationHarness.return_value.__exit__.return_value = False

sys.argv = [
    "run_pilot.py",
    "--db", r"{db}", "--fresh-db",
    "--condition", "full",
    "--n-runs", "1",
    "--condition-budget-hours", "1",
]
sys.exit(_rp.main() or 0)
""")
    result = _run_wrapper(wrapper)
    assert result.returncode == 4, (
        f"Expected exit 4 on empty-records, got {result.returncode}. "
        f"stderr:\n{result.stderr[:2000]}"
    )


def test_run_longitudinal_exits_3_on_per_task_watchdog(tmp_path):
    """ext12 #119 e2e: mock pipeline.invoke to raise BudgetExceeded;
    assert the longitudinal runner exits with code 3 (per-task abort).
    Uses --n-tasks 1 + heavy mocking to keep wall-clock under 5s.
    """
    db = tmp_path / "longitudinal.duckdb"
    wrapper = tmp_path / "wrap_run_longitudinal_abort.py"
    _write_wrapper(wrapper, f"""
from unittest.mock import MagicMock, patch
import sys

# Stub out EVERY heavy import the runner does inside main(), BEFORE
# main() runs. scripts._budget_watchdog is imported inside main() too
# so we can raise BudgetExceeded from our mocked pipeline.invoke.

import src.orchestrator.pipeline as _pipeline_mod
import src.orchestrator.tool_loader as _tool_loader_mod
import src.registry.evaluation_store as _eval_store_mod
import src.registry.metrics as _metrics_mod
import src.registry.store as _store_mod
import tests.evaluation.harness as _h
import tests.evaluation.snapshots as _snaps
import scripts._eval_registry_lock as _lock
import scripts._budget_watchdog as _bw

# Pipeline that raises BudgetExceeded on invoke.
_compiled = MagicMock()
_compiled.invoke.side_effect = _bw.BudgetExceeded("stub watchdog")
_graph = MagicMock()
_graph.compile.return_value = _compiled
_pipeline_mod.create_pipeline = lambda *a, **kw: _graph

# Everything else: permissive MagicMocks so the runner's prologue
# (snapshot take + restore, registry seed, metrics store) runs.
_tool_loader_mod.ToolLoader = MagicMock()
_eval_store_mod.EvaluationStore = MagicMock()
_metrics_mod.MetricsStore = MagicMock()
_store_mod.SkillRegistry = MagicMock()
_store_mod.seed_registry = lambda r: None
_snaps.take_clean_snapshot = lambda *a, **kw: "snap-id"
_snaps.restore_clean_snapshot = lambda *a, **kw: "snap-id"
_h._extract_result = lambda *a, **kw: {{
    "gate_passed": False, "output_in_range": False,
    "failure_reason": "stub", "attempt_count": 0,
}}
_lock.acquire_runner_lock = lambda **kw: MagicMock()

# Also block the chroma rmtree path — its safety net won't trigger
# because CHROMA_DIR lives under data/eval, but we may as well stub.
import shutil
_orig_rmtree = shutil.rmtree
shutil.rmtree = lambda *a, **kw: None

# Stub flush_task_cycle_state so we don't depend on DB state.
import tests.evaluation.cycle_flush as _cf
_cf.flush_task_cycle_state = lambda **kw: _cf.FlushResult(invocations_ok=True, utility_scores_ok=True, events_ok=True)

import scripts.run_longitudinal as _rl
sys.argv = [
    "run_longitudinal.py",
    "--n-tasks", "1",
    "--db", r"{db}",
    "--per-task-budget-seconds", "60",
    "--campaign-budget-hours", "1",
]
sys.exit(_rl.main() or 0)
""")
    result = _run_wrapper(wrapper, timeout=60)
    assert result.returncode == 3, (
        f"Expected exit 3 on per-task watchdog abort, got "
        f"{result.returncode}. stderr:\n{result.stderr[:3000]}\n"
        f"stdout:\n{result.stdout[:2000]}"
    )


def test_run_pilot_exits_0_on_clean_success(tmp_path):
    """ext12 #115 e2e: harness returns at least one record with no abort;
    expect clean exit 0 through sys.exit(main() or 0)."""
    db = tmp_path / "pilot_ok.duckdb"
    wrapper = tmp_path / "wrap_run_pilot_ok.py"
    _write_wrapper(wrapper, f"""
from unittest.mock import MagicMock
import sys

import tests.evaluation.harness as _h
import scripts._eval_registry_lock as _lock
import scripts.run_pilot as _rp

_h.EvaluationHarness = MagicMock()
_h.HarnessConfig = MagicMock()
_lock.acquire_runner_lock = lambda **kw: MagicMock()

# A stub EvaluationRecord with the attributes run_pilot's summary loop reads.
_rec = MagicMock()
_rec.success = True
_rec.gate_passed = True
_rec.output_in_range = True
_rec.task_id = "T01"
_rec.failure_reason = None
_rec.capability_type = "mcp_tool"
_rec.verification_tier_reached = 3

_inst = MagicMock()
_inst.prepare.return_value = "snap-id"
_inst.run_pilot.return_value = [_rec]
_inst.run_condition.return_value = [_rec]
_h.EvaluationHarness.return_value.__enter__.return_value = _inst
_h.EvaluationHarness.return_value.__exit__.return_value = False

sys.argv = [
    "run_pilot.py",
    "--db", r"{db}", "--fresh-db",
    "--condition", "full",
    "--n-runs", "1",
    "--condition-budget-hours", "1",
]
sys.exit(_rp.main() or 0)
""")
    result = _run_wrapper(wrapper)
    assert result.returncode == 0, (
        f"Expected clean exit 0, got {result.returncode}. "
        f"stderr:\n{result.stderr[:2000]}"
    )
