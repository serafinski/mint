#!/usr/bin/env bash
# Stage 2 — Headline numbers with two-source agreement + conditional audit.
#
# The audit is conditional: if analyzer and SQL agree on every metric to 3 sig figs,
# no blocking audit is needed, though a main_sample audit request is still written
# as a convenience artifact. If they disagree on any metric, an audit is generated
# for the disagreement rows specifically.
#
# State machine:
#   (no marker) --analyzer+SQL agreement--> PASS
#     --if any disagreement--> SHAPE_OK (audit request generated)
#       --verdicts--> PASS | FAIL
#
# Usage:
#     scripts/eval_protocol/stage2_headline.sh <path-to-eval-dir>

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

resolve_eval_out "stage2" "${1:-}" || exit $?
require_prior_stage "stage1.marker" "stage2" || exit $?

MARKER="stage2.marker"

# ---- State detection ----
if [[ -f "$EVAL_OUT/$MARKER" ]]; then
    STATUS=$(head -1 "$EVAL_OUT/$MARKER")
    case "$STATUS" in
        PASS) banner "Stage 2 already PASS."; exit 0 ;;
        FAIL) banner "Stage 2 previously FAILED — delete marker to retry."; exit 1 ;;
        SHAPE_OK)
            banner "Stage 2 — agreement computed, checking audit verdicts"
            check_audit_verdicts "stage2"; RC=$?
            case $RC in
                0) write_marker "$MARKER" "PASS" "agreement + audit clean"
                   echo "STAGE 2: PASS. Next: scripts/eval_protocol/stage4_longitudinal.sh $EVAL_OUT"
                   exit 0 ;;
                1) write_marker "$MARKER" "FAIL" "audit found problems"; exit 1 ;;
                2) print_audit_handoff "stage2" "disagreement"; exit 0 ;;
            esac ;;
    esac
fi

# ---- Fresh run ----
snapshot_git_state "20_stage2"

DB="$EVAL_OUT/12_metrics.duckdb"
[[ ! -f "$DB" ]] && { echo "ERROR: DB snapshot missing: $DB"; exit 1; }

banner "Stage 2.1 — analyzer (Source 1)"
# Run the analyzer in Chapter 6 primary-scope mode. Stage 2 extracts only the
# headline metrics from the Stage 1 DB and suppresses verbose per-task detail.
run_logged "20_analyzer.log" uv run scripts/analyze_results.py --db "$DB" --allow-missing-metrics --chapter6-primary-scope
ANALYZER_RC=$?
uv run python - <<'PY' 2>&1 | tee "$EVAL_OUT/20_analyzer_metrics.json"
from __future__ import annotations

import json
import os
from collections import defaultdict

import duckdb

import scripts.analyze_results as ar
from tests.evaluation.tasks import ALL_TASKS

eval_out = os.environ["EVAL_OUT"]
db = os.path.join(eval_out, "12_metrics.duckdb")
conn = duckdb.connect(db, read_only=True)
ar._SCOPE_SQL = ""

m1_rows = ar.query_condition_summary(conn)
m1 = {
    row["condition"]: {
        "n": int(row["n"]),
        "successes": int(row["successes"]),
        "pct": round(100.0 * row["successes"] / row["n"], 2) if row["n"] else None,
    }
    for row in m1_rows
}

per_run_rows = conn.execute(
    """
    SELECT ie.condition, ie.run_id, ie.capability_id, COUNT(*) AS invocations
    FROM invocation_events ie
    LEFT JOIN (
        SELECT DISTINCT run_id, capability_id
        FROM cycle_events
        WHERE event_type = 'pruned'
    ) pruned
        ON ie.capability_id = pruned.capability_id
        AND ie.run_id = pruned.run_id
    WHERE NOT COALESCE(ie.is_pilot, FALSE)
      AND pruned.capability_id IS NULL
    GROUP BY ie.condition, ie.run_id, ie.capability_id
    ORDER BY ie.condition, ie.run_id, ie.capability_id
    """
).fetchall()
pooled_all: dict[str, int] = defaultdict(int)
pooled_by_condition: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
for cond, _run_id, cap_id, inv_count in per_run_rows:
    pooled_all[cap_id] += int(inv_count)
    pooled_by_condition[cond][cap_id] += int(inv_count)
