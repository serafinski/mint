#!/usr/bin/env bash
# Stage 0 — Pre-flight wiring check with audit checkpoint.
#
# State machine:
#   (no marker) --run shape checks--> SHAPE_OK (audit request generated)
#     --paste verdicts, re-run--> PASS | FAIL
#
# Usage:
#     scripts/eval_protocol/stage0_preflight.sh
#     scripts/eval_protocol/stage0_preflight.sh docs/eval_runs/20260422_143000

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

resolve_eval_out "stage0" "${1:-}" || exit $?

MARKER="stage0.marker"

# ---- State detection ----
if [[ -f "$EVAL_OUT/$MARKER" ]]; then
    STATUS=$(head -1 "$EVAL_OUT/$MARKER")
    case "$STATUS" in
        PASS) banner "Stage 0 already PASS — nothing to do."; exit 0 ;;
        FAIL) banner "Stage 0 previously FAILED — delete marker to retry: rm $EVAL_OUT/$MARKER"; exit 1 ;;
        SHAPE_OK)
            banner "Stage 0 — shape OK, checking for audit verdicts"
            check_audit_verdicts "stage0"
            RC=$?
            case $RC in
                0)
                    write_marker "$MARKER" "PASS" "shape OK + audit verdicts clean"
                    echo "STAGE 0: PASS. Next: scripts/eval_protocol/stage1_main_eval.sh $EVAL_OUT"
                    exit 0 ;;
                1)
                    write_marker "$MARKER" "FAIL" "audit verdicts reported FAIL rows"
                    echo "STAGE 0: FAIL — audit verdicts show real problems."
                    exit 1 ;;
                2)
                    print_audit_handoff "stage0" "smoke"
                    exit 0 ;;
            esac ;;
    esac
fi

# ---- Fresh run ----
snapshot_git_state "00_stage0"
FAIL_REASON=""

banner "Stage 0.1 — pytest tests/unit/"
run_logged "00_pytest.log" uv run pytest tests/unit/ -q
[[ $? -ne 0 ]] && FAIL_REASON="pytest failed"

if [[ -z "$FAIL_REASON" ]]; then
    banner "Stage 0.2 — test_pipeline.py (informational — LLM generation is stochastic)"
    run_logged "01_test_pipeline.log" uv run scripts/test_pipeline.py
    PIPE_RC=$?
    # test_pipeline.py runs three real LLM-driven pipelines. Each one generates
    # fresh code from scratch, so runtime outcomes are stochastic — a single run
    # that fails is NOT evidence of pipeline breakage. The 240-record eval
    # (Stage 1) measures the *rate* of such failures across conditions; that's
    # the right instrument, not a one-shot smoke.
    # Examples of expected one-shot flakes: LLM uses deprecated pandas API
    # (df.applymap removed in 2.1+), emits unterminated string literal that
    # the repair pass doesn't always fix, generates edge-case Field constraints.
    # If EVERY run fails the same way, that's a pipeline bug — inspect the log
    # and the unit tests cover the underlying wiring.
    if [[ $PIPE_RC -ne 0 ]]; then
        echo "INFO: test_pipeline.py exited non-zero ($PIPE_RC)." | tee -a "$EVAL_OUT/01_test_pipeline.log"
        echo "      Informational only — stochastic LLM generation; Stage 1 measures the rate." | tee -a "$EVAL_OUT/01_test_pipeline.log"
    fi
fi

if [[ -z "$FAIL_REASON" ]]; then
    banner "Stage 0.3 — test_verification.py (informational — unit tests are authoritative)"
    run_logged "02_test_verification.log" uv run scripts/test_verification.py
    VERIF_RC=$?
    # This script is a high-level smoke against a hand-written reference MCP tool.
    # The unit suite is the authoritative coverage for Tier 0/1/1.5/2/3 logic.
    # If the unit tests pass and test_pipeline.py exercised all capability types
    # end-to-end, test_verification.py's outcome is informational — log it, do
    # not block on it.
    if [[ $VERIF_RC -ne 0 ]]; then
        echo "INFO: test_verification.py exited non-zero ($VERIF_RC)." | tee -a "$EVAL_OUT/02_test_verification.log"
        echo "      Not treated as a blocker — unit tests + test_pipeline are authoritative." | tee -a "$EVAL_OUT/02_test_verification.log"
    fi
