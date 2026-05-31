#!/usr/bin/env bash
# Stage 5 — Plausibility audit with audit checkpoint on flagged rows.
#
# State machine:
#   (no marker) --priors--> if no INVESTIGATE flags: PASS directly
#                           if INVESTIGATE flags: SHAPE_OK + audit on flagged rows
#     --verdicts--> PASS | FAIL
#
# Usage:
#     scripts/eval_protocol/stage5_plausibility.sh <path-to-eval-dir>

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

resolve_eval_out "stage5" "${1:-}" || exit $?
require_prior_stage "stage4.marker" "stage5" || exit $?

MARKER="stage5.marker"

# ---- State detection ----
if [[ -f "$EVAL_OUT/$MARKER" ]]; then
    STATUS=$(head -1 "$EVAL_OUT/$MARKER")
    case "$STATUS" in
        PASS) banner "Stage 5 already PASS."; exit 0 ;;
        FAIL) banner "Stage 5 previously FAILED — delete marker to retry."; exit 1 ;;
        SHAPE_OK)
            banner "Stage 5 — priors computed, checking audit verdicts"
            check_audit_verdicts "stage5"; RC=$?
            case $RC in
                0) write_marker "$MARKER" "PASS" "priors + audit clean"
                   echo "STAGE 5: PASS."
                   echo "Chapter 6 data is as defended as it gets. Start writing."
                   exit 0 ;;
                1) write_marker "$MARKER" "FAIL" "audit confirmed problems on flagged rows"
                   echo "STAGE 5: FAIL — investigate-level flags confirmed by trace audit."
                   exit 1 ;;
                2) print_audit_handoff "stage5" "plausibility_flagged"; exit 0 ;;
            esac ;;
    esac
fi

# ---- Fresh run ----
snapshot_git_state "40_stage5"
banner "Stage 5 — plausibility priors"

uv run python - <<'PY' 2>&1 | tee "$EVAL_OUT/40_plausibility.json"
import duckdb, json, os, sys
eval_out = os.environ["EVAL_OUT"]
main_db = os.path.join(eval_out, "12_metrics.duckdb")
long_db = os.path.join(eval_out, "31_longitudinal.duckdb")
report = {"flags": [], "info": {}}
def flag(l, code, msg): report["flags"].append({"level": l, "code": code, "msg": msg})
c = duckdb.connect(main_db, read_only=True)

per_cond = c.execute("""
    SELECT condition, COUNT(*) n,
           ROUND(100.0*AVG(CASE WHEN gate_passed AND output_in_range THEN 1.0 ELSE 0.0 END),2) pct
    FROM evaluation_runs WHERE NOT is_pilot GROUP BY 1 ORDER BY 1
""").fetchall()
report["info"]["per_condition_success"] = per_cond
cond_map = {x[0]: x[2] for x in per_cond}

expected_conds = {"static","no_verification","no_optimization","full"}
missing = expected_conds - set(cond_map.keys())
if missing: flag("INVESTIGATE","P1_missing_conditions",f"missing: {sorted(missing)}")

RANGES = {"static":(5,60), "no_verification":(15,85), "no_optimization":(20,90), "full":(20,90)}
for cond_, (lo, hi) in RANGES.items():
    if cond_ not in cond_map: continue
    pct = cond_map[cond_]
    if pct < lo: flag("INVESTIGATE",f"P2_{cond_}_below",f"{cond_}={pct}% < {lo}%")
    elif pct > hi: flag("WARN",f"P2_{cond_}_above",f"{cond_}={pct}% > {hi}%")

if cond_map.get("full",0) < cond_map.get("static",0)+3:
    flag("INVESTIGATE","P3_full_not_gt_static",
         f"full ({cond_map.get('full')}%) not above static ({cond_map.get('static')}%) — JIT adds nothing")
if cond_map.get("no_verification",0) > cond_map.get("full",0)+10:
    flag("INVESTIGATE","P3_no_ver_beats_full","no_verification beats full by >10pp — gate over-blocking")

