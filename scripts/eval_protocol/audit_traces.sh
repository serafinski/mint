#!/usr/bin/env bash
# Shared audit-request generator. Invoked by stage scripts to prepare a
# profile-specific trace audit request.
#
# Usage:
#   audit_traces.sh --stage <name> --profile <profile> --eval-dir <path>
#
# Profiles:
#   smoke                  — 3-task longitudinal smoke DB (Stage 0)
#   main_sample            — stratified sample from main eval (Stage 1)
#   disagreement           — rows where analyzer disagreed with SQL (Stage 2)
#   recurrence_sample      — stratified sample from recurrence eval (Stage 3)
#   longitudinal_sample    — tasks sampled from longitudinal run (Stage 4)
#   plausibility_flagged   — rows flagged INVESTIGATE by Stage 5

set -u

STAGE=""; PROFILE=""; EVAL_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --stage) STAGE="$2"; shift 2 ;;
        --profile) PROFILE="$2"; shift 2 ;;
        --eval-dir) EVAL_DIR="$2"; shift 2 ;;
        *) shift ;;
    esac
done

if [[ -z "$STAGE" || -z "$PROFILE" || -z "$EVAL_DIR" ]]; then
    echo "Usage: $0 --stage <name> --profile <profile> --eval-dir <path>"
    exit 2
fi

export EVAL_OUT="$EVAL_DIR"
export AUDIT_STAGE="$STAGE"
export AUDIT_PROFILE="$PROFILE"

uv run python - <<'PY' 2>&1
import duckdb, json, os, random, sys
from pathlib import Path

eval_out = Path(os.environ["EVAL_OUT"])
stage = os.environ["AUDIT_STAGE"]
profile = os.environ["AUDIT_PROFILE"]

random.seed(42)

# Real evaluation_runs schema used by the eval snapshots:
#   id, run_id, condition, task_id, capability_type, gate_passed, output_in_range,
#   attempt_latency_ms, failure_reason, langfuse_trace_id, timestamp, ...
# NO: expected_type, error_message, capability_name, cycle_id

# Load expected_type from TaskDefinition (not in DB).
try:
    from tests.evaluation.tasks import ALL_TASKS
    EXPECTED = {t.task_id: getattr(t, "expected_type", None) for t in ALL_TASKS}
except Exception:
    EXPECTED = {}

def _table_exists(conn, name):
    try:
        return bool(conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_name = ?", [name]
        ).fetchone())
    except Exception:
        return False

def _row_to_dict(r, bucket, check):
    """r: (run_id, task_id, condition, langfuse_trace_id, capability_type, failure_reason, attempt_latency_ms)"""
    run_id, task_id, cond, trace_id, cap_type, failure, latency = r
    exp = EXPECTED.get(task_id, "?")
    context = (
        f"Task {task_id} / cond {cond} / type {cap_type} (expected {exp}) / "
        f"latency {latency}ms"
    )
    if failure:
        context += f" / failure: {(failure or '')[:120]}"
    return {
        "audit_key": run_id,
        "langfuse_trace_id": trace_id,
        "context": f"[{bucket}] {context}",
        "check": check,
    }

_MAIN_COLS = (
    "run_id, task_id, condition, langfuse_trace_id, capability_type, "
    "failure_reason, attempt_latency_ms"
)

# Select DB based on profile
if profile == "smoke":
    db_path = "data/eval/longitudinal_smoke.duckdb"
elif profile == "longitudinal_sample":
    db_path = str(eval_out / "31_longitudinal.duckdb")
elif profile == "recurrence_sample":
    db_path = str(eval_out / "42_recurrence.duckdb")
else:
    db_path = str(eval_out / "12_metrics.duckdb")

if not os.path.exists(db_path):
    print(f"ERROR: DB not found for profile {profile}: {db_path}", file=sys.stderr)
    sys.exit(2)

c = duckdb.connect(db_path, read_only=True)
rows = []