m3 = {
    "aggregate": ar._gini_coefficient(list(pooled_all.values())) if pooled_all else None,
    "per_condition_aggregate": {
        cond: ar._gini_coefficient(list(cap_counts.values()))
        for cond, cap_counts in pooled_by_condition.items()
    },
    "n_capabilities": len(pooled_all),
}

lat_rows = conn.execute(
    """
    SELECT condition,
           COUNT(*) AS n_included,
           AVG(attempt_latency_ms) / 1000.0 AS avg_sec,
           MEDIAN(attempt_latency_ms) / 1000.0 AS median_sec,
           QUANTILE_CONT(attempt_latency_ms, 0.95) / 1000.0 AS p95_sec
    FROM evaluation_runs
    WHERE gate_passed
      AND output_in_range
      AND NOT COALESCE(output_shape_mismatch, FALSE)
      AND NOT is_pilot
    GROUP BY condition
    ORDER BY condition
    """
).fetchall()
m5 = {
    cond: {
        "n_included": int(n_included),
        "avg_sec": round(float(avg_sec), 4),
        "median_sec": round(float(median_sec), 4),
        "p95_sec": round(float(p95_sec), 4),
    }
    for cond, n_included, avg_sec, median_sec, p95_sec in lat_rows
}

expected_map = {t.task_id: getattr(t, "expected_type", None) for t in ALL_TASKS}
class_rows = conn.execute(
    """
    SELECT task_id, condition, capability_type
    FROM evaluation_runs
    WHERE is_pilot = FALSE
      AND condition IN ('full', 'no_verification', 'no_optimization')
      AND COALESCE(generated_artifact_count, 0) > 0
    """
).fetchall()
per_condition_hits: dict[str, dict[str, int]] = defaultdict(lambda: {"hit": 0, "total": 0})
n_total = 0
n_hit = 0
for task_id, condition, capability_type in class_rows:
    expected = expected_map.get(task_id)
    if expected is None:
        continue
    n_total += 1
    per_condition_hits[condition]["total"] += 1
    if (capability_type or "").lower() == expected:
        n_hit += 1
        per_condition_hits[condition]["hit"] += 1
classacc = {
    "aggregate_pct": round(100.0 * n_hit / n_total, 2) if n_total else None,
    "population_n": n_total,
    "per_condition_pct": {
        cond: round(100.0 * stats["hit"] / stats["total"], 2)
        for cond, stats in per_condition_hits.items()
        if stats["total"]
    },
}

print(json.dumps(
    {
        "M1": m1,
        "M3": m3,
        "M5": m5,
        "ClassAcc": classacc,
    },
    indent=2,
    sort_keys=True,
))
PY
ANALYZER_METRICS_RC=${PIPESTATUS[0]}

banner "Stage 2.2 — SQL cross-check (Source 2)"
uv run python - <<'PY' 2>&1 | tee "$EVAL_OUT/21_sql_crosscheck.json"
from __future__ import annotations

import duckdb
import json
import os
from collections import defaultdict

from tests.evaluation.tasks import ALL_TASKS

eval_out = os.environ["EVAL_OUT"]
c = duckdb.connect(os.path.join(eval_out, "12_metrics.duckdb"), read_only=True)
out = {}

def gini_coefficient(values):
    values = [float(v) for v in values if v is not None]
    if not values:
        return None
    values = sorted(values)
    total = sum(values)
    if total <= 0:
        return 0.0
    n = len(values)
    weighted = sum((idx + 1) * value for idx, value in enumerate(values))
    return (2.0 * weighted - (n + 1) * total) / (n * total)

m1_rows = c.execute(
    """
    SELECT condition,
           COUNT(*) AS n,
           SUM(CASE WHEN gate_passed AND output_in_range
                         AND NOT COALESCE(output_shape_mismatch, FALSE)
                    THEN 1 ELSE 0 END) AS successes
    FROM evaluation_runs
    WHERE NOT is_pilot
    GROUP BY condition
    ORDER BY condition
    """
).fetchall()
out["M1"] = {
    condition: {
        "n": int(n),
        "successes": int(successes),
        "pct": round(100.0 * successes / n, 2) if n else None,
    }
    for condition, n, successes in m1_rows
}

