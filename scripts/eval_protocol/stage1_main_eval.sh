#!/usr/bin/env bash
# Stage 1 — Main eval with audit checkpoint.
#
# State machine:
#   (no marker) --launch--> LAUNCHED
#     --pid exits, shape OK--> SHAPE_OK (audit request)
#     --verdicts--> PASS | FAIL
#
# Usage:
#     scripts/eval_protocol/stage1_main_eval.sh <path-to-eval-dir>

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

resolve_eval_out "stage1" "${1:-}" || exit $?
require_prior_stage "stage0.marker" "stage1" || exit $?

MARKER="stage1.marker"
PID_FILE="$EVAL_OUT/10_pilot.pid"

# ---- State detection ----
if [[ -f "$EVAL_OUT/$MARKER" ]]; then
    STATUS=$(head -1 "$EVAL_OUT/$MARKER")
    case "$STATUS" in
        PASS) banner "Stage 1 already PASS."; exit 0 ;;
        FAIL) banner "Stage 1 previously FAILED — delete marker to retry."; exit 1 ;;
        SHAPE_OK)
            banner "Stage 1 — shape OK, checking audit verdicts"
            check_audit_verdicts "stage1"; RC=$?
            case $RC in
                0) write_marker "$MARKER" "PASS" "shape + audit clean"
                   echo "STAGE 1: PASS. Next: scripts/eval_protocol/stage2_headline.sh $EVAL_OUT"
                   exit 0 ;;
                1) write_marker "$MARKER" "FAIL" "audit reported FAIL rows"
                   echo "STAGE 1: FAIL — eval numbers unreliable."
                   exit 1 ;;
                2) print_audit_handoff "stage1" "main_sample"; exit 0 ;;
            esac ;;
        LAUNCHED)
            if [[ -f "$PID_FILE" ]] && ps -p "$(cat "$PID_FILE")" > /dev/null 2>&1; then
                banner "Stage 1 — pilot still running (PID $(cat "$PID_FILE"))"
                tail -20 "$EVAL_OUT/10_pilot.log" 2>/dev/null
                exit 0
            fi
            # Pilot exited; fall through to verify phase
            banner "Stage 1 — pilot finished, verifying shape"
            ;;
    esac
fi

# ---- If no marker yet → launch phase ----
if [[ ! -f "$EVAL_OUT/$MARKER" ]]; then
    banner "Stage 1 — launching main eval"
    snapshot_git_state "10_stage1_launch"

    echo "This will WIPE the Stage 1 campaign DB, MetricsStore DB, chroma/, and skills/."
    echo "Pilot runs detached unattended; expect roughly a day, with a 32h hard ceiling."
    read -p "Continue? [y/N] " -n 1 -r; echo
    [[ ! $REPLY =~ ^[Yy]$ ]] && { echo "Aborted."; exit 0; }

    rm -rf data/eval/chroma data/eval/skills
    # State leak cleanup (2026-04-22 post-preflight audit):
    # - `.eval_runner.lock`: stale lock from pre-flights would block run_pilot's
    #   ext7 #51 lock acquisition. Safe to wipe here; we verified no eval
    #   runner is running before launch.
    # - `domains.json`: evolutionary taxonomy from pre-flights would give
    #   Stage 1 a warm-start the proposal's cold-start assumption doesn't
    #   match. Wipe for clean state.
    # - `metrics_store*.duckdb`: MetricsStore persistence from pre-flights
    #   would contaminate invocation counts used for M3 Gini.
    rm -f data/eval/.eval_runner.lock
    rm -f data/eval/domains.json

    # Sanity-check no eval runner is currently running before wiping state.
    if pgrep -fl "run_pilot.py\|run_longitudinal.py" > /dev/null 2>&1; then
        echo "ERROR: an eval runner is currently running:"
        pgrep -fl "run_pilot.py\|run_longitudinal.py"
        echo "Wait for it to finish or kill it before launching Stage 1."
        exit 1
    fi

    # Pin the campaign DB path so we can find it for the verify phase.
    # Without --db, run_pilot writes a fresh timestamped file which the
    # script can't locate afterwards.
    PILOT_DB="data/eval/metrics_pilot_${EVAL_OUT##*/}.duckdb"
    PILOT_METRICS_STORE="data/eval/metrics_store_pilot_${EVAL_OUT##*/}.duckdb"
    rm -f "$PILOT_DB"
    rm -f "$PILOT_METRICS_STORE"
    echo "$PILOT_DB" > "$EVAL_OUT/10_pilot_db_path.txt"
    echo "$PILOT_METRICS_STORE" > "$EVAL_OUT/10_pilot_metrics_store_path.txt"

    # --condition-budget-hours=8 caps each condition at 8h wall-clock.
    # 4 conditions × 8h = 32h hard ceiling — the campaign cannot hang past
    # that. Made explicit here so future default changes don't silently
    # extend the window.
    # K=3 runs per cell (240 total records). Disclosure §11 note: reduced from
    # the K=5 planning target after the 2026-04-22 pre-flight confirmed ~4-5min
    # per run (projecting K=5 to 30h vs K=3 to 18h). Real wall-clock time can
    # be higher once full-condition verification/retry costs are included; the
    # 8h per-condition budget above is the operational guardrail. K=3 retains ±12.6pp CI at
    # 50% success per condition — sufficient for cross-condition comparisons at
    # >=10pp deltas. If deltas are muddy after this run, append K=2 more via
    # --n-runs 2 --append for cumulative K=5.
    # caffeinate prevents macOS idle sleep during the unattended run.
    # -i = prevent idle sleep, -d = prevent display sleep, -s = prevent system
    # sleep on AC. Without this, closed-lid or 15-min-idle pauses all Docker
    # containers mid-run → condition SIGALRM watchdog fires against a stopped
    # process → partial-condition aborts at wake-up.
    nohup caffeinate -i -s uv run scripts/run_pilot.py \
        --all-tasks --all-conditions --n-runs 3 \
        --db "$PILOT_DB" --fresh-db \
        --metrics-store "$PILOT_METRICS_STORE" \
        --condition-budget-hours 8 \
        > "$EVAL_OUT/10_pilot.log" 2>&1 &
    PILOT_PID=$!
    echo "$PILOT_PID" > "$PID_FILE"
    echo "Started PID $PILOT_PID at $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$EVAL_OUT/10_pilot_start.txt"

    write_marker "$MARKER" "LAUNCHED" "pilot PID $PILOT_PID"
    echo "STAGE 1 LAUNCHED (PID $PILOT_PID)."
    echo
    echo "Log file: $EVAL_OUT/10_pilot.log"
    echo "Streaming live output below — Ctrl-C stops the tail only; eval keeps running"
    echo "in the background under nohup + caffeinate. When done watching, just close"
    echo "the terminal or Ctrl-C. Re-run this script after the pilot exits to enter the verify phase."
    echo
    echo "---------------- live log (Ctrl-C to stop watching) ----------------"
    # Give the log a moment to start being written, then tail it.
    sleep 2
    exec tail -f "$EVAL_OUT/10_pilot.log"
