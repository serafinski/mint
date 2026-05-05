"""Subprocess-level exit-code tests for ``run_longitudinal.py``."""

from __future__ import annotations

from tests.unit._script_subprocess import (
    run_project_script,
    run_wrapper,
    seed_evaluation_runs,
    write_wrapper,
)


def test_run_longitudinal_help_exposes_watchdog_flags():
    result = run_project_script("run_longitudinal.py", "--help", timeout=10)

    assert result.returncode == 0
    assert "--per-task-budget-seconds" in result.stdout
    assert "--campaign-budget-hours" in result.stdout


def test_run_longitudinal_refuses_existing_nonempty_db(tmp_path):
    """A longitudinal campaign should not append unless explicitly requested."""
    db = tmp_path / "longitudinal.duckdb"
    seed_evaluation_runs(
        db,
        rows=[{"run_id": "existing_1", "condition": "longitudinal"}],
    )

    result = run_project_script(
        "run_longitudinal.py",
        "--db",
        str(db),
        "--n-tasks",
        "1",
        timeout=15,
    )

    assert result.returncode == 2, (
        f"Expected exit 2 for non-empty DB, got {result.returncode}. "
        f"stderr:\n{result.stderr[:500]}"
    )


def test_run_longitudinal_exits_3_on_per_task_watchdog(tmp_path):
    """A per-task watchdog abort should surface as process exit code 3."""
    db = tmp_path / "longitudinal.duckdb"
    wrapper = tmp_path / "wrap_run_longitudinal_abort.py"
    write_wrapper(wrapper, f"""
from unittest.mock import MagicMock
import sys

import src.orchestrator.pipeline as _pipeline_mod
import src.orchestrator.tool_loader as _tool_loader_mod
import src.registry.evaluation_store as _eval_store_mod
import src.registry.metrics as _metrics_mod
import src.registry.store as _store_mod
import tests.evaluation.harness as _h
import tests.evaluation.snapshots as _snaps
import scripts._eval_registry_lock as _lock
import scripts._budget_watchdog as _bw

_compiled = MagicMock()
_compiled.invoke.side_effect = _bw.BudgetExceeded("stub watchdog")
_graph = MagicMock()
_graph.compile.return_value = _compiled
_pipeline_mod.create_pipeline = lambda *a, **kw: _graph

_tool_loader_mod.ToolLoader = MagicMock()
_eval_store_mod.EvaluationStore = MagicMock()
_metrics_mod.MetricsStore = MagicMock()
_store_mod.SkillRegistry = MagicMock()
_store_mod.seed_registry = lambda registry: None
_snaps.take_clean_snapshot = lambda *a, **kw: "snap-id"
_snaps.restore_clean_snapshot = lambda *a, **kw: "snap-id"
_h._extract_result = lambda *a, **kw: {{
    "gate_passed": False,
    "output_in_range": False,
    "failure_reason": "stub",
    "attempt_count": 0,
}}
_lock.acquire_runner_lock = lambda **kw: MagicMock()

import shutil
shutil.rmtree = lambda *a, **kw: None

import tests.evaluation.cycle_flush as _cf
_cf.flush_task_cycle_state = lambda **kw: _cf.FlushResult(
    invocations_ok=True,
    utility_scores_ok=True,
    events_ok=True,
)

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

    result = run_wrapper(wrapper, timeout=60)

    assert result.returncode == 3, (
        f"Expected exit 3 on per-task watchdog abort, got {result.returncode}. "
        f"stderr:\n{result.stderr[:3000]}\nstdout:\n{result.stdout[:2000]}"
    )


def test_run_longitudinal_exits_5_on_partial_flush(tmp_path):
    """A partial telemetry flush should surface as process exit code 5."""
    db = tmp_path / "longitudinal.duckdb"
    wrapper = tmp_path / "wrap_partial_flush.py"
    write_wrapper(wrapper, f"""
from unittest.mock import MagicMock
import sys

import src.orchestrator.pipeline as _pipeline_mod
import src.orchestrator.tool_loader as _tool_loader_mod
import src.registry.evaluation_store as _eval_store_mod
import src.registry.metrics as _metrics_mod
import src.registry.store as _store_mod
import tests.evaluation.harness as _h
import tests.evaluation.snapshots as _snaps
import scripts._eval_registry_lock as _lock

_compiled = MagicMock()
_compiled.invoke.return_value = MagicMock()
_graph = MagicMock()
_graph.compile.return_value = _compiled
_pipeline_mod.create_pipeline = lambda *a, **kw: _graph

_tool_loader_mod.ToolLoader = MagicMock()
_eval_store_mod.EvaluationStore = MagicMock()
_metrics_mod.MetricsStore = MagicMock()
_store_mod.SkillRegistry = MagicMock()
_store_mod.seed_registry = lambda registry: None
_snaps.take_clean_snapshot = lambda *a, **kw: "snap-id"
_snaps.restore_clean_snapshot = lambda *a, **kw: "snap-id"
_h._extract_result = lambda *a, **kw: {{
    "gate_passed": True,
    "output_in_range": True,
    "failure_reason": "none",
    "attempt_count": 1,
    "capability_type": "mcp_tool",
    "verification_tier_reached": 3,
}}
_lock.acquire_runner_lock = lambda **kw: MagicMock()

import shutil
shutil.rmtree = lambda *a, **kw: None

import tests.evaluation.cycle_flush as _cf
_cf.flush_task_cycle_state = lambda **kw: _cf.FlushResult(
    invocations_ok=True,
    utility_scores_ok=False,
    events_ok=True,
    errors=["cycle_utility_scores: simulated transient"],
)

import src.orchestrator.tracing as _tracing
import contextlib
_tracing.trace_context = lambda **kw: contextlib.nullcontext()

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

    result = run_wrapper(wrapper, timeout=60)

    assert result.returncode == 5, (
        f"Expected exit 5 on partial flush, got {result.returncode}. "
        f"stderr:\n{result.stderr[-2000:]}"
    )
    assert "partial failures" in result.stderr.lower()