if profile == "smoke":
    # Smoke DB: verify the 3 tasks actually ran real work and that cycle_events
    # (if any) correspond to real optimizer operations.
    # Sample the 3 task runs directly from evaluation_runs.
    runs = c.execute(f"""
        SELECT {_MAIN_COLS} FROM evaluation_runs
        WHERE NOT is_pilot AND langfuse_trace_id IS NOT NULL
        ORDER BY timestamp LIMIT 3
    """).fetchall()
    for r in runs:
        rows.append(_row_to_dict(r, bucket="smoke",
                                 check="Verify agent/tool did real work on this task. cycle_flush should have fired after."))
    # If cycle_events has entries, sample two.
    if _table_exists(c, "cycle_events"):
        evs = c.execute("""
            SELECT task_id, event_type, capability_id, run_id FROM cycle_events
            ORDER BY occurred_at LIMIT 2
        """).fetchall()
        # cycle_events has no langfuse_trace_id column — we join on run_id to find it.
        for task_id, ev_type, cap_id, run_id in evs:
            tr = c.execute(
                "SELECT langfuse_trace_id FROM evaluation_runs WHERE run_id = ? LIMIT 1",
                [run_id]
            ).fetchone()
            rows.append({
                "audit_key": f"smoke_event_{task_id}_{ev_type}_{(cap_id or '')[:20]}",
                "langfuse_trace_id": tr[0] if tr else None,
                "context": f"Pillar 3 event: {ev_type} on cap '{cap_id}' at task {task_id}",
                "check": "Verify this Pillar 3 operation actually produced executable content (not a no-op metadata stub).",
            })

elif profile == "main_sample":
    # Stratified sample: 1 happy per condition + 2 random failures + 2 AGENT successes
    cond_rows = c.execute(f"""
        WITH per_cond AS (
            SELECT *, row_number() OVER (PARTITION BY condition ORDER BY random()) AS rn
            FROM evaluation_runs
            WHERE NOT is_pilot AND gate_passed AND output_in_range
              AND langfuse_trace_id IS NOT NULL
        )
        SELECT {_MAIN_COLS} FROM per_cond WHERE rn = 1 ORDER BY condition
    """).fetchall()
    for r in cond_rows:
        rows.append(_row_to_dict(r, bucket="happy",
                                 check="Verify real execution and output matches task intent."))
    fails = c.execute(f"""
        SELECT {_MAIN_COLS} FROM evaluation_runs
        WHERE NOT is_pilot AND failure_reason IS NOT NULL
          AND langfuse_trace_id IS NOT NULL
        ORDER BY random() LIMIT 2
    """).fetchall()
    for r in fails:
        rows.append(_row_to_dict(r, bucket="failure",
                                 check="Is the failure in src/ (thesis bug) or eval scaffolding (noise)?"))
    agents = c.execute(f"""
        SELECT {_MAIN_COLS} FROM evaluation_runs
        WHERE NOT is_pilot AND condition='full' AND gate_passed AND output_in_range
          AND capability_type='agent' AND langfuse_trace_id IS NOT NULL
        ORDER BY random() LIMIT 2
    """).fetchall()
    for r in agents:
        rows.append(_row_to_dict(r, bucket="agent",
                                 check="Did the DeepAgent plan + execute >=3 tool calls (real agent work)?"))

elif profile == "disagreement":
    disagreement_file = eval_out / "22_disagreements.json"
    if not disagreement_file.exists():
        print("// INFO: no disagreement file — analyzer and SQL fully agreed", file=sys.stderr)
        print(json.dumps({"stage": stage, "profile": profile, "capture_dir": str(eval_out),
                          "db_path": db_path, "n_rows": 0, "rows": []}, indent=2))
        (eval_out / f"{stage}_audit_plan.json").write_text(json.dumps({"rows": []}, indent=2))
        (eval_out / f"{stage}_audit_request.md").write_text(
            f"# {stage} audit — no disagreement\n\nAnalyzer and SQL fully agreed; nothing to audit.\n"
            f"\nMark PASS directly if desired:\n\n    echo PASS > {eval_out}/{stage}.marker\n")
        sys.exit(0)
    disagree = json.loads(disagreement_file.read_text())
    for d in disagree.get("rows", []):
        rows.append({
            "audit_key": d.get("run_id", "unknown"),
            "langfuse_trace_id": d.get("langfuse_trace_id"),
            "context": f"Task {d.get('task_id')} / cond {d.get('condition')} — analyzer says {d.get('analyzer')}, SQL says {d.get('sql')}",
            "check": "Read the trace; which side describes what actually happened?",
        })

