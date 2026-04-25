#!/usr/bin/env bash
# Stage 4 — Longitudinal + M6 + audit checkpoint.
#
# State machine:
#   (no marker) --launch--> LAUNCHED
#     --pid exits, six gates pass--> SHAPE_OK (audit on sampled cycles)
#     --verdicts--> PASS | FAIL
#
# Usage:
#     scripts/eval_protocol/stage4_longitudinal.sh <path-to-eval-dir>
#     EXPECTED_CYCLES=60 FLOOR_PER_CYCLE=8 scripts/eval_protocol/stage4_longitudinal.sh <path>

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

resolve_eval_out "stage4" "${1:-}" || exit $?
require_prior_stage "stage1.marker" "stage4" || exit $?

MARKER="stage4.marker"
PID_FILE="$EVAL_OUT/30_longitudinal.pid"
LIVE_DB="data/eval/longitudinal.duckdb"

export EXPECTED_CYCLES="${EXPECTED_CYCLES:-40}"
export FLOOR_PER_CYCLE="${FLOOR_PER_CYCLE:-8}"

# ---- State detection ----
if [[ -f "$EVAL_OUT/$MARKER" ]]; then
    STATUS=$(head -1 "$EVAL_OUT/$MARKER")
    case "$STATUS" in
        PASS) banner "Stage 4 already PASS."; exit 0 ;;
        FAIL) banner "Stage 4 previously FAILED — delete marker to retry."; exit 1 ;;
        SHAPE_OK)
            banner "Stage 4 — shape OK, checking audit verdicts"
            check_audit_verdicts "stage4"; RC=$?
            case $RC in
                0) write_marker "$MARKER" "PASS" "shape + audit clean"
                   echo "STAGE 4: PASS. Next: scripts/eval_protocol/stage5_plausibility.sh $EVAL_OUT"
                   exit 0 ;;
                1) write_marker "$MARKER" "FAIL" "audit reported problems"; exit 1 ;;
                2) print_audit_handoff "stage4" "longitudinal_sample"; exit 0 ;;
            esac ;;
        LAUNCHED)
            if [[ -f "$PID_FILE" ]] && ps -p "$(cat "$PID_FILE")" > /dev/null 2>&1; then
                banner "Stage 4 — longitudinal still running (PID $(cat "$PID_FILE"))"
                tail -20 "$EVAL_OUT/30_longitudinal.log" 2>/dev/null
                exit 0
            fi
            banner "Stage 4 — longitudinal finished, verifying"
            ;;
    esac
fi

# ---- Launch phase ----
if [[ ! -f "$EVAL_OUT/$MARKER" ]]; then
    snapshot_git_state "30_stage4_launch"
    if [[ -f "$LIVE_DB" ]]; then
        echo "ERROR: $LIVE_DB already exists (finding #175). Delete explicitly: rm -f $LIVE_DB"
        exit 1
    fi
    echo "Launching longitudinal with EXPECTED_CYCLES=$EXPECTED_CYCLES."
    read -p "Continue? [y/N] " -n 1 -r; echo
    [[ ! $REPLY =~ ^[Yy]$ ]] && { echo "Aborted."; exit 0; }

    nohup caffeinate -i -s uv run scripts/run_longitudinal.py \
        --db "$LIVE_DB" --n-tasks "$EXPECTED_CYCLES" --fresh-db \
        > "$EVAL_OUT/30_longitudinal.log" 2>&1 &
    LPID=$!
    echo "$LPID" > "$PID_FILE"
    echo "Started PID $LPID, n_tasks=$EXPECTED_CYCLES at $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        > "$EVAL_OUT/30_longitudinal_start.txt"

    write_marker "$MARKER" "LAUNCHED" "longitudinal PID $LPID, tasks=$EXPECTED_CYCLES"
    echo "STAGE 4 LAUNCHED. Re-run this script when it finishes."
    exit 0
fi

# ---- Verify phase ----
snapshot_git_state "30_stage4_verify"

[[ ! -f "$LIVE_DB" ]] && { write_marker "$MARKER" "FAIL" "DB missing"; echo "STAGE 4: FAIL — $LIVE_DB not found."; exit 1; }

