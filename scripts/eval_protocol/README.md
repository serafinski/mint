# Eval protocol scripts — with Claude/Codex+LangFuse audit checkpoints

Six stage scripts plus one helper. Audit-gated stages run automated checks, then **refuse to advance** until a Claude/Codex-assisted LangFuse trace audit confirms the data is real. The full specification lives in [`docs/EVAL_PROTOCOL.md`](../../docs/EVAL_PROTOCOL.md).

## The audit checkpoint flow

```
(no marker)
    │
    │ run automated shape / agreement / plausibility checks
    ▼
SHAPE_OK ─── audit request generated at $EVAL_OUT/stage<N>_audit_request.md
    │
    │ you paste request into Claude/Codex; the audit session pulls traces via LangFuse MCP;
    │ you save response to $EVAL_OUT/stage<N>_audit_verdicts.md;
    │ you re-run the stage script
    ▼
PASS  (0 FAILs in audit)   OR   FAIL  (≥1 FAIL or automated gate failed)
```

The next stage's `require_prior_stage` gate refuses to run until the marker is `PASS`. This is the mechanism — not a checklist, an enforced state machine.

## Running

```bash
# Stage 0 — preflight (pytest, pipeline smoke, verification smoke, 3-task longitudinal smoke)
scripts/eval_protocol/stage0_preflight.sh                # creates capture dir
# → runs shape checks, writes stage0.marker = SHAPE_OK, generates stage0_audit_request.md
# → paste stage0_audit_request.md into Claude/Codex+MCP, save response as stage0_audit_verdicts.md
scripts/eval_protocol/stage0_preflight.sh docs/eval_runs/<ts>    # re-run: reads verdicts → PASS

# Stage 1 — main eval (240 records, roughly a day unattended; 32h hard ceiling)
scripts/eval_protocol/stage1_main_eval.sh docs/eval_runs/<ts>    # launch, detached
scripts/eval_protocol/stage1_main_eval.sh docs/eval_runs/<ts>    # next morning: verify + generate audit
# → paste stage1_audit_request.md → save verdicts → re-run → PASS

# Stage 2 — headline M1/M3/M5/ClassAcc with machine-readable two-source agreement
scripts/eval_protocol/stage2_headline.sh docs/eval_runs/<ts>
# → if full agreement, stage auto-PASSes and also writes an optional audit request
# → if any disagreement, audit is mandatory on disagreement rows specifically

# Stage 3 — supplemental recurrence track for Pillar 3
scripts/eval_protocol/stage3_recurrence.sh docs/eval_runs/<ts>    # launch, detached
scripts/eval_protocol/stage3_recurrence.sh docs/eval_runs/<ts>    # verify + audit handoff
# → defaults to the 216-row 4-condition matrix from docs/RECURRENCE_EVAL_PLAN.md
# → override via RECURRENCE_TASKS / RECURRENCE_VARIANTS / RECURRENCE_CONDITIONS / RECURRENCE_RUNS

# Stage 4 — longitudinal M6 (parallel to Stage 2; different DB)
scripts/eval_protocol/stage4_longitudinal.sh docs/eval_runs/<ts>    # launch
scripts/eval_protocol/stage4_longitudinal.sh docs/eval_runs/<ts>    # verify after finish
# → audit on sampled cycles (first / middle / last) → PASS

# Stage 5 — plausibility priors
scripts/eval_protocol/stage5_plausibility.sh docs/eval_runs/<ts>
# → requires Stage 4 PASS (so longitudinal checks are actually in scope)
# → if no INVESTIGATE flags: auto-PASS, no audit needed
# → if flags: audit on the specific flagged rows → PASS or FAIL
```

## The audit checkpoint, explained

Each stage generates a file like `$EVAL_OUT/stage1_audit_request.md`. It contains:

- The capture-dir path and DB path
- The exact LangFuse MCP tool calls the audit session should make (`mcp__langfuse__fetch_trace`, etc.)
- A stratified sample of rows selected for that stage's profile
- Per-row "what to check" guidance
- The exact output format the audit response must use

You paste it into a Claude/Codex session with LangFuse MCP tools. The audit session pulls each trace, reads the actual execution, and returns a verdict table. The mandatory summary line is:

```
AUDIT SUMMARY: 12 OK / 2 SUSPICIOUS / 0 FAIL
```

You save the response as `$EVAL_OUT/stage1_audit_verdicts.md` and re-run the stage script. It greps for that summary line. Zero FAILs → marker flips to `PASS`. Any FAIL → marker flips to `FAIL` and the stage blocks.

## Audit profiles (what each stage samples)

| Stage | Profile | What it audits | Why |
|---|---|---|---|
| 0 | `smoke` | 3-task longitudinal smoke — Pillar 3 events | Verify cycle_flush actually produced real work, not silent empties |
| 1 | `main_sample` | 4 happy-path rows (1/condition) + 2 errors + 2 AGENT successes | Verify real agent work on a stratified sample of the 240 Stage 1 records |
| 2 | `disagreement` | Rows where analyzer and SQL returned different numbers | Decide which side described reality |
| 2 (fallback) | `main_sample` | Same as Stage 1's audit | Optional when analyzer+SQL fully agreed — still recommended |
| 3 | `recurrence_sample` | One happy-path row per recurrence condition + agent/static spot-checks when present | Verify repeated-family runs show real work, not synthetic bookkeeping |
| 4 | `longitudinal_sample` | Events from first, middle, last cycle | Verify Pillar 3 ops (split/merge/prune) actually executed |
| 5 | `plausibility_flagged` | Rows corresponding to Stage 5's INVESTIGATE flags | Confirm or refute the flags with trace evidence |

## Tunables

```bash
EXPECTED_CYCLES=60 FLOOR_PER_CYCLE=8 scripts/eval_protocol/stage4_longitudinal.sh <path>
```

## If a stage fails

Each stage's marker records a one-line reason. Inspect logs in the capture dir. To retry a stage, delete its marker: `rm $EVAL_OUT/stage<N>.marker`. Do not bypass `require_prior_stage`.

## Manual override (escape hatch)

If you need to skip an audit (you've read the traces by hand, or the audit tooling is broken and you can't afford to debug it), manually write the PASS marker:

```bash
echo PASS > $EVAL_OUT/stage<N>.marker
```

Use sparingly — this is why the automated gates exist. Every use of this escape hatch is an implicit disclosure you should mention in Chapter 6.

## The four defense layers

Each layer catches a different failure class. The audit layer (Claude/Codex+MCP) is the critical addition — without it, the scripts only catch shape + agreement + plausibility, which misses "numbers look fine, agent fabricated output."

| Layer | Mechanism | Catches | Misses |
|---|---|---|---|
| **Shape** | Stage 1/4 completeness queries | missing rows, NULL labels, dupes | values wrong but shaped right |
| **Agreement** | Stage 2/4 two-source cross-check | analyzer bugs | both sides wrong the same way |
| **Plausibility** | Stage 5 priors | impossibly-shaped numbers | plausible-but-wrong numbers |
| **Semantic (trace)** | Claude/Codex+LangFuse audit | "agent fabricated output", "tool no-op", "silent swallow" | cohort-level systematic effects (advisor / committee catches these) |

All four + one advisor read = thesis-level defensibility. That is the whole plan.