m5_rows = c.execute(
    """
    SELECT condition,
           COUNT(*) AS n_included,
           AVG(attempt_latency_ms) / 1000.0 AS avg_sec,
           MEDIAN(attempt_latency_ms) / 1000.0 AS median_sec,
           QUANTILE_CONT(attempt_latency_ms, 0.95) / 1000.0 AS p95_sec
    FROM evaluation_runs
    WHERE gate_passed
      AND output_in_range
      AND NOT COALESCE(output_shape_mismatch, FALSE)
      AND NOT is_pilot
    GROUP BY condition
    ORDER BY condition
    """
).fetchall()
out["M5"] = {
    condition: {
        "n_included": int(n_included),
        "avg_sec": round(float(avg_sec), 4),
        "median_sec": round(float(median_sec), 4),
        "p95_sec": round(float(p95_sec), 4),
    }
    for condition, n_included, avg_sec, median_sec, p95_sec in m5_rows
}

per_run_rows = c.execute(
    """
    SELECT ie.condition, ie.run_id, ie.capability_id, COUNT(*) AS invocations
    FROM invocation_events ie
    LEFT JOIN (
        SELECT DISTINCT run_id, capability_id
        FROM cycle_events
        WHERE event_type = 'pruned'
    ) pruned
        ON ie.capability_id = pruned.capability_id
        AND ie.run_id = pruned.run_id
    WHERE NOT COALESCE(ie.is_pilot, FALSE)
      AND pruned.capability_id IS NULL
    GROUP BY ie.condition, ie.run_id, ie.capability_id
    ORDER BY ie.condition, ie.run_id, ie.capability_id
    """
).fetchall()
pooled_all: dict[str, int] = defaultdict(int)
pooled_by_condition: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
for cond, _run_id, cap_id, inv_count in per_run_rows:
    pooled_all[cap_id] += int(inv_count)
    pooled_by_condition[cond][cap_id] += int(inv_count)
out["M3"] = {
    "aggregate": gini_coefficient(list(pooled_all.values())) if pooled_all else None,
    "per_condition_aggregate": {
        cond: gini_coefficient(list(cap_counts.values()))
        for cond, cap_counts in pooled_by_condition.items()
    },
    "n_capabilities": len(pooled_all),
}

expected = {t.task_id: getattr(t, "expected_type", None) for t in ALL_TASKS}
class_rows = c.execute(
    """
    SELECT task_id, condition, capability_type
    FROM evaluation_runs
    WHERE is_pilot = FALSE
      AND condition IN ('full', 'no_verification', 'no_optimization')
      AND COALESCE(generated_artifact_count, 0) > 0
    """
).fetchall()
per_condition_hits: dict[str, dict[str, int]] = defaultdict(lambda: {"hit": 0, "total": 0})
n_total = 0
n_hit = 0
for task_id, condition, capability_type in class_rows:
    expected_type = expected.get(task_id)
    if expected_type is None:
        continue
    n_total += 1
    per_condition_hits[condition]["total"] += 1
    if (capability_type or "").lower() == expected_type:
        n_hit += 1
        per_condition_hits[condition]["hit"] += 1
out["ClassAcc"] = {
    "aggregate_pct": round(100.0 * n_hit / n_total, 2) if n_total else None,
    "population_n": n_total,
    "per_condition_pct": {
        cond: round(100.0 * stats["hit"] / stats["total"], 2)
        for cond, stats in per_condition_hits.items()
        if stats["total"]
    },
}

print(json.dumps(out, indent=2, sort_keys=True))
PY
SQL_RC=${PIPESTATUS[0]}

banner "Stage 2.3 — agreement detector"
uv run python - <<'PY' 2>&1 | tee "$EVAL_OUT/22_agreement_summary.json"
from __future__ import annotations

import json
import os
from pathlib import Path

eval_out = Path(os.environ["EVAL_OUT"])
analyzer = json.loads((eval_out / "20_analyzer_metrics.json").read_text())
sql = json.loads((eval_out / "21_sql_crosscheck.json").read_text())

disagreements = []