cp "$LIVE_DB" "$EVAL_OUT/31_longitudinal.duckdb"
echo "Finished at $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$EVAL_OUT/31_longitudinal_status.txt"
tail -50 "$EVAL_OUT/30_longitudinal.log" >> "$EVAL_OUT/31_longitudinal_status.txt"

banner "Stage 4 — raw-window completeness checks + scipy KL cross-check"
uv run python - <<'PY' 2>&1 | tee "$EVAL_OUT/32_stage4_gate.json"
from __future__ import annotations

import duckdb
import json
import os
import sys
from scipy.stats import entropy

eval_out = os.environ["EVAL_OUT"]
expected_cycles = int(os.environ["EXPECTED_CYCLES"])
floor = int(os.environ["FLOOR_PER_CYCLE"])
c = duckdb.connect(os.path.join(eval_out, "31_longitudinal.duckdb"), read_only=True)
out = {"expected_cycles": expected_cycles, "floor_per_cycle": floor}; problems = []

run_rows = c.execute("""
    SELECT run_id, MAX(timestamp) AS last_ts
    FROM evaluation_runs
    WHERE condition = 'longitudinal'
    GROUP BY run_id
    ORDER BY last_ts DESC
""").fetchall()
out["4A_available_run_ids"] = [r[0] for r in run_rows]
if not run_rows:
    problems.append("4A: no evaluation_runs rows for condition='longitudinal'")
    print(json.dumps({**out, "_problems": problems}, indent=2, default=str))
    sys.exit(50)
selected_run_id = run_rows[0][0]
out["selected_run_id"] = selected_run_id
if len(run_rows) != 1:
    problems.append(f"4A: expected exactly 1 run_id in the Stage 4 DB, found {len(run_rows)}")

task_rows = c.execute("""
    SELECT run_id, task_id, timestamp
    FROM evaluation_runs
    WHERE condition = 'longitudinal' AND run_id = ?
    ORDER BY timestamp
""", [selected_run_id]).fetchall()
out["4B_total_cycles"] = len(task_rows)
out["4B_task_sequence"] = [task_id for _run_id, task_id, _ts in task_rows]
if len(task_rows) != expected_cycles:
    problems.append(f"4B: evaluation_runs has {len(task_rows)} cycles, expected {expected_cycles}")

boundaries = [ts for _run_id, _task_id, ts in task_rows]
cycle_utility = [{} for _ in task_rows]
cycle_events = [[] for _ in task_rows]

utility_rows = c.execute("""
    SELECT capability_id, utility_score, occurred_at
    FROM cycle_utility_scores
    WHERE condition = 'longitudinal' AND run_id = ?
    ORDER BY occurred_at
""", [selected_run_id]).fetchall()
event_rows = c.execute("""
    SELECT capability_id, event_type, details, occurred_at
    FROM cycle_events
    WHERE condition = 'longitudinal' AND run_id = ?
    ORDER BY occurred_at
""", [selected_run_id]).fetchall()

u_idx = 0
for cap_id, score, util_ts in utility_rows:
    while u_idx + 1 < len(boundaries) and boundaries[u_idx + 1] <= util_ts:
        u_idx += 1
    if cycle_utility:
        cycle_utility[u_idx][cap_id] = float(score)

e_idx = 0
for cap_id, event_type, details, evt_ts in event_rows:
    while e_idx + 1 < len(boundaries) and boundaries[e_idx + 1] <= evt_ts:
        e_idx += 1
    if cycle_events:
        cycle_events[e_idx].append({
            "capability_id": cap_id,
            "event_type": event_type,
            "details": details or "",
        })

cycles = []
for i, (_run_id, task_id, _ts) in enumerate(task_rows):
    cycles.append({
        "cycle": i + 1,
        "task_id": task_id,
        "utility_scores": dict(cycle_utility[i]),
        "events": list(cycle_events[i]),
    })
out["4C_reconstructed_cycles"] = len(cycles)
if len(cycles) != expected_cycles:
    problems.append(f"4C: reconstructed {len(cycles)} cycle windows, expected {expected_cycles}")

below = [
    {"cycle": cyc["cycle"], "task_id": cyc["task_id"], "n_caps": len(cyc["utility_scores"])}
    for cyc in cycles
    if len(cyc["utility_scores"]) < floor
]
out["4D_cycles_below_floor"] = below
if below:
    problems.append(f"4D: {len(below)} cycles below floor={floor}")