elif profile == "longitudinal_sample":
    # Sample 3 points along the task timeline: first, middle, last.
    task_order = [r[0] for r in c.execute("""
        SELECT task_id FROM cycle_utility_scores
        GROUP BY task_id ORDER BY MIN(occurred_at)
    """).fetchall()]
    if len(task_order) >= 3:
        picks = [task_order[0], task_order[len(task_order)//2], task_order[-1]]
    else:
        picks = task_order
    for tid in picks:
        tr = c.execute("""
            SELECT run_id, langfuse_trace_id FROM evaluation_runs
            WHERE NOT is_pilot AND task_id = ? AND langfuse_trace_id IS NOT NULL LIMIT 1
        """, [tid]).fetchone()
        if tr:
            rows.append({
                "audit_key": f"long_task_{tid}",
                "langfuse_trace_id": tr[1],
                "context": f"Longitudinal task {tid} (run_id={tr[0]})",
                "check": "Verify the task ran real work; sample Pillar 3 event details if cycle_events has entries for this task.",
            })
        if _table_exists(c, "cycle_events"):
            evs = c.execute("""
                SELECT event_type, capability_id, run_id FROM cycle_events
                WHERE task_id = ? ORDER BY random() LIMIT 1
            """, [tid]).fetchall()
            for ev_type, cap_id, run_id in evs:
                tr2 = c.execute(
                    "SELECT langfuse_trace_id FROM evaluation_runs WHERE run_id = ? LIMIT 1",
                    [run_id]
                ).fetchone()
                rows.append({
                    "audit_key": f"long_event_{tid}_{ev_type}_{(cap_id or '')[:20]}",
                    "langfuse_trace_id": tr2[0] if tr2 else None,
                    "context": f"Pillar 3 op: {ev_type} on '{cap_id}' at task {tid}",
                    "check": "Verify the Pillar 3 op produced executable content, not a metadata stub.",
                })

elif profile == "recurrence_sample":
    cond_rows = c.execute(f"""
        WITH per_cond AS (
            SELECT *,
                   row_number() OVER (
                       PARTITION BY condition
                       ORDER BY task_family_id, variant_id, timestamp
                   ) AS rn
            FROM evaluation_runs
            WHERE benchmark_track = 'recurrence'
              AND gate_passed
              AND output_in_range
              AND langfuse_trace_id IS NOT NULL
        )
        SELECT {_MAIN_COLS} FROM per_cond WHERE rn = 1 ORDER BY condition
    """).fetchall()
    for r in cond_rows:
        rows.append(_row_to_dict(
            r,
            bucket="recurrence_happy",
            check="Verify real execution on a recurrence variant and confirm the variant-specific task intent was respected.",
        ))

    agent_rows = c.execute(f"""
        SELECT {_MAIN_COLS}
        FROM evaluation_runs
        WHERE benchmark_track = 'recurrence'
          AND capability_type = 'agent'
          AND gate_passed
          AND output_in_range
          AND langfuse_trace_id IS NOT NULL
        ORDER BY timestamp
        LIMIT 1
    """).fetchall()
    for r in agent_rows:
        rows.append(_row_to_dict(
            r,
            bucket="recurrence_agent",
            check="Verify the seeded or generated agent did real multi-step work on the recurrence variant.",
        ))

    static_rows = c.execute(f"""
        SELECT {_MAIN_COLS}
        FROM evaluation_runs
        WHERE benchmark_track = 'recurrence'
          AND condition = 'static'
          AND gate_passed
          AND output_in_range
          AND langfuse_trace_id IS NOT NULL
        ORDER BY timestamp
        LIMIT 1
    """).fetchall()
    for r in static_rows:
        rows.append(_row_to_dict(
            r,
            bucket="recurrence_static",
            check="Verify the fixed-library static baseline executed real deterministic work rather than a metadata-only shortcut.",
        ))

elif profile == "plausibility_flagged":
    plaus = eval_out / "40_plausibility.json"
    if not plaus.exists():
        print(f"// ERROR: Stage 5 JSON missing: {plaus}", file=sys.stderr); sys.exit(2)
    ps = json.loads(plaus.read_text())
    flags = [f for f in ps.get("flags", []) if f["level"] == "INVESTIGATE"]
    for f in flags:
        code = f["code"]; msg = f["msg"]
        if code == "P4_dead_tasks_full":
            dead = [t for t, n, ok in ps.get("info", {}).get("per_task_full", []) if ok == 0]
            for t in dead[:3]:
                found = c.execute(f"""
                    SELECT {_MAIN_COLS} FROM evaluation_runs
                    WHERE NOT is_pilot AND task_id = ? AND condition='full'
                      AND langfuse_trace_id IS NOT NULL LIMIT 1
                """, [t]).fetchall()
                for r in found:
                    rows.append(_row_to_dict(
                        r,
                        bucket=code,
                        check=(
                            f"Why did {t} have zero successes in full-condition "
                            f"runs? Stage 5 flag: {msg}"
                        ),
                    ))
        elif code == "P5_dominant_error":
            top_err = ps.get("info", {}).get("top_errors", [])
            if top_err:
                top_msg = top_err[0][0]
                found = c.execute(f"""
                    SELECT {_MAIN_COLS} FROM evaluation_runs
                    WHERE NOT is_pilot AND failure_reason = ?
                      AND langfuse_trace_id IS NOT NULL LIMIT 2
                """, [top_msg]).fetchall()
                for r in found:
                    rows.append(_row_to_dict(r, bucket=code, check="src/ bug or eval noise?"))
        elif code == "P9_dominant_cap":
            top_cap = (ps.get("info", {}).get("top_5_capabilities") or [[None]])[0][0]
            if top_cap:
                found = c.execute(f"""
                    SELECT {_MAIN_COLS} FROM evaluation_runs er
                    JOIN invocation_events ie ON er.run_id = ie.run_id
                    WHERE NOT er.is_pilot AND ie.capability_id = ?
                      AND er.langfuse_trace_id IS NOT NULL LIMIT 2
                """, [top_cap]).fetchall()
                for r in found:
                    rows.append(_row_to_dict(r, bucket=code, check=f"'{top_cap}' dominates invocations — appropriate?"))
        else:
            rows.append({
                "audit_key": code,
                "langfuse_trace_id": None,
                "context": f"Stage 5 flag {code}: {msg}",
                "check": "Inspect manually — this flag does not map to a specific row selector.",
            })

else:
    print(f"ERROR: unknown profile {profile}", file=sys.stderr); sys.exit(2)

plan = {
    "stage": stage, "profile": profile, "capture_dir": str(eval_out),
    "db_path": db_path, "n_rows": len(rows), "rows": rows,
}
(eval_out / f"{stage}_audit_plan.json").write_text(json.dumps(plan, indent=2, default=str))

# Write MD request
lines = [
    f"# LangFuse trace audit — {stage} ({profile})",
    "",
    "**Paste this entire file into a review session with LangFuse MCP tools.**",
    "",
    f"- Capture dir: `{eval_out}`",
    f"- DB: `{db_path}`",
    f"- Rows to audit: {len(rows)}",
    "",
    "## Task",
    "",
    "For each row, use `mcp__langfuse__fetch_trace` (and `fetch_observations` / `fetch_observation` as needed) to pull the trace and verify whether the agent did real, task-appropriate work.",
    "",
    "Return a verdict table in this EXACT format so the stage script can auto-advance:",
    "",
    "```",
    "| audit_key | verdict | reason |",
    "|---|---|---|",
    "| <key1> | OK | real sandbox_execute call, output shape matches contract |",
    "| <key2> | SUSPICIOUS | one tool call only, looks like short-circuit |",
    "| <key3> | FAIL | agent fabricated output, no data access in trace |",
    "```",
    "",
    "**Verdict meanings:**",
    "- `OK` — real execution, behavior consistent with task intent.",
    "- `SUSPICIOUS` — ran but something is off. Not disqualifying; flag in Chapter 6 caveats.",
    "- `FAIL` — agent did not do real work. DB label cannot be trusted for this row.",
    "",
    "**At the end, include this summary line EXACTLY (the stage script greps for it):**",
    "",
    "```",
    "AUDIT SUMMARY: <N> OK / <M> SUSPICIOUS / <K> FAIL",
    "```",
    "",
    "## Rows to audit",
    "",
]
for i, row in enumerate(rows, 1):
    lines.append(f"### Row {i} — `{row['audit_key']}`")
    lines.append("")
    tid = row.get("langfuse_trace_id")
    lines.append(f"- langfuse_trace_id: `{tid or '(none — fetch by tag/session via MCP)'}`")
    lines.append(f"- Context: {row['context']}")
    lines.append(f"- Check: {row['check']}")
    lines.append("")

(eval_out / f"{stage}_audit_request.md").write_text("\n".join(lines))
print(json.dumps({"ok": True, "n_rows": len(rows),
                  "request": str(eval_out / f"{stage}_audit_request.md")}, indent=2))
PY

RC=$?
if [[ $RC -eq 0 ]]; then
    echo "Audit request written: $EVAL_DIR/${STAGE}_audit_request.md"
fi
exit $RC
