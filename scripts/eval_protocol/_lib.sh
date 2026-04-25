#!/usr/bin/env bash
# Shared helpers for the stage scripts. Sourced, not executed. Do NOT `set -e`.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

# Resolve / mint the capture dir.
resolve_eval_out() {
    local stage_name="$1"
    local arg_dir="$2"
    if [[ -n "$arg_dir" ]]; then
        if [[ "$arg_dir" = /* ]]; then EVAL_OUT="$arg_dir"
        else EVAL_OUT="$REPO_ROOT/$arg_dir"; fi
    elif [[ -n "${EVAL_OUT:-}" ]]; then
        :
    else
        if [[ "$stage_name" != "stage0" ]]; then
            echo "ERROR: $stage_name requires a run directory."
            echo "Usage: $0 <path-to-eval-run-dir>"
            return 2
        fi
        EVAL_RUN=$(date +%Y%m%d_%H%M%S)
        EVAL_OUT="$REPO_ROOT/docs/eval_runs/$EVAL_RUN"
    fi
    mkdir -p "$EVAL_OUT"
    export EVAL_OUT
    echo "Capture dir: $EVAL_OUT"
}

# Require prior stage's marker to say PASS.
require_prior_stage() {
    local marker="$1"; local stage_name="$2"
    if [[ ! -f "$EVAL_OUT/$marker" ]]; then
        echo "ERROR: $stage_name blocked — prior stage marker not found: $EVAL_OUT/$marker"
        return 3
    fi
    if ! grep -q "^PASS" "$EVAL_OUT/$marker"; then
        echo "ERROR: $stage_name blocked — prior stage is not PASS. See $EVAL_OUT/$marker"
        echo "(If the prior stage has SHAPE_OK but is waiting for audit verdicts, complete the audit first.)"
        return 3
    fi
}

# Write a stage marker with status + reason.
write_marker() {
    local marker="$1"; local status="$2"; local reason="$3"
    {
        echo "$status"
        echo "stage: $marker"
        echo "time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "reason: $reason"
    } > "$EVAL_OUT/$marker"
}

snapshot_git_state() {
    local prefix="$1"
    git rev-parse HEAD > "$EVAL_OUT/${prefix}_git_head.txt" 2>&1 || true
    git status --short > "$EVAL_OUT/${prefix}_git_status.txt" 2>&1 || true
    git diff > "$EVAL_OUT/${prefix}_git_diff.txt" 2>&1 || true
}

# Run a command, tee output to a log file, return the command's exit code.
run_logged() {
    local logname="$1"; shift
    local log="$EVAL_OUT/$logname"
    echo "===== $(date -u +%Y-%m-%dT%H:%M:%SZ) — running: $* =====" | tee "$log"
    "$@" 2>&1 | tee -a "$log"
    local rc=${PIPESTATUS[0]}
    echo "===== EXIT=$rc =====" | tee -a "$log"
    return $rc
}

banner() {
    echo
    echo "================================================================"
    echo "  $*"
    echo "================================================================"
}

# ---- Audit cycle helpers ----
#
# Each stage follows this state machine:
#   (no marker) --run--> SHAPE_OK (audit request generated) --verdicts-arrive--> PASS
#                                                            \--verdicts-fail--> FAIL
#
# "Verdicts arrive" = user pastes Claude's response into <stage>_audit_verdicts.md
# and re-runs the stage script. The script then calls check_audit_verdicts.

# Generate an audit request file for a given stage+profile.
#   $1: stage short name (e.g. "stage0", "stage1")
#   $2: profile (smoke / main_sample / disagreement / longitudinal_sample / plausibility_flagged)
#   $3: extra arg passed to audit_traces.sh (optional)
generate_audit_request() {
    local stage="$1"; local profile="$2"; shift 2
    local audit_script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/audit_traces.sh"
    "$audit_script" --stage "$stage" --profile "$profile" --eval-dir "$EVAL_OUT" "$@"
    return $?
}

# Check whether Claude's verdict file for a given stage is present and clean.
#   $1: stage short name — reads ${stage}_audit_verdicts.md
# Returns:
#   0 = file present AND verdict summary shows 0 FAILs
#   1 = file present, has FAILs → stage should mark FAIL
#   2 = file not present → stage stays SHAPE_OK (PENDING)
check_audit_verdicts() {
    local stage="$1"
    local verdicts="$EVAL_OUT/${stage}_audit_verdicts.md"
    if [[ ! -f "$verdicts" ]]; then
        return 2
    fi
    # Summary line format (documented in audit_traces.sh): "AUDIT SUMMARY: N OK / M SUSPICIOUS / K FAIL"
    local summary=$(grep -E "^AUDIT SUMMARY:" "$verdicts" | tail -1)
    if [[ -z "$summary" ]]; then
        echo "WARN: $verdicts has no 'AUDIT SUMMARY:' line — cannot auto-advance."
        echo "      Verify manually, then: echo 'PASS' > $EVAL_OUT/${stage}.marker"
        return 2
    fi
    local n_fail=$(echo "$summary" | grep -oE '([0-9]+)[[:space:]]+FAIL' | grep -oE '^[0-9]+')
    n_fail=${n_fail:-0}
    if [[ "$n_fail" -gt 0 ]]; then
        echo "Audit verdicts show $n_fail FAIL row(s). See: $verdicts"
        return 1
    fi
    return 0
}

# Print the "hand off to Claude" banner that tells the user exactly what to do.
print_audit_handoff() {
    local stage="$1"
    local profile="$2"
    local req="$EVAL_OUT/${stage}_audit_request.md"
    local ver="$EVAL_OUT/${stage}_audit_verdicts.md"
    cat <<EOF

  ────────────────────────────────────────────────────────────────
  AUDIT CHECKPOINT — $stage ($profile)
  ────────────────────────────────────────────────────────────────

  The automated shape/agreement/plausibility checks passed for this stage.
  BEFORE advancing, a Claude+LangFuse audit must confirm the underlying
  traces show real agent work.

  1. Open a Claude session with LangFuse MCP tools (this session works).
  2. Paste the contents of:
         $req
  3. Save Claude's response to:
         $ver
  4. Re-run this stage script. It will read the verdicts and advance the marker.

  (If you want to skip the audit — don't, but if you must: manually write
   'PASS' to $EVAL_OUT/${stage}.marker. The next stage will proceed.)

EOF
}