n_rows, n_non_null, min_u, max_u = c.execute("""
    SELECT COUNT(*), COUNT(utility_score), MIN(utility_score), MAX(utility_score)
    FROM cycle_utility_scores
    WHERE condition = 'longitudinal' AND run_id = ?
""", [selected_run_id]).fetchone()
empty_cycles = [cyc["cycle"] for cyc in cycles if not cyc["utility_scores"]]
out["4E_utility_fill"] = {
    "n_rows": n_rows,
    "n_non_null": n_non_null,
    "min": min_u,
    "max": max_u,
    "empty_cycles": empty_cycles,
}
if n_rows != n_non_null:
    problems.append(f"4E: {n_rows - n_non_null} NULL utility rows")
if empty_cycles:
    problems.append(f"4E: {len(empty_cycles)} cycles had empty utility snapshots")

out["4F_event_windows"] = {
    "cycles_with_events": sum(1 for cyc in cycles if cyc["events"]),
    "total_event_rows": len(event_rows),
}

if not cycles:
    problems.append("M6: no cycles reconstructed from longitudinal data")
    out["M6_scipy_kl"] = None
else:
    kls = []
    prev = {}
    for cyc in cycles:
        curr = cyc["utility_scores"]
        if curr and not prev:
            kl = 0.0
        elif prev and not curr:
            kl = float("inf")
        elif not prev and not curr:
            kl = None
        else:
            all_caps = sorted(set(prev) | set(curr))
            pa = [prev.get(cap, 0.0) for cap in all_caps]
            qa = [curr.get(cap, 0.0) for cap in all_caps]
            min_val = min(min(pa), min(qa))
            if min_val < 0:
                pa = [v - min_val + 1e-10 for v in pa]
                qa = [v - min_val + 1e-10 for v in qa]
            pa = [v + 1e-10 for v in pa]
            qa = [v + 1e-10 for v in qa]
            pa_sum = sum(pa)
            qa_sum = sum(qa)
            pa = [v / pa_sum for v in pa]
            qa = [v / qa_sum for v in qa]
            kl = float(entropy(qa, pa))
        kls.append({"cycle": cyc["cycle"], "task_id": cyc["task_id"], "kl_utility": kl})
        prev = curr
    out["M6_scipy_kl"] = kls

out["_problems"] = problems
print(json.dumps(out, indent=2, default=str))
sys.exit(0 if not problems else 50)
PY
GATE_RC=${PIPESTATUS[0]}

banner "Stage 4 — analyzer metrics (Source 1 for M6)"
uv run python - <<'PY' 2>&1 | tee "$EVAL_OUT/33_analyzer_m6.json"
from __future__ import annotations

import json
import os

import duckdb

from scripts.analyze_longitudinal import (
    _detect_convergence,
    _kl_divergence_between_snapshots,
    _load_cycle_data,
    _resolve_longitudinal_run_id,
)

db = os.path.join(os.environ["EVAL_OUT"], "31_longitudinal.duckdb")
conn = duckdb.connect(db, read_only=True)
run_id = _resolve_longitudinal_run_id(conn, None)
cycles = _load_cycle_data(conn, run_id=run_id)
has_utility_scores = any(bool(c.get("utility_scores")) for c in cycles)

rows = []
prev_utility = {}
for cyc in cycles:
    utility = cyc.get("utility_scores", {})
    kl_utility = (
        _kl_divergence_between_snapshots(prev_utility, utility)
        if has_utility_scores else None
    )
    rows.append({
        "cycle": cyc["cycle"],
        "task_id": cyc["task_id"],
        "kl_utility": kl_utility,
    })
    prev_utility = utility

convergence_cycle = None
utility_kls = [row["kl_utility"] for row in rows if row["kl_utility"] is not None]
if len(utility_kls) > 1:
    convergence_cycle = _detect_convergence(utility_kls[1:], 0.05, 3)
    if convergence_cycle is not None:
        convergence_cycle += 1

print(json.dumps(
    {
        "selected_run_id": run_id,
        "primary_kl_source": "utility_scores" if has_utility_scores else "invocation_counts",
        "convergence_cycle": convergence_cycle,
        "rows": rows,
    },
    indent=2,
    default=str,
))
PY
ANALYZER_JSON_RC=${PIPESTATUS[0]}