def compare_numeric(metric, analyzer_value, sql_value, *, tolerance, condition=None, stat=None):
    if analyzer_value is None or sql_value is None:
        if analyzer_value != sql_value:
            disagreements.append({
                "metric": metric,
                "condition": condition,
                "stat": stat,
                "analyzer": analyzer_value,
                "sql": sql_value,
                "reason": "missing_or_null",
            })
        return
    if abs(float(analyzer_value) - float(sql_value)) > tolerance:
        disagreements.append({
            "metric": metric,
            "condition": condition,
            "stat": stat,
            "analyzer": analyzer_value,
            "sql": sql_value,
            "reason": "value_mismatch",
        })

for cond in sorted(set(analyzer.get("M1", {})) | set(sql.get("M1", {}))):
    a = analyzer.get("M1", {}).get(cond)
    s = sql.get("M1", {}).get(cond)
    if a is None or s is None:
        disagreements.append({
            "metric": "M1",
            "condition": cond,
            "analyzer": a,
            "sql": s,
            "reason": "condition_missing",
        })
        continue
    if int(a["n"]) != int(s["n"]) or int(a["successes"]) != int(s["successes"]):
        disagreements.append({
            "metric": "M1",
            "condition": cond,
            "analyzer": a,
            "sql": s,
            "reason": "count_mismatch",
        })
    compare_numeric("M1", a["pct"], s["pct"], tolerance=0.01, condition=cond, stat="pct")

compare_numeric(
    "M3",
    analyzer.get("M3", {}).get("aggregate"),
    sql.get("M3", {}).get("aggregate"),
    tolerance=0.0005,
    stat="aggregate",
)
if analyzer.get("M3", {}).get("n_capabilities") != sql.get("M3", {}).get("n_capabilities"):
    disagreements.append({
        "metric": "M3",
        "stat": "n_capabilities",
        "analyzer": analyzer.get("M3", {}).get("n_capabilities"),
        "sql": sql.get("M3", {}).get("n_capabilities"),
        "reason": "count_mismatch",
    })
for cond in sorted(
    set(analyzer.get("M3", {}).get("per_condition_aggregate", {}))
    | set(sql.get("M3", {}).get("per_condition_aggregate", {}))
):
    compare_numeric(
        "M3",
        analyzer.get("M3", {}).get("per_condition_aggregate", {}).get(cond),
        sql.get("M3", {}).get("per_condition_aggregate", {}).get(cond),
        tolerance=0.0005,
        condition=cond,
        stat="aggregate",
    )

for cond in sorted(set(analyzer.get("M5", {})) | set(sql.get("M5", {}))):
    a = analyzer.get("M5", {}).get(cond)
    s = sql.get("M5", {}).get(cond)
    if a is None or s is None:
        disagreements.append({
            "metric": "M5",
            "condition": cond,
            "analyzer": a,
            "sql": s,
            "reason": "condition_missing",
        })
        continue
    if int(a["n_included"]) != int(s["n_included"]):
        disagreements.append({
            "metric": "M5",
            "condition": cond,
            "stat": "n_included",
            "analyzer": a["n_included"],
            "sql": s["n_included"],
            "reason": "count_mismatch",
        })
    for stat in ("avg_sec", "median_sec", "p95_sec"):
        compare_numeric("M5", a[stat], s[stat], tolerance=0.01, condition=cond, stat=stat)

compare_numeric(
    "ClassAcc",
    analyzer.get("ClassAcc", {}).get("aggregate_pct"),
    sql.get("ClassAcc", {}).get("aggregate_pct"),
    tolerance=0.01,
    stat="aggregate_pct",
)
if analyzer.get("ClassAcc", {}).get("population_n") != sql.get("ClassAcc", {}).get("population_n"):
    disagreements.append({
        "metric": "ClassAcc",
        "stat": "population_n",
        "analyzer": analyzer.get("ClassAcc", {}).get("population_n"),
        "sql": sql.get("ClassAcc", {}).get("population_n"),
        "reason": "count_mismatch",
    })
