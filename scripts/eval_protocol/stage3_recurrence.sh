#!/usr/bin/env bash
# Stage 3 — Supplemental recurrence eval for Pillar 3.
#
# State machine:
#   (no marker) --launch--> LAUNCHED
#     --pid exits, shape OK--> SHAPE_OK (audit request)
#     --verdicts--> PASS | FAIL
#
# Usage:
#     scripts/eval_protocol/stage3_recurrence.sh <path-to-eval-dir>
#
# Tunables:
#     RECURRENCE_TASKS="T04,T11,T13,T14,T15,T20"
#     RECURRENCE_VARIANTS=3
#     RECURRENCE_CONDITIONS="full,no_verification,no_optimization,static"
#     RECURRENCE_RUNS=3
#     RECURRENCE_CONDITION_BUDGET_HOURS=8

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

resolve_eval_out "stage3" "${1:-}" || exit $?
require_prior_stage "stage1.marker" "stage3" || exit $?

MARKER="stage3.marker"
PID_FILE="$EVAL_OUT/40_recurrence.pid"

export RECURRENCE_TASKS="${RECURRENCE_TASKS:-T04,T11,T13,T14,T15,T20}"
export RECURRENCE_VARIANTS="${RECURRENCE_VARIANTS:-3}"
export RECURRENCE_CONDITIONS="${RECURRENCE_CONDITIONS:-full,no_verification,no_optimization,static}"
export RECURRENCE_RUNS="${RECURRENCE_RUNS:-3}"
export RECURRENCE_CONDITION_BUDGET_HOURS="${RECURRENCE_CONDITION_BUDGET_HOURS:-8}"

_csv_count() {
    local value="$1"
    awk -F',' 'NF {print NF}' <<<"$value"
}

EXPECTED_TASKS=$(_csv_count "$RECURRENCE_TASKS")
EXPECTED_CONDITIONS=$(_csv_count "$RECURRENCE_CONDITIONS")
EXPECTED_ROWS=$(( EXPECTED_TASKS * RECURRENCE_VARIANTS * EXPECTED_CONDITIONS * RECURRENCE_RUNS ))
export EXPECTED_TASKS EXPECTED_CONDITIONS EXPECTED_ROWS

if [[ -f "$EVAL_OUT/$MARKER" ]]; then
    STATUS=$(head -1 "$EVAL_OUT/$MARKER")
    case "$STATUS" in
        PASS) banner "Stage 3 already PASS."; exit 0 ;;
        FAIL) banner "Stage 3 previously FAILED — delete marker to retry."; exit 1 ;;
        SHAPE_OK)
            banner "Stage 3 — shape OK, checking audit verdicts"
            check_audit_verdicts "stage3"; RC=$?
            case $RC in
                0) write_marker "$MARKER" "PASS" "shape + audit clean"
                   echo "STAGE 3: PASS. Continue with the remaining protocol stages as needed."
                   exit 0 ;;
                1) write_marker "$MARKER" "FAIL" "audit reported FAIL rows"; exit 1 ;;
                2) print_audit_handoff "stage3" "recurrence_sample"; exit 0 ;;
            esac ;;
        LAUNCHED)
            if [[ -f "$PID_FILE" ]] && ps -p "$(cat "$PID_FILE")" > /dev/null 2>&1; then
                banner "Stage 3 — recurrence eval still running (PID $(cat "$PID_FILE"))"
                tail -20 "$EVAL_OUT/40_recurrence.log" 2>/dev/null
                exit 0
            fi
            banner "Stage 3 — recurrence eval finished, verifying shape"
            ;;
    esac
fi

if [[ ! -f "$EVAL_OUT/$MARKER" ]]; then
    banner "Stage 3 — launching recurrence eval"
    snapshot_git_state "40_stage3_launch"

    echo "This will WIPE the recurrence DB, recurrence MetricsStore files, and regenerate recurrence fixtures."
    echo "Expected matrix: tasks=$RECURRENCE_TASKS | variants=$RECURRENCE_VARIANTS | conditions=$RECURRENCE_CONDITIONS | runs=$RECURRENCE_RUNS"
    echo "Expected rows: $EXPECTED_ROWS"
    echo "Per-condition watchdog: ${RECURRENCE_CONDITION_BUDGET_HOURS}h"
    read -p "Continue? [y/N] " -n 1 -r; echo
    [[ ! $REPLY =~ ^[Yy]$ ]] && { echo "Aborted."; exit 0; }

    if pgrep -fl "run_recurrence_eval.py" > /dev/null 2>&1; then
        echo "ERROR: a recurrence eval runner is currently running:"
        pgrep -fl "run_recurrence_eval.py"
        echo "Wait for it to finish or kill it before launching Stage 3."
        exit 1
    fi

    RECURRENCE_DB="data/eval/metrics_recurrence_${EVAL_OUT##*/}.duckdb"
    RECURRENCE_METRICS_ROOT="data/eval/recurrence_metrics_${EVAL_OUT##*/}"
    rm -f "$RECURRENCE_DB"
    rm -rf "$RECURRENCE_METRICS_ROOT"
    echo "$RECURRENCE_DB" > "$EVAL_OUT/40_recurrence_db_path.txt"
    echo "$RECURRENCE_METRICS_ROOT" > "$EVAL_OUT/40_recurrence_metrics_root.txt"

    nohup caffeinate -i -s uv run scripts/run_recurrence_eval.py \
        --db "$RECURRENCE_DB" \
        --metrics-root "$RECURRENCE_METRICS_ROOT" \
        --tasks "$RECURRENCE_TASKS" \
        --variants "$RECURRENCE_VARIANTS" \
        --conditions "$RECURRENCE_CONDITIONS" \
        --n-runs "$RECURRENCE_RUNS" \
        --condition-budget-hours "$RECURRENCE_CONDITION_BUDGET_HOURS" \
        --regenerate-fixtures \
        > "$EVAL_OUT/40_recurrence.log" 2>&1 &
    REC_PID=$!
    echo "$REC_PID" > "$PID_FILE"
    echo "Started PID $REC_PID at $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$EVAL_OUT/40_recurrence_start.txt"

    write_marker "$MARKER" "LAUNCHED" "recurrence PID $REC_PID"
    echo "STAGE 3 LAUNCHED (PID $REC_PID)."
    echo
    echo "Log file: $EVAL_OUT/40_recurrence.log"
    echo "Streaming live output below — Ctrl-C stops the tail only; eval keeps running."
    echo
    echo "---------------- live log (Ctrl-C to stop watching) ----------------"
    sleep 2
    exec tail -f "$EVAL_OUT/40_recurrence.log"