per_task = c.execute("""
    SELECT task_id, COUNT(*) n, SUM(CASE WHEN gate_passed AND output_in_range THEN 1 ELSE 0 END) n_ok
    FROM evaluation_runs WHERE NOT is_pilot AND condition='full' GROUP BY 1 ORDER BY 1
""").fetchall()
report["info"]["per_task_full"] = per_task
dead = [t for t,_,ok in per_task if ok==0]
full_runs = c.execute("""
    SELECT COUNT(DISTINCT run_id)
    FROM evaluation_runs
    WHERE NOT is_pilot AND condition='full'
""").fetchone()[0]
if dead:
    lvl = "INVESTIGATE" if len(dead)<=3 else "WARN"
    flag(lvl,"P4_dead_tasks_full",f"{len(dead)} task(s) 0/{full_runs} in full: {dead[:5]}")
    if len(dead) > 6:
        flag("INVESTIGATE","P4_eval_broken",f"{len(dead)}/20 dead — eval broken, not thesis")

err_rows = c.execute("""
    SELECT failure_reason, COUNT(*) n
    FROM evaluation_runs
    WHERE NOT is_pilot
      AND failure_reason IS NOT NULL
      AND lower(trim(failure_reason)) NOT IN ('', 'none', 'null')
    GROUP BY 1
    ORDER BY n DESC
    LIMIT 10
""").fetchall()
report["info"]["top_errors"] = err_rows
if err_rows and err_rows[0][1] >= 40:
    flag("WARN","P5_dominant_error",f"top err hit {err_rows[0][1]}×")

type_dist = c.execute("""
    SELECT capability_type, COUNT(*) FROM evaluation_runs
    WHERE NOT is_pilot AND condition='full' AND gate_passed AND output_in_range
    GROUP BY 1 ORDER BY 1
""").fetchall()
report["info"]["type_distribution_full"] = type_dist
if type_dist:
    total = sum(n for _,n in type_dist)
    for typ,n in type_dist:
        pct = 100.0*n/total if total else 0
        if pct > 80: flag("INVESTIGATE","P6_type_monoculture",f"{typ}={pct:.0f}% — classifier collapsed")

# P10 — snapshot/restore integrity: if success rate is monotonically increasing
# across run_index within a (task, condition) cell, later runs are inheriting
# earlier runs' generated caps — snapshot_restore not resetting state.
run_index_trend = c.execute("""
    WITH indexed AS (
        SELECT condition, task_id, run_id,
               row_number() OVER (PARTITION BY condition, task_id ORDER BY timestamp) AS run_idx,
               (CASE WHEN gate_passed AND output_in_range THEN 1.0 ELSE 0.0 END) AS success
        FROM evaluation_runs WHERE NOT is_pilot AND condition='full'
    )
    SELECT run_idx, AVG(success) FROM indexed GROUP BY 1 ORDER BY 1
""").fetchall()
report["info"]["run_index_trend_full"] = run_index_trend
if len(run_index_trend) >= 3:
    first_val = run_index_trend[0][1] or 0
    last_val  = run_index_trend[-1][1] or 0
    if last_val - first_val > 0.3:  # 30pp jump from earliest to latest run index
        flag("INVESTIGATE","P10_snapshot_leak",
             f"full success rises {first_val:.2f}→{last_val:.2f} across run_index — "
             "snapshot restore may be leaking state between runs")

ca = None
try:
    from tests.evaluation.tasks import ALL_TASKS
    expected = {t.task_id: getattr(t, "expected_type", None) for t in ALL_TASKS}
    rows = c.execute("""
        SELECT task_id, capability_type FROM evaluation_runs
        WHERE NOT is_pilot AND condition!='static' AND gate_passed AND output_in_range
    """).fetchall()
    matches = [(ct, expected.get(tid)) for tid, ct in rows if expected.get(tid)]
    if matches:
        n_ok = sum(1 for ct, exp in matches if ct == exp)
        ca = round(100.0 * n_ok / len(matches), 2)
except Exception as e:
    report["info"]["classification_accuracy_error"] = str(e)