fi

# ---- Verify phase (pilot has exited) ----
snapshot_git_state "10_stage1_verify"

echo "Finished at $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$EVAL_OUT/11_pilot_status.txt"
tail -50 "$EVAL_OUT/10_pilot.log" >> "$EVAL_OUT/11_pilot_status.txt"

PILOT_DB=$(cat "$EVAL_OUT/10_pilot_db_path.txt" 2>/dev/null)
if [[ -z "$PILOT_DB" || ! -f "$PILOT_DB" ]]; then
    write_marker "$MARKER" "FAIL" "pilot DB missing after run: $PILOT_DB"
    echo "STAGE 1: FAIL — DB not produced: $PILOT_DB"
    exit 1
fi

cp "$PILOT_DB" "$EVAL_OUT/12_metrics.duckdb"

banner "Stage 1 — four go/no-go queries"
uv run python - <<'PY' 2>&1 | tee "$EVAL_OUT/13_stage1_gate.json"
import duckdb, json, os, sys
eval_out = os.environ["EVAL_OUT"]
c = duckdb.connect(os.path.join(eval_out, "12_metrics.duckdb"), read_only=True)
out = {
    "A_total_count": c.execute("SELECT COUNT(*) FROM evaluation_runs WHERE NOT is_pilot").fetchone()[0],
    "B_non_expected_cells": c.execute("""
        SELECT condition, task_id, COUNT(*) n FROM evaluation_runs
        WHERE NOT is_pilot GROUP BY 1,2 HAVING n != 3
    """).fetchall(),
    "C_duplicate_run_ids": c.execute("""
        SELECT condition, task_id, run_id, COUNT(*) FROM evaluation_runs
        WHERE NOT is_pilot GROUP BY 1,2,3 HAVING COUNT(*) > 1
    """).fetchall(),
    "D_null_labels": c.execute("""
        SELECT COUNT(*) FROM evaluation_runs
        WHERE NOT is_pilot AND (gate_passed IS NULL OR output_in_range IS NULL)
          AND failure_reason IS NULL
    """).fetchone()[0],
    "E_per_condition_rows": c.execute("""
        SELECT condition, COUNT(*) FROM evaluation_runs WHERE NOT is_pilot GROUP BY 1 ORDER BY 1
    """).fetchall(),
}
problems = []
# 20 tasks × 4 conditions × 3 runs = 240 (matches launcher's --n-runs 3).
if out["A_total_count"] != 240: problems.append(f"expected 240, got {out['A_total_count']}")
if out["B_non_expected_cells"]: problems.append(f"{len(out['B_non_expected_cells'])} cells != 3 rows")
if out["C_duplicate_run_ids"]: problems.append(f"{len(out['C_duplicate_run_ids'])} dup run_ids")
if out["D_null_labels"] != 0: problems.append(f"{out['D_null_labels']} NULL labels on non-error rows")
out["_problems"] = problems
print(json.dumps(out, indent=2, default=str))
sys.exit(0 if not problems else 30)
PY
GATE_RC=${PIPESTATUS[0]}

if [[ $GATE_RC -ne 0 ]]; then
    write_marker "$MARKER" "FAIL" "completeness gate failed"
    echo "STAGE 1: FAIL — shape gate."
    exit 1
fi

# Shape OK → audit
banner "Stage 1 — generating audit request"
generate_audit_request "stage1" "main_sample"
[[ $? -ne 0 ]] && { write_marker "$MARKER" "FAIL" "audit request gen failed"; exit 1; }

write_marker "$MARKER" "SHAPE_OK" "240 rows clean; awaiting audit"
echo "STAGE 1: shape OK. DB snapshot: $EVAL_OUT/12_metrics.duckdb"
print_audit_handoff "stage1" "main_sample"
exit 0