fi

snapshot_git_state "40_stage3_verify"

RECURRENCE_DB=$(cat "$EVAL_OUT/40_recurrence_db_path.txt" 2>/dev/null)
if [[ -z "$RECURRENCE_DB" || ! -f "$RECURRENCE_DB" ]]; then
    write_marker "$MARKER" "FAIL" "recurrence DB missing after run: $RECURRENCE_DB"
    echo "STAGE 3: FAIL — DB not produced: $RECURRENCE_DB"
    exit 1
fi

cp "$RECURRENCE_DB" "$EVAL_OUT/42_recurrence.duckdb"
echo "Finished at $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$EVAL_OUT/41_recurrence_status.txt"
tail -50 "$EVAL_OUT/40_recurrence.log" >> "$EVAL_OUT/41_recurrence_status.txt"

banner "Stage 3 — recurrence shape gate"
uv run python - <<'PY' 2>&1 | tee "$EVAL_OUT/43_stage3_gate.json"
from __future__ import annotations

import duckdb
import json
import os
import sys

eval_out = os.environ["EVAL_OUT"]
expected_rows = int(os.environ["EXPECTED_ROWS"])
expected_runs = int(os.environ["RECURRENCE_RUNS"])

c = duckdb.connect(os.path.join(eval_out, "42_recurrence.duckdb"), read_only=True)
out = {
    "expected_rows": expected_rows,
    "expected_runs": expected_runs,
}
problems = []

out["A_total_count"] = c.execute("""
    SELECT COUNT(*) FROM evaluation_runs
    WHERE benchmark_track = 'recurrence'
""").fetchone()[0]
if out["A_total_count"] != expected_rows:
    problems.append(f"expected {expected_rows}, got {out['A_total_count']}")

out["B_non_expected_cells"] = c.execute("""
    SELECT condition, task_family_id, variant_id, COUNT(*) AS n
    FROM evaluation_runs
    WHERE benchmark_track = 'recurrence'
    GROUP BY 1, 2, 3
    HAVING n != ?
    ORDER BY 1, 2, 3
""", [expected_runs]).fetchall()
if out["B_non_expected_cells"]:
    problems.append(f"{len(out['B_non_expected_cells'])} recurrence cells != {expected_runs} rows")

out["C_duplicate_run_ids"] = c.execute("""
    SELECT condition, task_instance_id, run_id, COUNT(*)
    FROM evaluation_runs
    WHERE benchmark_track = 'recurrence'
    GROUP BY 1, 2, 3
    HAVING COUNT(*) > 1
""").fetchall()
if out["C_duplicate_run_ids"]:
    problems.append(f"{len(out['C_duplicate_run_ids'])} duplicate recurrence run_ids")

out["D_null_labels"] = c.execute("""
    SELECT COUNT(*) FROM evaluation_runs
    WHERE benchmark_track = 'recurrence'
      AND (gate_passed IS NULL OR output_in_range IS NULL)
      AND failure_reason IS NULL
""").fetchone()[0]
if out["D_null_labels"] != 0:
    problems.append(f"{out['D_null_labels']} NULL labels on non-error rows")

out["E_per_condition_rows"] = c.execute("""
    SELECT condition, COUNT(*)
    FROM evaluation_runs
    WHERE benchmark_track = 'recurrence'
    GROUP BY 1
    ORDER BY 1
""").fetchall()

out["_problems"] = problems
print(json.dumps(out, indent=2, default=str))
sys.exit(0 if not problems else 30)
PY
GATE_RC=${PIPESTATUS[0]}

if [[ $GATE_RC -ne 0 ]]; then
    write_marker "$MARKER" "FAIL" "recurrence shape gate failed"
    echo "STAGE 3: FAIL — recurrence shape gate."
    exit 1
fi

banner "Stage 3 — generating audit request"
generate_audit_request "stage3" "recurrence_sample"
[[ $? -ne 0 ]] && { write_marker "$MARKER" "FAIL" "audit request gen failed"; exit 1; }

write_marker "$MARKER" "SHAPE_OK" "recurrence rows clean; awaiting audit"
echo "STAGE 3: shape OK. DB snapshot: $EVAL_OUT/42_recurrence.duckdb"
print_audit_handoff "stage3" "recurrence_sample"
exit 0