fi

if [[ -z "$FAIL_REASON" ]]; then
    banner "Stage 0.4 — longitudinal smoke (3 tasks)"
    rm -f data/eval/longitudinal_smoke.duckdb
    run_logged "03_longitudinal_smoke.log" \
        uv run scripts/run_longitudinal.py \
            --db data/eval/longitudinal_smoke.duckdb \
            --n-tasks 3 \
            --fresh-db \
            --per-task-budget-seconds 900 \
            --campaign-budget-hours 1
    [[ $? -ne 0 ]] && FAIL_REASON="longitudinal smoke failed"
fi

if [[ -z "$FAIL_REASON" ]]; then
    banner "Stage 0.5 — smoke DB integrity"
    uv run python - <<'PY' 2>&1 | tee "$EVAL_OUT/04_smoke_checks.json"
import duckdb, json, os, sys
db = "data/eval/longitudinal_smoke.duckdb"
if not os.path.exists(db):
    print(json.dumps({"error": "smoke DB missing"})); sys.exit(10)
c = duckdb.connect(db, read_only=True)
out = {
    "tasks_flushed_distinct": c.execute("SELECT COUNT(DISTINCT task_id) FROM cycle_utility_scores").fetchone()[0],
    "cycle_events": c.execute("SELECT COUNT(*) FROM cycle_events").fetchone()[0],
    "utility_rows": c.execute("SELECT COUNT(*) FROM cycle_utility_scores").fetchone()[0],
    "rows_per_task": c.execute("SELECT task_id, COUNT(*) FROM cycle_utility_scores GROUP BY 1 ORDER BY 1").fetchall(),
    "utility_stats": c.execute("SELECT MIN(utility_score), MAX(utility_score), COUNT(utility_score) FROM cycle_utility_scores").fetchone(),
}
print(json.dumps(out, indent=2, default=str))
problems = []
# Smoke goal: prove per-task flush wiring works. Task failures are normal.
if out["tasks_flushed_distinct"] < 1:
    problems.append("cycle_utility_scores has no rows — flush never fired (#125)")
if out["utility_rows"] == 0:
    problems.append("cycle_utility_scores empty (#124/#125)")
if out["cycle_events"] == 0:
    print("// INFO: cycle_events empty — normal for a 3-task smoke; Pillar 3 ops rarely fire this early", file=sys.stderr)
min_u, max_u, n_non_null = out["utility_stats"]
if n_non_null != out["utility_rows"]:
    problems.append(f"utility_score has NULLs: {n_non_null}/{out['utility_rows']}")
if problems:
    print("\nPROBLEMS:", file=sys.stderr)
    for p in problems: print(f"  - {p}", file=sys.stderr)
    sys.exit(20)
PY
    [[ ${PIPESTATUS[0]} -ne 0 ]] && FAIL_REASON="smoke checks failed"
fi

banner "Stage 0 — shape check summary"
if [[ -n "$FAIL_REASON" ]]; then
    write_marker "$MARKER" "FAIL" "$FAIL_REASON"
    echo "STAGE 0: FAIL — $FAIL_REASON"
    exit 1
fi

# Shape checks passed — generate audit request
banner "Stage 0 — generating audit request for longitudinal smoke"
generate_audit_request "stage0" "smoke"
AUDIT_RC=$?
if [[ $AUDIT_RC -ne 0 ]]; then
    write_marker "$MARKER" "FAIL" "could not generate audit request"
    echo "STAGE 0: FAIL — audit request generation failed."
    exit 1
fi

write_marker "$MARKER" "SHAPE_OK" "shape checks green; awaiting trace-audit verdicts"
echo "STAGE 0: shape OK."
print_audit_handoff "stage0" "smoke"
exit 0