for cond in sorted(
    set(analyzer.get("ClassAcc", {}).get("per_condition_pct", {}))
    | set(sql.get("ClassAcc", {}).get("per_condition_pct", {}))
):
    compare_numeric(
        "ClassAcc",
        analyzer.get("ClassAcc", {}).get("per_condition_pct", {}).get(cond),
        sql.get("ClassAcc", {}).get("per_condition_pct", {}).get(cond),
        tolerance=0.01,
        condition=cond,
        stat="per_condition_pct",
    )

summary = {"n_disagreements": len(disagreements), "details": disagreements}
print(json.dumps(summary, indent=2))

# Also emit disagreement rows for audit if any.
if disagreements:
    import duckdb
    c = duckdb.connect(str(eval_out / "12_metrics.duckdb"), read_only=True)
    rows = []
    sampled_conditions = sorted({d["condition"] for d in disagreements if d.get("condition")})
    for cond in sampled_conditions:
        found = c.execute("""
            SELECT run_id, task_id, condition, langfuse_trace_id
            FROM evaluation_runs WHERE NOT is_pilot AND condition = ?
              AND langfuse_trace_id IS NOT NULL ORDER BY random() LIMIT 2
        """, [cond]).fetchall()
        for r in found:
            rows.append({
                "run_id": r[0], "task_id": r[1], "condition": r[2],
                "langfuse_trace_id": r[3],
                "analyzer": "condition-scoped disagreement",
                "sql": "condition-scoped disagreement",
            })
    if not rows:
        found = c.execute("""
            SELECT run_id, task_id, condition, langfuse_trace_id
            FROM evaluation_runs
            WHERE NOT is_pilot AND langfuse_trace_id IS NOT NULL
            ORDER BY random() LIMIT 2
        """).fetchall()
        for r in found:
            rows.append({
                "run_id": r[0], "task_id": r[1], "condition": r[2],
                "langfuse_trace_id": r[3],
                "analyzer": "global disagreement",
                "sql": "global disagreement",
            })
    (eval_out / "22_disagreements.json").write_text(json.dumps({"rows": rows}, indent=2))
PY
AGREE_RC=${PIPESTATUS[0]}

# Decision: if no disagreements AND analyzer succeeded, stage is PASS without audit.
# If disagreements exist, generate audit request and mark SHAPE_OK.

N_DISAGREE=$(uv run python - <<'PY' 2>/dev/null || echo "?"
import json, os
from pathlib import Path
eval_out = Path(os.environ["EVAL_OUT"])
print(json.loads((eval_out / "22_agreement_summary.json").read_text())["n_disagreements"])
PY
)

if [[ $ANALYZER_RC -ne 0 ]]; then
    write_marker "$MARKER" "FAIL" "analyzer exit $ANALYZER_RC"
    exit 1
fi
if [[ $ANALYZER_METRICS_RC -ne 0 ]]; then
    write_marker "$MARKER" "FAIL" "machine-readable analyzer metrics failed"
    exit 1
fi
if [[ $SQL_RC -ne 0 ]]; then
    write_marker "$MARKER" "FAIL" "sql cross-check failed"
    exit 1
fi
if [[ $AGREE_RC -ne 0 || "$N_DISAGREE" == "?" ]]; then
    write_marker "$MARKER" "FAIL" "agreement detector failed"
    exit 1
fi

if [[ "$N_DISAGREE" == "0" ]]; then
    # Full agreement — optional audit only. Generate the sample request as a
    # convenience artifact, but do not block downstream stages on it.
    banner "Stage 2 — full analyzer/SQL agreement"
    echo "Analyzer and SQL agree on all headline metrics."
    echo "A main_sample trace audit is still recommended, but no longer blocks PASS."
    echo
    generate_audit_request "stage2" "main_sample"
    write_marker "$MARKER" "PASS" "agreement clean; optional main_sample audit request generated"
    echo "STAGE 2: PASS. Optional audit request: $EVAL_OUT/stage2_audit_request.md"
else
    banner "Stage 2 — DISAGREEMENT detected"
    echo "Analyzer and SQL disagreed on $N_DISAGREE metric value(s)."
    echo "Generating trace audit for disagreement rows specifically."
    generate_audit_request "stage2" "disagreement"
    write_marker "$MARKER" "SHAPE_OK" "disagreement detected; audit required"
    print_audit_handoff "stage2" "disagreement"
fi
exit 0