report["info"]["classification_accuracy_pct"] = ca
if ca is not None:
    if ca < 40: flag("INVESTIGATE","P7_classifier_broken",f"{ca}% — near chance")
    elif ca > 97: flag("WARN","P7_classifier_suspicious",f"{ca}% — suspiciously high")

inv = c.execute("""
    SELECT COUNT(DISTINCT capability_id), COUNT(*) FROM invocation_events WHERE NOT is_pilot
""").fetchone()
report["info"]["invocation_summary"] = {"n_caps": inv[0], "n_invocations": inv[1]}
if inv[0] < 8: flag("INVESTIGATE","P9_tiny_registry_use",f"only {inv[0]} caps invoked")
top_cap = c.execute("""
    SELECT capability_id, COUNT(*) n FROM invocation_events
    WHERE NOT is_pilot GROUP BY 1 ORDER BY n DESC LIMIT 5
""").fetchall()
report["info"]["top_5_capabilities"] = top_cap
if top_cap and inv[1] and (100.0*top_cap[0][1]/inv[1]) > 70:
    flag("INVESTIGATE","P9_dominant_cap",
         f"'{top_cap[0][0]}' is {100.0*top_cap[0][1]/inv[1]:.0f}% of invocations")

if os.path.exists(long_db):
    lc = duckdb.connect(long_db, read_only=True)
    kl_input = lc.execute("""
        SELECT task_id, AVG(utility_score), COUNT(*),
               MAX(utility_score)-MIN(utility_score),
               MIN(occurred_at) AS first_seen
        FROM cycle_utility_scores GROUP BY 1 ORDER BY first_seen
    """).fetchall()
    report["info"]["longitudinal_per_cycle"] = kl_input
    if len(kl_input) >= 10:
        first_n, last_n = kl_input[0][2], kl_input[-1][2]
        if last_n == first_n:
            flag("WARN","P10_registry_frozen",f"registry size unchanged over {len(kl_input)} cycles")
        elif last_n < first_n*0.5:
            flag("INVESTIGATE","P10_registry_collapse",f"registry shrank {first_n}->{last_n}")

n_inv = sum(1 for f in report["flags"] if f["level"]=="INVESTIGATE")
n_warn = sum(1 for f in report["flags"] if f["level"]=="WARN")
report["summary"] = {"INVESTIGATE": n_inv, "WARN": n_warn,
                     "verdict": "HEALTHY" if n_inv==0 and n_warn<=1
                                else "REVIEW" if n_inv==0
                                else "INVESTIGATE"}
print(json.dumps(report, indent=2, default=str))
sys.exit(0 if n_inv==0 else 60)
PY
PRIORS_RC=${PIPESTATUS[0]}

if [[ $PRIORS_RC -eq 0 ]]; then
    # No INVESTIGATE flags — stage can PASS without an extra audit
    banner "Stage 5 — all priors clean, no audit needed"
    write_marker "$MARKER" "PASS" "no INVESTIGATE flags; audit not required"
    echo "STAGE 5: PASS (no flags to audit)."
    echo
    echo "Chapter 6 data is as defended as algorithmic checks can make it."
    echo "Final recommended manual step: open 2-3 LangFuse traces and eyeball them."
    exit 0
elif [[ $PRIORS_RC -eq 60 ]]; then
    # INVESTIGATE flags present — generate audit for flagged rows
    banner "Stage 5 — INVESTIGATE flags raised; generating flagged-row audit"
    generate_audit_request "stage5" "plausibility_flagged"
    [[ $? -ne 0 ]] && { write_marker "$MARKER" "FAIL" "audit gen failed"; exit 1; }
    write_marker "$MARKER" "SHAPE_OK" "flags raised; awaiting trace audit verdicts"
    echo "STAGE 5: priors flagged things. Audit the specific rows before marking PASS."
    print_audit_handoff "stage5" "plausibility_flagged"
    exit 0
else
    write_marker "$MARKER" "FAIL" "plausibility script failed (exit $PRIORS_RC)"
    echo "STAGE 5: FAIL — plausibility script crashed (exit $PRIORS_RC)."
    exit 1
fi
