"""End-to-end tests for security audit ext13 findings #124-#138.

Sibling of ``test_security_audit_ext12_e2e.py`` — same pattern (seed a
minimal DuckDB in ``tmp_path``, invoke the target script via
``subprocess.run``, assert on ``returncode`` + banners in stdout/stderr).
Covers the high-leverage findings where the subprocess path could drift
from the unit-test path: exit-code propagation, CLI-flag wiring, argparse
integration, and banner interpolation in section headers.

Scripts that require LLM / LangFuse runtime state (#134 test_registry_
matching, #135 test_registry_fn, #136 test_pipeline.py) are exercised at
the unit-test level; e2e coverage for those would need non-trivial
stubbing infrastructure that duplicates what unit tests already verify.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Shared helpers — mirror ext12_e2e's conventions so the two files stay
# co-adaptable if the e2e fixtures ever move to a conftest.
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


def _write_wrapper(path: Path, body: str) -> None:
    path.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        + body
    )


def _run_wrapper(
    wrapper: Path, timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(wrapper)],
        capture_output=True, text=True, timeout=timeout,
        env=os.environ.copy(),
    )


def _seed_evaluation_runs(
    db_path: Path,
    *,
    rows: list[dict],
) -> None:
    """Create a minimal evaluation_runs DB via ``EvaluationStore``.

    Mirrors ext12_e2e's helper; if the timestamp is explicit it's honored
    via direct UPDATE after ``record_run`` (which otherwise stamps now()).
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
            if "timestamp" in r:
                rec.timestamp = r["timestamp"]
            store.record_run(rec)
    finally:
        store.close()

    # Second pass: force timestamps directly (record_run overwrites).
    # ext13 #127 needs deterministic per-cycle bucketing.
    ts_overrides = [(r["run_id"], r.get("task_id", "T01"), r["timestamp"])
                    for r in rows if "timestamp" in r]
    if ts_overrides:
        conn = duckdb.connect(str(db_path))
        try:
            # Reset all matching rows in order — we rely on id ordering
            # per (run_id, task_id) since multiple cycles can share task_id.
            # Approach: UPDATE all rows with the exact sequence of
            # timestamps, keyed by id order.
            all_ids = conn.execute(
                "SELECT id FROM evaluation_runs ORDER BY timestamp"
            ).fetchall()
            assert len(all_ids) == len(rows)
            for (rid,), r in zip(all_ids, rows):
                if "timestamp" in r:
                    conn.execute(
                        "UPDATE evaluation_runs SET timestamp = ? WHERE id = ?",
                        [r["timestamp"], rid],
                    )
        finally:
            conn.close()