banner "Stage 4 — analyzer (Source 1 for M6)"
run_logged "33_analyze_longitudinal.log" \
    uv run scripts/analyze_longitudinal.py --db "$EVAL_OUT/31_longitudinal.duckdb"
ANALYZER_RC=$?

banner "Stage 4 — agreement detector"
uv run python - <<'PY' 2>&1 | tee "$EVAL_OUT/34_stage4_agreement.json"
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

eval_out = Path(os.environ["EVAL_OUT"])
analyzer = json.loads((eval_out / "33_analyzer_m6.json").read_text())
scipy = json.loads((eval_out / "32_stage4_gate.json").read_text())

details = []

if analyzer.get("selected_run_id") != scipy.get("selected_run_id"):
    details.append({
        "field": "selected_run_id",
        "analyzer": analyzer.get("selected_run_id"),
        "scipy": scipy.get("selected_run_id"),
    })

if analyzer.get("primary_kl_source") != "utility_scores":
    details.append({
        "field": "primary_kl_source",
        "analyzer": analyzer.get("primary_kl_source"),
        "scipy": "utility_scores",
    })

an_rows = analyzer.get("rows", [])
sp_rows = scipy.get("M6_scipy_kl", []) or []
if len(an_rows) != len(sp_rows):
    details.append({
        "field": "row_count",
        "analyzer": len(an_rows),
        "scipy": len(sp_rows),
    })

for idx, (a_row, s_row) in enumerate(zip(an_rows, sp_rows), start=1):
    if a_row.get("cycle") != s_row.get("cycle") or a_row.get("task_id") != s_row.get("task_id"):
        details.append({
            "field": "cycle_identity",
            "cycle": idx,
            "analyzer": {"cycle": a_row.get("cycle"), "task_id": a_row.get("task_id")},
            "scipy": {"cycle": s_row.get("cycle"), "task_id": s_row.get("task_id")},
        })
    a_kl = a_row.get("kl_utility")
    s_kl = s_row.get("kl_utility")
    if a_kl is None or s_kl is None:
        if a_kl != s_kl:
            details.append({
                "field": "kl_utility",
                "cycle": idx,
                "analyzer": a_kl,
                "scipy": s_kl,
            })
    elif abs(float(a_kl) - float(s_kl)) > 0.0005:
        details.append({
            "field": "kl_utility",
            "cycle": idx,
            "analyzer": a_kl,
            "scipy": s_kl,
        })

analyzer_conv = analyzer.get("convergence_cycle")
scipy_conv = None
streak = 0
for row in sp_rows[1:]:
    kl = row.get("kl_utility")
    if kl is not None and kl < 0.05:
        streak += 1
        if streak >= 3:
            scipy_conv = row["cycle"]
            break
    else:
        streak = 0
if analyzer_conv != scipy_conv:
    details.append({
        "field": "convergence_cycle",
        "analyzer": analyzer_conv,
        "scipy": scipy_conv,
    })

summary = {"n_disagreements": len(details), "details": details}
print(json.dumps(summary, indent=2))
sys.exit(0 if not details else 70)
PY
AGREE_RC=${PIPESTATUS[0]}

if [[ $GATE_RC -ne 0 ]]; then
    write_marker "$MARKER" "FAIL" "gate checks failed"
    echo "STAGE 4: FAIL — completeness gate."
    exit 1
fi
if [[ $ANALYZER_JSON_RC -ne 0 ]]; then
    write_marker "$MARKER" "FAIL" "machine-readable analyzer metrics failed"
    exit 1
fi
if [[ $ANALYZER_RC -ne 0 && $ANALYZER_RC -ne 2 ]]; then
    write_marker "$MARKER" "FAIL" "analyzer exit $ANALYZER_RC"
    exit 1
fi
if [[ $AGREE_RC -ne 0 ]]; then
    write_marker "$MARKER" "FAIL" "analyzer/scipy disagreement on M6"
    exit 1
fi

# Shape OK → audit
banner "Stage 4 — generating audit request for sampled cycles"
generate_audit_request "stage4" "longitudinal_sample"
[[ $? -ne 0 ]] && { write_marker "$MARKER" "FAIL" "audit gen failed"; exit 1; }

write_marker "$MARKER" "SHAPE_OK" "six gates green; awaiting audit"
echo "STAGE 4: shape OK."
print_audit_handoff "stage4" "longitudinal_sample"
exit 0