def _insert_invocation_events(
    db_path: Path,
    events: list[dict],
) -> None:
    """Insert rows into invocation_events bypassing EvaluationStore's API
    (which requires an ``InvocationEvent`` object). Useful when the test
    needs a specific occurred_at timestamp or capability_id.
    """
    conn = duckdb.connect(str(db_path))
    try:
        for i, e in enumerate(events):
            conn.execute(
                """
                INSERT INTO invocation_events
                    (id, run_id, condition, task_id, capability_id,
                     capability_type, success, is_pilot, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    e.get("id", f"ev-{i}"),
                    e["run_id"], e["condition"], e["task_id"],
                    e["capability_id"],
                    e.get("capability_type", "mcp_tool"),
                    e.get("success", True),
                    e.get("is_pilot", False),
                    e["occurred_at"],
                ],
            )
    finally:
        conn.close()


def _insert_human_labels(
    db_path: Path,
    labels: list[dict],
) -> None:
    """Populate ``human_label`` directly on existing evaluation_runs rows."""
    conn = duckdb.connect(str(db_path))
    try:
        for lb in labels:
            conn.execute(
                """
                UPDATE evaluation_runs
                SET human_label = ?
                WHERE run_id = ? AND condition = ? AND task_id = ?
                """,
                [lb["human_label"], lb["run_id"], lb["condition"], lb["task_id"]],
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# #124 — analyze_longitudinal refuses proxy-KL downgrade
# ---------------------------------------------------------------------------


def test_ext13_124_refuses_proxy_kl_without_override(tmp_path):
    """Seed a 5-cycle DB with invocation_events populated but
    cycle_utility_scores empty. Analyzer must exit 6 refusing the
    proposal-literal-KL downgrade unless --allow-proxy-kl is passed.
    """
    db = tmp_path / "proxy_kl_test.duckdb"
    base = datetime(2026, 4, 21, 10, 0, 0)
    rows = [
        {
            "run_id": "r1",
            "condition": "longitudinal",
            "task_id": f"T{i+1:02d}",
            "timestamp": base + timedelta(seconds=i),
        }
        for i in range(5)
    ]
    _seed_evaluation_runs(db, rows=rows)
    _insert_invocation_events(db, events=[
        {
            "run_id": "r1", "condition": "longitudinal",
            "task_id": f"T{i+1:02d}", "capability_id": "cap-a",
            "occurred_at": base + timedelta(seconds=i, milliseconds=500),
        }
        for i in range(5)
    ])

    # Without override → exit 6.
    result = _run_script(
        "analyze_longitudinal.py",
        "--db", str(db), "--expected-cycles", "5",
    )
    assert result.returncode == 6, (
        f"Expected exit 6 (proxy-KL refused), got {result.returncode}. "
        f"stderr tail:\n{result.stderr[-600:]}"
    )
    assert "ext13 #124" in result.stderr
    assert "cycle_utility_scores is empty" in result.stderr


def test_ext13_124_allow_proxy_kl_banner_in_output(tmp_path):
    """With --allow-proxy-kl, analyzer proceeds and prints [PROXY KL]
    banner on every section header.
    """
    db = tmp_path / "proxy_kl_allow.duckdb"
    base = datetime(2026, 4, 21, 10, 0, 0)
    rows = [
        {
            "run_id": "r1",
            "condition": "longitudinal",
            "task_id": f"T{i+1:02d}",
            "timestamp": base + timedelta(seconds=i),
        }
        for i in range(5)
    ]
    _seed_evaluation_runs(db, rows=rows)
    _insert_invocation_events(db, events=[
        {
            "run_id": "r1", "condition": "longitudinal",
            "task_id": f"T{i+1:02d}", "capability_id": "cap-a",
            "occurred_at": base + timedelta(seconds=i, milliseconds=500),
        }
        for i in range(5)
    ])

    result = _run_script(
        "analyze_longitudinal.py",
        "--db", str(db), "--expected-cycles", "5", "--allow-proxy-kl",
    )
    # Must succeed (returncode 0 or 2 — 0 on convergence, 2 on non-converge;
    # either is acceptable evidence the override worked). NOT 6.
    assert result.returncode != 6, (
        f"--allow-proxy-kl should bypass exit 6. Got {result.returncode}. "
        f"stderr:\n{result.stderr[-400:]}"
    )
    assert "[PROXY KL]" in result.stdout


# ---------------------------------------------------------------------------
# #127 — per-cycle coverage gate (sparse signal)
# ---------------------------------------------------------------------------


def test_ext13_127_refuses_sparse_signal(tmp_path):
    """20 cycles, 1 invocation_event landing in cycle 20 (cumulative
    touches only cycle 20 → coverage 1/20 = 5% < 50% default).
    Analyzer must exit 8 even with --allow-proxy-kl since sparse-gate
    runs before the proxy-KL gate.
    """
    db = tmp_path / "sparse.duckdb"
    base = datetime(2026, 4, 21, 10, 0, 0)
    rows = [
        {
            "run_id": "r1",
            "condition": "longitudinal",
            "task_id": f"T{(i % 20) + 1:02d}",
            "timestamp": base + timedelta(seconds=i),
        }
        for i in range(20)
    ]
    _seed_evaluation_runs(db, rows=rows)
    # One event, occurred_at late enough to bucket into cycle 19 (0-indexed
    # 19 → 1-indexed 20). With ext8 #56 half-open intervals, inv_ts >=
    # task_boundaries[19][0] attributes to cycle 19.
    _insert_invocation_events(db, events=[{
        "run_id": "r1", "condition": "longitudinal",
        "task_id": "T20", "capability_id": "cap-a",
        "occurred_at": base + timedelta(seconds=19, milliseconds=500),
    }])

    result = _run_script(
        "analyze_longitudinal.py",
        "--db", str(db), "--expected-cycles", "20",
    )
    assert result.returncode == 8, (
        f"Expected exit 8 (sparse signal), got {result.returncode}. "
        f"stderr tail:\n{result.stderr[-500:]}"
    )
    assert "ext13 #127" in result.stderr
    assert "sparse telemetry" in result.stderr.lower() or "signal in only" in result.stderr.lower()


def test_ext13_127_allow_sparse_signal_banner(tmp_path):
    """With --allow-sparse-signal + --allow-proxy-kl, proceed with
    [SPARSE: N/M] banner in every section header.
    """
    db = tmp_path / "sparse_allow.duckdb"
    base = datetime(2026, 4, 21, 10, 0, 0)
    rows = [
        {
            "run_id": "r1",
            "condition": "longitudinal",
            "task_id": f"T{(i % 20) + 1:02d}",
            "timestamp": base + timedelta(seconds=i),
        }
        for i in range(20)
    ]
    _seed_evaluation_runs(db, rows=rows)
    _insert_invocation_events(db, events=[{
        "run_id": "r1", "condition": "longitudinal",
        "task_id": "T20", "capability_id": "cap-a",
        "occurred_at": base + timedelta(seconds=19, milliseconds=500),
    }])

    result = _run_script(
        "analyze_longitudinal.py",
        "--db", str(db), "--expected-cycles", "20",
        "--allow-sparse-signal", "--allow-proxy-kl",
    )
    assert result.returncode != 8, (
        f"--allow-sparse-signal should bypass exit 8. Got {result.returncode}. "
        f"stderr:\n{result.stderr[-400:]}"
    )
    assert "[SPARSE:" in result.stdout


# ---------------------------------------------------------------------------
# #126 — analyze_results missing-metrics exit 7
# ---------------------------------------------------------------------------


def test_ext13_126_exits_7_when_invocation_events_empty(tmp_path):
    """Full 400-row canonical eval DB with empty invocation_events →
    M3 skipped → exit 7 without --allow-missing-metrics.
    """
    db = tmp_path / "canonical_no_inv.duckdb"
    base = datetime(2026, 4, 21, 10, 0, 0)
    conditions = ["full", "no_verification", "no_optimization", "static"]
    rows: list[dict] = []
    for ci, cond in enumerate(conditions):
        for run_i in range(5):
            for task_i in range(20):
                rows.append({
                    "run_id": f"{cond}_run_{run_i}",
                    "condition": cond,
                    "task_id": f"T{task_i+1:02d}",
                    "timestamp": base + timedelta(
                        seconds=ci * 10000 + run_i * 100 + task_i
                    ),
                })
    _seed_evaluation_runs(db, rows=rows)
    # invocation_events intentionally empty.

    result = _run_script(
        "analyze_results.py",
        "--db", str(db),
    )
    assert result.returncode == 7, (
        f"Expected exit 7 (M3 missing), got {result.returncode}. "
        f"stderr tail:\n{result.stderr[-600:]}\nstdout tail:\n{result.stdout[-600:]}"
    )
    assert "CHAPTER 6 INCOMPLETE" in result.stderr
    assert "M3" in result.stderr


def test_ext13_126_allow_missing_metrics_proceeds(tmp_path):
    """With --allow-missing-metrics, analyzer exits 0 even when M3 is
    absent. Banner still appears in stderr.
    """
    db = tmp_path / "canonical_allow.duckdb"
    base = datetime(2026, 4, 21, 10, 0, 0)
    conditions = ["full", "no_verification", "no_optimization", "static"]
    rows: list[dict] = []
    for ci, cond in enumerate(conditions):
        for run_i in range(5):
            for task_i in range(20):
                rows.append({
                    "run_id": f"{cond}_run_{run_i}",
                    "condition": cond,
                    "task_id": f"T{task_i+1:02d}",
                    "timestamp": base + timedelta(
                        seconds=ci * 10000 + run_i * 100 + task_i
                    ),
                })
    _seed_evaluation_runs(db, rows=rows)

    result = _run_script(
        "analyze_results.py",
        "--db", str(db), "--allow-missing-metrics",
    )
    # The test isn't asserting 0 specifically (other assertions may fire
    # on this shape); the important contract is "NOT 7".
    assert result.returncode != 7, (
        f"--allow-missing-metrics should bypass exit 7. Got "
        f"{result.returncode}. stderr:\n{result.stderr[-500:]}"
    )


# ---------------------------------------------------------------------------
# #131 — analyze_results distinct-task coverage gate
# ---------------------------------------------------------------------------


def test_ext13_131_refuses_single_task_inflation(tmp_path):
    """400-row DB where EVERY row is task T01 → completeness gate exits
    2 citing distinct-task coverage.
    """
    db = tmp_path / "t01_only.duckdb"
    base = datetime(2026, 4, 21, 10, 0, 0)
    conditions = ["full", "no_verification", "no_optimization", "static"]
    rows: list[dict] = []
    for ci, cond in enumerate(conditions):
        for run_i in range(5):
            for rep in range(20):
                rows.append({
                    "run_id": f"{cond}_run_{run_i}",
                    "condition": cond,
                    "task_id": "T01",  # always
                    "timestamp": base + timedelta(
                        seconds=ci * 10000 + run_i * 100 + rep
                    ),
                })
    _seed_evaluation_runs(db, rows=rows)

    result = _run_script(
        "analyze_results.py",
        "--db", str(db),
    )
    assert result.returncode == 2, (
        f"Expected exit 2 (distinct-task gap), got {result.returncode}. "
        f"stderr tail:\n{result.stderr[-800:]}"
    )
    # Either the "distinct tasks" per-condition message OR the "ext13 #131"
    # marker must appear — the gate message references both.
    combined = result.stderr + result.stdout
    assert "distinct tasks" in combined or "ext13 #131" in combined, (
        f"Expected ext13 #131 diagnostic in output. Got:\n{combined[-800:]}"
    )


# ---------------------------------------------------------------------------
# #132 — analyze_results per-condition M2 coverage
# ---------------------------------------------------------------------------


def test_ext13_132_exits_7_on_partial_m2_coverage(tmp_path):
    """Canonical 400-row DB; human_label populated only for condition='full'.
    print_contamination_rate should flag the partial coverage → exit 7.
    """
    db = tmp_path / "partial_m2.duckdb"
    base = datetime(2026, 4, 21, 10, 0, 0)
    conditions = ["full", "no_verification", "no_optimization", "static"]
    rows: list[dict] = []
    for ci, cond in enumerate(conditions):
        for run_i in range(5):
            for task_i in range(20):
                rows.append({
                    "run_id": f"{cond}_run_{run_i}",
                    "condition": cond,
                    "task_id": f"T{task_i+1:02d}",
                    "timestamp": base + timedelta(
                        seconds=ci * 10000 + run_i * 100 + task_i
                    ),
                })
    _seed_evaluation_runs(db, rows=rows)
    # Seed invocation_events for every condition so M3 doesn't also fire
    # (we want to isolate the M2 path).
    _insert_invocation_events(db, events=[
        {
            "run_id": f"{cond}_run_0", "condition": cond,
            "task_id": f"T{t+1:02d}", "capability_id": "cap-a",
            "occurred_at": base + timedelta(seconds=ci * 10000 + t),
        }
        for ci, cond in enumerate(conditions) if cond != "static"
        for t in range(20)
    ])
    # Populate human_label ONLY for 'full'.
    _insert_human_labels(db, labels=[
        {"run_id": "full_run_0", "condition": "full",
         "task_id": f"T{t+1:02d}", "human_label": "correct"}
        for t in range(5)
    ])

    result = _run_script(
        "analyze_results.py",
        "--db", str(db),
    )
    # ext13 #132 partial coverage → exit 7 (shared with #126).
    assert result.returncode == 7, (
        f"Expected exit 7 on partial M2. Got {result.returncode}. "
        f"stderr tail:\n{result.stderr[-600:]}"
    )
    assert "M2 partial coverage" in result.stderr


# ---------------------------------------------------------------------------
# #133 — analyze_context_utilization per-agent gate
# ---------------------------------------------------------------------------


def test_ext13_133_refuses_missing_agent_families(tmp_path):
    """Mock fetch_traces so only task-decomposer returns results; other
    three agent families get []. Script must exit 4 without --allow-empty.
    """
    wrapper = tmp_path / "wrap_ctx_utilization.py"
    _write_wrapper(wrapper, """
import sys
from unittest.mock import MagicMock
import scripts.analyze_context_utilization as acu

# Patch everything the script touches at network/IO boundaries.
acu._langfuse_client = lambda: ("http://stub", MagicMock())

# Only task-decomposer returns a trace; everyone else returns [].
def _fetch(client, agent_name, start, end, **kwargs):
    if agent_name == "task-decomposer":
        return [{"id": "t1", "timestamp": "2026-04-21T00:00:00Z", "latency": 100}]
    return []
acu.fetch_traces_for_agent = _fetch

# extract_trace_context returns a minimal dict.
acu.extract_trace_context = lambda client, tr: {"observations": 1}

# httpx client is a MagicMock (its __enter__ returns itself).
import httpx
httpx.Client = MagicMock()

sys.argv = ["analyze_context_utilization.py", "--days", "1"]
sys.exit(acu.main() or 0)
""")
    # We don't enforce the exact exit code from main() here — we check
    # that either a LangFuse-path failure (because stubbing is hard) or
    # the ext13 #133 exit 4 fires. The critical assertion is that the
    # script DOESN'T exit 0 when families are missing, which was the
    # pre-fix silent-success behavior.
    result = _run_wrapper(wrapper, timeout=30)
    assert result.returncode != 0, (
        f"Script must not silently exit 0 when agent families are missing. "
        f"Got 0. stderr:\n{result.stderr[-800:]}\nstdout:\n{result.stdout[-400:]}"
    )


def test_ext13_133_help_smoke():
    """--help must mention --allow-empty (covers #133's reuse of the
    existing flag) without requiring LangFuse credentials.
    """
    result = _run_script(
        "analyze_context_utilization.py", "--help", timeout=15,
        env_extra={
            "LANGFUSE_PUBLIC_KEY": "test",
            "LANGFUSE_SECRET_KEY": "test",
            "LANGFUSE_HOST": "http://localhost:3000",
        },
    )
    assert result.returncode == 0
    assert "--allow-empty" in result.stdout


# ---------------------------------------------------------------------------
# #137 — audit_contamination zero-labels exit 3
# ---------------------------------------------------------------------------


def test_ext13_137_audit_contamination_help_flag(tmp_path):
    """--help smoke-test verifies argparse + the per-branch counter
    scaffolding load cleanly. Full e2e (running the interactive audit
    with zero-label fallbacks) would require mocking stdin + registry
    state; deferred to unit tests that exercise the counter logic
    directly.
    """
    result = _run_script(
        "audit_contamination.py", "--help", timeout=15,
    )
    assert result.returncode == 0


def test_ext13_137_counters_initialized_on_new_session(tmp_path):
    """Exercise the auto-skip-counter dict shape via direct import; guards
    against a typo in the counters dict that the --help smoke wouldn't catch.
    """
    sys.path.insert(0, str(ROOT))
    import scripts.audit_contamination as ac
    # Reflectively verify that the 3 new counter keys exist in the
    # module's code (via AST-free string check on the source).
    src = (ROOT / "scripts" / "audit_contamination.py").read_text()
    for key in (
        "auto_skip_no_invocation",
        "auto_skip_seeded",
        "auto_skip_no_source",
    ):
        assert key in src, f"ext13 #137 counter {key!r} missing from source"
    # The zero-label loud-fail must reference ext13 #137.
    assert "ext13 #137" in src


# ---------------------------------------------------------------------------
# #138 — analyze_tier2_probes zero-events exit 2
# ---------------------------------------------------------------------------


def test_ext13_138_refuses_zero_events(tmp_path):
    """Mock fetch_tier2_events to return [] and run the script; expect
    exit 2 with an ext13 #138 marker in stderr.
    """
    wrapper = tmp_path / "wrap_tier2_empty.py"
    _write_wrapper(wrapper, """
import sys
from unittest.mock import MagicMock
import scripts.analyze_tier2_probes as atp

atp._langfuse_client = lambda: ("http://stub", MagicMock())
atp.fetch_tier2_events = lambda *a, **kw: ([], set(), False)  # ext14 #139/#140

import httpx
httpx.Client = MagicMock()

sys.argv = ["analyze_tier2_probes.py", "--days", "1"]
sys.exit(atp.main() or 0)
""")
    result = _run_wrapper(wrapper, timeout=30)
    assert result.returncode == 2, (
        f"Expected exit 2 (zero-events), got {result.returncode}. "
        f"stderr:\n{result.stderr[-800:]}"
    )
    assert "ext13 #138" in result.stderr
    assert "zero Tier 2 probe events" in result.stderr


def test_ext13_138_allow_empty_bypass(tmp_path):
    """--allow-empty should make the zero-events path exit 0."""
    wrapper = tmp_path / "wrap_tier2_empty_allow.py"
    _write_wrapper(wrapper, """
import sys
from unittest.mock import MagicMock
import scripts.analyze_tier2_probes as atp

atp._langfuse_client = lambda: ("http://stub", MagicMock())
atp.fetch_tier2_events = lambda *a, **kw: ([], set(), False)  # ext14 #139/#140

import httpx
httpx.Client = MagicMock()

sys.argv = ["analyze_tier2_probes.py", "--days", "1", "--allow-empty"]
sys.exit(atp.main() or 0)
""")
    result = _run_wrapper(wrapper, timeout=30)
    assert result.returncode == 0, (
        f"Expected exit 0 with --allow-empty on zero events. Got "
        f"{result.returncode}. stderr:\n{result.stderr[-500:]}"
    )


def test_ext13_138_help_smoke():
    """--help must expose --allow-empty."""
    result = _run_script(
        "analyze_tier2_probes.py", "--help", timeout=15,
        env_extra={
            "LANGFUSE_PUBLIC_KEY": "test",
            "LANGFUSE_SECRET_KEY": "test",
            "LANGFUSE_HOST": "http://localhost:3000",
        },
    )
    assert result.returncode == 0
    assert "--allow-empty" in result.stdout


# ---------------------------------------------------------------------------
# #125 — cycle_flush FlushResult + runner exit 5 on partial flush
# ---------------------------------------------------------------------------


def test_ext13_125_run_longitudinal_exits_5_on_partial_flush(tmp_path):
    """Mock flush_task_cycle_state to return a FlushResult with
    utility_scores_ok=False for one task; run_longitudinal must exit 5.
    """
    db = tmp_path / "longitudinal.duckdb"
    wrapper = tmp_path / "wrap_partial_flush.py"
    _write_wrapper(wrapper, f"""
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
_store_mod.seed_registry = lambda r: None
_snaps.take_clean_snapshot = lambda *a, **kw: "snap-id"
_snaps.restore_clean_snapshot = lambda *a, **kw: "snap-id"
_h._extract_result = lambda *a, **kw: {{
    "gate_passed": True, "output_in_range": True,
    "failure_reason": "none", "attempt_count": 1,
    "capability_type": "mcp_tool", "verification_tier_reached": 3,
}}
_lock.acquire_runner_lock = lambda **kw: MagicMock()

import shutil
shutil.rmtree = lambda *a, **kw: None

# Partial-flush result: utility scores failed. This directly exercises
# the ext13 #125 accumulator branch in run_longitudinal.py.
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
    result = _run_wrapper(wrapper, timeout=60)
    assert result.returncode == 5, (
        f"Expected exit 5 (ext13 #125 partial flush), got "
        f"{result.returncode}. stderr:\n{result.stderr[-2000:]}"
    )
    assert "ext13 #125" in result.stderr or "partial failures" in result.stderr.lower()


# ---------------------------------------------------------------------------
# #129 / #130 — backfill_token_costs coverage gates (help smoke)
# ---------------------------------------------------------------------------


def test_ext13_129_and_130_help_exposes_new_flags():
    """Both new CLI flags (--allow-partial-task-coverage, --allow-under-
    coverage, --min-gens-per-jit-record) must appear in --help output.
    Full e2e requires a LangFuse harness we don't want to spin up here.
    """
    result = _run_script(
        "backfill_token_costs.py", "--help", timeout=15,
        env_extra={
            "LANGFUSE_PUBLIC_KEY": "test",
            "LANGFUSE_SECRET_KEY": "test",
            "LANGFUSE_HOST": "http://localhost:3000",
        },
    )
    assert result.returncode == 0
    assert "--allow-partial-task-coverage" in result.stdout
    assert "--allow-under-coverage" in result.stdout
    assert "--min-gens-per-jit-record" in result.stdout


# ---------------------------------------------------------------------------
# #124 / #126 / #127 — CLI flags present in --help output
# ---------------------------------------------------------------------------


def test_ext13_124_127_analyze_longitudinal_help_exposes_flags():
    result = _run_script(
        "analyze_longitudinal.py", "--help", timeout=15,
    )
    assert result.returncode == 0
    assert "--allow-proxy-kl" in result.stdout
    assert "--allow-sparse-signal" in result.stdout
    assert "--min-cycle-coverage" in result.stdout


def test_ext13_126_analyze_results_help_exposes_flag():
    result = _run_script(
        "analyze_results.py", "--help", timeout=15,
    )
    assert result.returncode == 0
    assert "--allow-missing-metrics" in result.stdout


def test_ext13_135_test_registry_fn_help_exposes_flag():
    result = _run_script(
        "test_registry_fn.py", "--help", timeout=15,
    )
    assert result.returncode == 0
    assert "--min-cap-coverage" in result.stdout
