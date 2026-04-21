# Security / Robustness Audit — 2026-04-19, Ninth Extension (#79-#98)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md) — #1-#18 all ✅ FIXED 2026-04-20.
**Sibling extensions:**
- [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) — #12-#14 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext5.md`](./security_audit_2026_04_19_ext5.md) — #15-#18 ✅ (folded into parent).
- [`security_audit_2026_04_19_ext6.md`](./security_audit_2026_04_19_ext6.md) — #19-#43 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext7.md`](./security_audit_2026_04_19_ext7.md) — #44-#55 ✅ all FIXED 2026-04-20.
- [`security_audit_2026_04_19_ext8.md`](./security_audit_2026_04_19_ext8.md) — #56-#78 ✅ all FIXED 

**This extension's scope.** A reviewer submitted 20 claims (their numbering #81-#100). 7 distinct findings independently confirmed and added as #79-#85 below. Each new finding consolidates one-to-three user claims that share a root cause (e.g., #83 covers user claims #89+#90+#91 — the same pip-packages-persistence chain across three layers). 13 user claims are either (a) not reproducible, (b) already-fixed by prior extensions, (c) documented design choices, or (d) subsumed by an existing ext8 finding — each logged in §"Claims not added" for audit trail.

**Status at time of write (2026-04-20):** all nineteen **open**. **ext8 remains open** (#56-#78). ext9 findings #79-#97 are additive — total open count after ext9 lands: 42 (ext8 + ext9). First batch (#79-#85), second batch (#86-#96), third batch (#97) all same-day.

**Status re-verified 2026-04-21: ALL TWENTY ✅ FIXED.** Every ext9 finding (#79-#98) carries a `Security audit finding #NN (ext9)` comment in code pinning the remediation. Spot-verification sample: #79 `analyze_results.py:283`, #80 :642, #81 :679, #82 `run_longitudinal_shift.py:518`, #83 schema/store/composition chain complete (`capability.py:61` + `store.py:130,182` + `composition.py:417,513`), #84 `analyze_results.py:1275`, #85 :570+:1481, #86 `run_longitudinal.py:186-192` + `run_longitudinal_shift.py:291-292`, #87 runners' session_id, #88 `test_pipeline.py:106`, #89 `decomposition.py:772-811` (subtask pruning + depends_on remap), #90 :635, #91 `composition.py:522` + `execution.py:737+756`, #92 `decomposition.py:211+921`, #93 `composition.py:1009`, #94 `analyze_context_utilization.py:288`, #95 `harness.py:1093`, #96 `analyze_results.py:906`, #97 `harness.py:1676` T19 dispatch branch + :1843 drift guard, #98 `decomposition.py:416` writer bare-filename fix. Cross-ext11/ext12 stale claims corrected — no ext9 finding is actually open.

| # | User's # | Severity | Scope | Impact |
|---|---|---|---|---|
| 79 | 81 | MEDIUM | `analyze_results.py` Table 1 column semantics | "Success" (shape-checked) vs "Output OK" (not shape-checked) disagree; headline numbers look contradictory |
| 80 | 82 | MEDIUM | `analyze_results.py` JIT Gap calculation | Gap uses worst JIT ablation as lower bound; systematically under-sells full-system benefit |
| 81 | 83 | MEDIUM | `analyze_results.py` Fisher pooled-JIT test | Pools non-exchangeable ablation conditions; inflates significance. Disclosed in comment but published anyway |
| 82 | 88 | P1 | `run_longitudinal_shift.py` shift marker timing | Marker occurred_at > last Phase A task's end → lands in cycle N+1 bucket → adaptation verdict off-by-one |
| 83 | 89+90+91 | **P0** | `pip_packages` persistence chain across three layers | `CapabilityDescriptor` lacks field, `SkillRegistry` doesn't persist, `_match_to_artifact` hardcodes `[]`; registry-matched MCP tools crash on ImportError |
| 84 | 97+98 | MEDIUM | `analyze_results.py` M2 partial-label handling | `n_contam / n_sampled` excludes "partial" → partially-contaminated tools counted as clean; contradicts proposal's "fraction containing fabricated elements" |
| 85 | 99+100 | MEDIUM | `analyze_results.py` failure breakdown denominator | Table 6 reports counts not rates; identical counts across conditions with different N look identical when they aren't |
| 86 | 101+102 | **P0 CRITICAL** | `run_longitudinal*.py` rmtree of `src/mcp_tools` (project source) | ✅ **FIXED 2026-04-21** (status corrected post-audit). `scripts/run_longitudinal.py:186-192` + `scripts/run_longitudinal_shift.py:291-292` explicitly comment that `MCP_TOOLS_DIR` is project source and exclude it from the rmtree loop. `MCP_TOOLS_DIR` is now used only in `take_clean_snapshot` / `restore_clean_snapshot` for the project-source snapshot. Earlier ext11/ext12 cross-references claimed "still open per verification" — that was stale; current code has the fix. |
| 87 | 103 | MEDIUM | `run_longitudinal*.py` session_id derived only from seed | Two runs with same `--seed` pool into one LangFuse session; cross-run separation impossible |
| 88 | 104 | MEDIUM | `test_pipeline.py` doesn't use `build_initial_state` | Integration smoke skips contract injection + writer-synthesis → tests a simpler path than production |
| 89 | 118 | **P0** | `decomposition.py` truncates `required_capabilities` but keeps all subtasks | Subtasks referencing dropped capabilities crash at execution; MAX_CAPS_HARD=8 silent drop |
| 90 | 120 | P1 | Decomposition retry loop doesn't trigger on DAG violations | DAG validator fires AFTER retry exhaustion; invalid DAGs proceed with flipped sticky flag but no regeneration |
| 91 | 129 | MEDIUM | `execute_mcp_tool_in_chain` loads ALL `step_*_output.json` | Parallel branches see each other's state; depends_on ignored; untraceable cross-branch corruption |
| 92 | 132 | MEDIUM | `CompositionalExecutor._resolve_step` returns first resolving capability only | Multi-capability subtasks have all but first silently ignored |
| 93 | 134 | MEDIUM | `run()` only checks terminal step for `_output_file_content` | Intermediate SKILL/AGENT files lost; file-output capability restricted to last step |
| 94 | 141 | MEDIUM | `analyze_context_utilization.py` reports per-agent peak, not chain total | Pillar 3 "Context Economy" cost systematically under-reported in multi-agent chains |
| 95 | 158 | MEDIUM | `_extract_result` falls back to single-artifact when only one artifact exists | Lead-agent serving N subtasks gets subtasks 2..N silently skipped |
| 96 | 172 | MEDIUM | Gini excludes zero-invocation active capabilities | `_gini_coefficient` only sees invoked-at-least-once caps → "1 cap used 100 times" reports Gini=0 instead of near-1 |
| 97 | 195 | **P0** | `_run_static_implementation` whitelist doesn't cover all `has_static_coverage=True` tasks | T19 declares static coverage but has no implementation branch → ValueError on every static-condition run of T19 |
| 98 | (ext9 verify) | P1 | Writer-subtask description uses bare filename; LLM emits `open("/<filename>")` | Production CLI hits `PermissionError: '/report.md'` on file-output prompts that name files without `/workspace/` (T16-shape). Surfaced post-#92-downgrade by test_mint AGENT-YAML rerun. Affects decomposer-emitted writers AND `_ensure_file_writer_last_step`-synthesized writers. |

**Numbering.** Continues sequentially from ext8's #78. User's local numbering preserved in "User's #" column. First batch #79-#85 landed; second batch #86-#96 added same-day.

---

## Issue #79 — Table 1 "Success" and "Output OK" columns apply different correctness predicates; headline numbers look contradictory (MEDIUM)

### Summary

`scripts/analyze_results.py::query_condition_summary` computes three columns:

```python
SUM(CASE WHEN gate_passed AND output_in_range AND NOT COALESCE(output_shape_mismatch, FALSE) THEN 1 ELSE 0 END) AS successes,
SUM(CASE WHEN gate_passed THEN 1 ELSE 0 END) AS gate_passed,
SUM(CASE WHEN output_in_range THEN 1 ELSE 0 END) AS output_in_range
```

- "successes" (Table 1's primary metric, per ext6 #32 fix) includes the `NOT output_shape_mismatch` tightening.
- "output_in_range" column (reported separately in Table 1 header at line 340) is the raw `output_in_range` without the shape check.

Result: a reader of Table 1 sees something like "Success: 50% | Gate Pass: 95% | Output OK: 90%" for a condition with many shape-mismatched rows. The "90% Output OK but only 50% success" disparity is real (shape-mismatched rows pass the validator but fail the contract) — but the reader sees three percentages without any column explaining the gap.

### Where

- **Query (inconsistent predicates):** `scripts/analyze_results.py:251-253` (and duplicate at 279-281).
- **Table header (no explanation of gap):** `scripts/analyze_results.py:340`:
  ```python
  header = f"{'Condition':<22} {'Success':>14} {'95% CI':>18} {'Gate Pass':>14} {'Output OK':>14}"
  ```

### Reproduction

```
Condition           Success       95% CI   Gate Pass    Output OK
─────────────────────────────────────────────────────────────────
JIT + Verif + Opt   50.0% [...]      95.0%       90.0%
                    ↑                            ↑
                    includes shape check          doesn't include shape check

Reader: "Why is Success so much lower than Output OK? Both are correctness metrics."
```

No column explains the 40-percentage-point gap.

### Threat model / impact

- **Reader confusion → Chapter 6 narrative risk.** The disparity looks like a bug in the reporting; reviewers may flag it during defense.
- **Direction of bias: none (numbers are correct)** — just confusingly labeled.
- **Compounds with ext6 #32's fix messaging.** #32 correctly tightened the success predicate; #79 is that the sibling columns weren't also updated for consistency.

### Remediation

**A. Either unify both columns OR add a third column explaining the gap.**

Option A1 — tighten "Output OK" the same way:
```python
SUM(CASE WHEN output_in_range AND NOT COALESCE(output_shape_mismatch, FALSE) THEN 1 ELSE 0 END) AS output_in_range_shape_checked
```

Option A2 — add a "Shape Mismatch" column:
```python
SUM(CASE WHEN output_shape_mismatch THEN 1 ELSE 0 END) AS shape_mismatch
# Table header:
header = f"... {'Gate Pass':>14} {'Output OK':>14} {'ShapeMismatch':>14}"
```

Reader can see: "Output OK=90%, ShapeMismatch=40%, Success=50%" → math adds up.

A1 is preferred because it makes the three columns tell a coherent story. A2 is more informative but adds a column.

**B. Regression test.**

```python
def test_table1_columns_use_consistent_predicates(tmp_path):
    """Success ≤ Output OK ≤ n_total must hold when shape check is applied to both."""
    ...
```

### Recommendation

**Land as Chapter 6 hygiene.** ~15 LOC + 1 test. ~15 min.

---

## Issue #80 — "JIT Gap" uses worst JIT ablation as the lower bound; systematically under-sells full-system benefit (MEDIUM)

### Summary

`scripts/analyze_results.py::print_cluster_analysis` reports the JIT-vs-static gap as a range:

```python
jit_rates = [d["successes"] / d["n"] for d in jit_conds if d["n"] > 0]
jit_min = min(jit_rates) * 100
jit_max = max(jit_rates) * 100
...
lines.append(f"    Gap:          {jit_min - static_rate:.1f} – {jit_max - static_rate:.1f} pp")
```

The "JIT Gap" lower bound is the WORST-performing ablation condition (`no_verification`, `no_optimization`, etc.). The upper bound is the full system. Reporting the range `[worst_ablation - static, full - static]` is honest in one sense (the cluster interpretation is recommended by §cluster_analysis) — but it conflates "the full system's improvement" with "the weakest ablation's improvement."

For Chapter 6's "does JIT beat static?" headline, this systematically understates the full system. A reader skimming the table sees "Gap: 2-15pp" when the full-system-only gap might be 15pp.

### Where

- **Gap calculation:** `scripts/analyze_results.py:530-537`.
- **Cluster framing rationale:** `scripts/analyze_results.py:516` — "Critique Recommendation: cluster, not ranking".

### Reproduction

Synthetic:
- `full` success rate: 80%
- `no_verification`: 65%
- `no_optimization`: 70%
- `static`: 65%

Reported Gap: `0 – 15 pp` (0 being `no_verification - static`).

The "full - static = 15pp" is buried in the upper bound. A hurried reader focuses on the lower-bound pessimistic number.

### Threat model / impact

- **Chapter 6's headline comparison is misleadingly conservative.** If the reviewer reads "JIT gap: 0-15pp" they infer the JIT advantage is marginal; if they read "full JIT gap: 15pp, weakest ablation gap: 0pp" they infer the full system is the meaningful comparison target.
- **Direction of bias: anti-JIT.** Under-sells the thesis's claim.
- **Intentional methodology choice disclosed in §print_cluster_analysis** — the "cluster vs ranking" framing is deliberate. But the output format doesn't signpost that the lower bound is a weak-ablation anchor, not a "minimum improvement of the system."

### Remediation

**A. Report the full-system gap separately, AND the ablation spread.**

```python
# Existing cluster report:
lines.append(f"    JIT cluster:  {jit_min:.1f}% – {jit_max:.1f}%")
lines.append(f"    Static:       {static_rate:.1f}%")
lines.append(f"    Gap (cluster): {jit_min - static_rate:.1f} – {jit_max - static_rate:.1f} pp")

# NEW: full-system-only gap, explicitly.
full_cond = next((d for d in jit_conds if d["condition"] == "full"), None)
if full_cond and full_cond["n"] > 0:
    full_rate = full_cond["successes"] / full_cond["n"] * 100
    lines.append(f"    Gap (full system vs static): {full_rate - static_rate:.1f} pp")
```

Reader sees: "Gap (cluster): 0–15pp. Gap (full vs static): 15pp." The asymmetric framing is honest.

**B. Regression test.**

```python
def test_cluster_analysis_reports_full_system_gap_separately():
    """Full-system gap should appear as its own line, not just as upper bound of cluster range."""
    ...
```

### Recommendation

**Methodology-disclosure level. ~10 LOC + 1 test. ~15 min.**

---

## Issue #81 — Pooled-JIT Fisher test is methodologically fallacious but printed anyway with a disclaimer (MEDIUM)

### Summary

`scripts/analyze_results.py:563-567`:

```python
_, p_pooled = fisher_exact_2x2(
    <sum of all JIT conditions' successes/n>,
    static_successes, static_n,
)
print(
    f"  Pooled JIT vs Static (NOTE: pooling inflates significance): "
    ...
)
```

Pooling `full + no_verification + no_optimization` into a single "JIT" bucket and running Fisher's exact treats three non-exchangeable conditions as samples of the same population. This is a statistical fallacy: the three conditions are three different interventions, not three independent samples of one intervention. The pooled p-value overstates significance because N is inflated (3×) by samples that don't share an underlying distribution.

The code comment ("pooling inflates significance") acknowledges this — but the number IS printed and could be quoted out of context. Chapter 6 readers who copy the p-value without the surrounding context misrepresent the statistical claim.

### Where

- **Pooled test:** `scripts/analyze_results.py:563-567`.
- **Already-disclosed in inline comment** (line 567 printed output).

### Threat model / impact

- **Statistical-correctness risk in Chapter 6 tables.** Thesis committee or peer reviewer sees "p < 0.01 (pooled)" and treats it as supporting the thesis claim, not as a cautionary note.
- **Direction of bias: pro-JIT.** Inflated N → smaller p-value → looks more significant than the within-condition tests (which are the proposal-correct tests).
- **Already has a disclaimer.** So it's a presentation-risk concern, not a hidden bug.

### Remediation

**A. Remove the pooled test entirely; report only per-condition Fisher + Holm-corrected.**

```python
# Don't compute or print the pooled test. The per-condition tests (with
# Holm-Bonferroni correction) are the proposal-correct methodology.
# Dropping the pooled test avoids misquote risk.
```

**B. Or: downgrade to a diagnostic section labeled "illustrative only, do not cite".**

```python
print("\n===== DIAGNOSTIC ONLY (DO NOT CITE) =====")
print("  Pooled JIT p-value (statistically fallacious — see Chapter 4.5):")
print(f"    p = {p_pooled:.4f}")
print("===== END DIAGNOSTIC =====\n")
```

(A) is cleaner. (B) preserves the internal tooling if team wants it but signposts that it's not a thesis-quotable number.

### Recommendation

**Remove or downgrade.** ~5 LOC. ~10 min.

---

## Issue #82 — `run_longitudinal_shift.py` shift marker `occurred_at` lands in cycle N+1 window; adaptation verdict reads the marker one cycle late (P1)

### Summary

`scripts/run_longitudinal_shift.py:400-422` writes the `distribution_shift` cycle_events row AFTER the last Phase A task completes + its per-task flush finishes. The marker's `occurred_at` is therefore strictly after Phase A's last `evaluation_runs.timestamp`, and STRICTLY before Phase B's first task's timestamp.

The comment at line 400-406 frames this as intentional ("occurred_at is strictly after all Phase A events but before the first Phase B task's events"). But the analyzer buckets events into per-cycle windows using task_boundaries from `evaluation_runs.timestamp` (see ext8 #56 for the off-by-one analysis of task-end timestamps). Under the half-open-interval fix for #56, the marker's `occurred_at` ∈ (last_Phase_A.timestamp, first_Phase_B.timestamp) lands in cycle N+1's bucket — one cycle late.

Consequence: `analyze_longitudinal.py`'s adaptation-verdict logic expects the shift marker at cycle N (the last Phase A cycle) but finds it at cycle N+1 (the first Phase B cycle). The "shift-detection point" is off by one. For the three-tier verdict (graceful/sluggish/collapse), this shifts the "Phase B re-convergence" window start by one cycle, making fast-adapting systems look slower than they are.

### Where

- **Shift marker emission (timestamp > last Phase A task):** `run_longitudinal_shift.py:400-422`.
- **Attribution via `occurred_at`:** depends on ext8 #56's per-cycle bucketing logic.
- **Consumer:** `analyze_longitudinal.py`'s `_detect_shift_point` + `_classify_adaptation`.

### Reproduction

Direct trace of the code: the marker is written at line 422 with no explicit timestamp; `record_cycle_events` defaults `occurred_at=CURRENT_TIMESTAMP`, which at that point in execution is strictly after the Phase A task's evaluation_runs row was written.

### Threat model / impact

- **Step 18.8 adaptation verdict biased toward "sluggish".** One-cycle offset means the "Phase B start" the analyzer detects is actually Phase B cycle 1; the analyzer's "cycles-to-re-converge" counter starts one cycle late.
- **Compounds with ext8 #56.** Fixing #56's general cycle attribution without ALSO fixing the shift marker's intentional post-task timing leaves this specific marker still mis-attributed.

### Remediation

**A. Write the marker with `occurred_at` explicitly set to the Phase A task's timestamp.**

```python
# scripts/run_longitudinal_shift.py:413-422 — before:
eval_store.record_cycle_events(
    run_id=run_id,
    condition="longitudinal",
    task_id=task.task_id,
    events=[{...}],
)

# after — pass the last Phase A task's timestamp so the marker lands in Phase A's cycle:
last_phase_a_timestamp = <fetch from evaluation_runs where (run_id, task_id) matches>
eval_store.record_cycle_events(
    run_id=run_id,
    condition="longitudinal",
    task_id=task.task_id,
    occurred_at=last_phase_a_timestamp,   # NEW
    events=[{...}],
)
```

Requires `record_cycle_events` to accept an explicit `occurred_at` (may already, depending on schema). If it defaults to CURRENT_TIMESTAMP without a kwarg override, add the kwarg.

**B. Alternative: write the marker BEFORE the last Phase A task runs.**

Emit the `distribution_shift` event at the START of cycle `shift_at + 1` (Phase B's first cycle) instead of at the END of cycle `shift_at` (Phase A's last cycle). Clearer intent: the shift happens BEFORE the first Phase B task, and the marker timestamp reflects that. Analyzer can detect the marker at the beginning of Phase B without fighting with end-of-phase-A boundaries.

(A) preserves the current semantic intent (marker belongs to Phase A's end); (B) re-interprets it (marker belongs to Phase B's start). Either is defensible; the code should be consistent with whichever interpretation the analyzer uses.

**C. Regression test.**

```python
def test_shift_marker_lands_in_correct_cycle_bucket():
    """A shift marker emitted at shift_at=5 should be attributed to cycle 5, not cycle 6."""
    # Seed synthetic DB; call _load_cycle_data post-ext8-#56-fix.
    # Assert the shift marker is in cycles[4]['events'], not cycles[5]['events'].
    ...
```

### Recommendation

**Bundle with ext8 #56's cycle-attribution fix.** Same consumer (`_load_cycle_data`), same bucketing logic. ~20 LOC + 1 test. ~30 min combined.

---

## Issue #83 — `pip_packages` is lost across three layers; registry-matched MCP tools crash on ImportError at invocation (P0)

### Summary

MCP tool generation produces a `pip_packages: list[str]` on the `GeneratedArtifact` — the exact PyPI names the generator LLM declared. At registration time, `_register_artifact_directly` (or `ToolLoader.register_tool`) builds a `CapabilityDescriptor`. The pip_packages data is **lost at three layers**:

1. **`CapabilityDescriptor` has no `pip_packages` field** (`src/schemas/capability.py:15`). The schema simply doesn't carry it.
2. **`SkillRegistry` doesn't persist `pip_packages` in ChromaDB metadata** (`src/registry/store.py` — grep for `pip_packages` returns nothing).
3. **`CompositionalExecutor._match_to_artifact` hardcodes `pip_packages = []`** when reconstructing a `GeneratedArtifact` proxy from a registry match (`src/orchestrator/composition.py:500`).

Net effect: a tool generated with `pip_packages=["pandas", "openpyxl"]`, registered, and retrieved in a later task's registry match loses ALL dependency information. The executor passes `extra_packages=[]` to `sandbox_execute`; the sandbox runs without installing pandas/openpyxl; the tool's `import pandas` raises `ModuleNotFoundError`. Chain fails with a generic "execution error" attributable to the tool — not to the pip-packages-lost-in-registry bug.

### Where

- **Layer 1 — schema gap:** `src/schemas/capability.py:15` (`class CapabilityDescriptor(BaseModel):` has no `pip_packages` field).
- **Layer 2 — persistence gap:** `src/registry/store.py` — `_build_metadata` + `add` don't serialize `pip_packages`; nothing in ChromaDB metadata carries it.
- **Layer 3 — hardcoded empty list on match:** `src/orchestrator/composition.py:500`:
  ```python
  pip_packages = artifact_or_match.pip_packages   # line 496, for generated artifacts
  ...
  pip_packages = []                                # line 500, for registry matches
  ```
- **Executor passes it downstream:** `src/orchestrator/composition.py:512` — `pip_packages=pip_packages` into `sandbox_execute_in_workspace`.

### Reproduction

```python
# Run 1: generate a tool with pip_packages=["pandas"].
artifact_1 = generate_mcp_tool(..., task="analyze sales CSV", ...)
assert artifact_1.pip_packages == ["pandas"]
registry.add_from_artifact(artifact_1)   # stored, but pip_packages dropped

# Run 2 (different task, same registry): registry match retrieves the tool.
ref, match = resolve(task="analyze customer CSV")
# _match_to_artifact reconstructs:
art = _match_to_artifact(match, ref)
assert art.pip_packages == []           # WRONG — was ["pandas"] at generation time

# Executor runs with extra_packages=[]:
sandbox_execute_in_workspace(
    code=art.source_code,                # contains `import pandas`
    workspace_path=ws,
    extra_packages=[],                   # nothing installed
)
# → stderr: ModuleNotFoundError: No module named 'pandas'
```

### Threat model / impact

- **Any registry-matched tool with non-trivial dependencies crashes at runtime.** This breaks cross-task reuse — exactly the feature Pillar 1 claims.
- **Chapter 6 M1 depressed on reuse scenarios.** Tasks that should have succeeded via registry reuse fail because dependencies are lost; the analyzer attributes the failure to "chain_step_failed" instead of "registry dependency loss."
- **Direction of bias: anti-reuse.** The thesis's "generate once, reuse many times" claim is structurally broken for any tool needing more than the base image's dependencies.
- **Compounds with ext8 #74** (pip install timeout) — #74 is about dependency install being TOO SLOW; #83 is about dependency info being LOST before install can even be attempted.

### Remediation

**All three layers need fixes; they chain.**

**A. Add `pip_packages` to `CapabilityDescriptor`.**

```python
# src/schemas/capability.py:
class CapabilityDescriptor(BaseModel):
    ...
    pip_packages: list[str] = Field(
        default_factory=list,
        description="PyPI packages required by MCP_TOOL execution. Empty for SKILL/AGENT.",
    )
```

**B. Persist `pip_packages` in ChromaDB metadata.**

```python
# src/registry/store.py::_build_metadata — add:
if getattr(capability, "pip_packages", None):
    metadata["pip_packages"] = json.dumps(capability.pip_packages)

# src/registry/store.py::_rehydrate — add:
if "pip_packages" in meta:
    try:
        kwargs["pip_packages"] = json.loads(meta["pip_packages"])
    except Exception:
        kwargs["pip_packages"] = []
```

ChromaDB metadata accepts only primitives; JSON-encoding the list-of-strings as a VARCHAR is the standard workaround (same pattern as `agent_config`).

**C. Read `pip_packages` from the match in `_match_to_artifact`.**

```python
# src/orchestrator/composition.py:500 — before:
pip_packages = []

# after:
pip_packages = list(getattr(match.capability, "pip_packages", []) or [])
```

**D. Regression tests.**

```python
def test_capability_descriptor_preserves_pip_packages_round_trip():
    cap = CapabilityDescriptor(name="x", pip_packages=["pandas"], ...)
    assert cap.pip_packages == ["pandas"]

def test_skill_registry_persists_pip_packages(tmp_path):
    registry = SkillRegistry(tmp_path)
    registry.add_from_artifact(make_artifact(pip_packages=["pandas", "openpyxl"]))
    matched = registry.search(...)
    assert matched[0].capability.pip_packages == ["pandas", "openpyxl"]

def test_compositional_match_to_artifact_restores_pip_packages():
    match = make_match_with_pip_packages(["pandas"])
    art = CompositionalExecutor._match_to_artifact(match, ref)
    assert art.pip_packages == ["pandas"]

def test_registry_matched_tool_installs_required_packages(tmp_path):
    """End-to-end: generate → register → retrieve → execute. Dependencies survive."""
    ...
```

### Recommendation

**P0 — blocking Phase-5 eval for anything involving registry reuse.** The fix is a three-layer patch (~40 LOC) + four regression tests (~30 LOC). ~1-1.5h.

Legacy data: existing ChromaDB entries have no `pip_packages` metadata. Post-fix, they continue to report `[]` — the tools that were generated with dependencies will still fail at retrieval time. Either (a) regenerate from scratch (clean baseline), or (b) run a one-shot backfill that re-extracts pip_packages from each stored `source_code` via `extract_imports()` (the same helper used by the fallback path in `sandbox_tool.py`). (b) is cleaner for legacy data.

---

## Issue #84 — M2 contamination rate excludes "partial" labels from numerator; contradicts proposal's "fraction containing fabricated elements" definition (MEDIUM)

### Summary

`scripts/analyze_results.py::print_contamination_rate` at line 1063:

```python
rate = 100.0 * n_contam / n_sampled if n_sampled else 0.0
```

Where `n_contam` counts only rows with `human_label='contaminated'` (line 1028). Rows labeled `'partial'` (line 1029, `SUM(... WHEN human_label = 'partial' ...) AS n_partial`) are tallied separately and reported alongside — but NOT added to the numerator.

The proposal §Metrics defines M2 as: *"fraction containing fabricated elements"*. A `partial` label means the tool IS partially contaminated — it contains SOME fabricated elements. Excluding it from the numerator measures "fully contaminated / sampled" which is a stricter definition than the proposal's. Result: M2 under-reports the fraction of tools with any fabrication.

### Where

- **Numerator definition:** `scripts/analyze_results.py:1028, 1063`.
- **`n_partial` reported separately:** line 1029 + line 1068 (displayed but not summed).
- **Proposal text:** `docs/thesis_proposal.md` §Metrics M2 — "fraction containing fabricated elements" (any fabrication counts, per the natural reading).

### Reproduction

```
Sample: 30 tools labeled
  - 10 "correct"
  - 5 "contaminated"
  - 15 "partial"

Current formula: rate = 5/30 = 16.7%
Proposal-literal formula: rate = (5 + 15)/30 = 66.7%
```

Near-50-point gap depending on label distribution.

### Threat model / impact

- **Chapter 6 M2 reported value disagrees with proposal definition by ~3-4x depending on partial-label frequency.** Reviewer cross-checking the proposal text finds the mismatch.
- **Direction of bias: anti-detection.** Reports cleaner contamination than proposal's methodology would report.
- **Compounds with ext8 #72 (probe bypass via timestamp variation)** and ext8 #57 (resume sampling bias). Three layers of M2 under-reporting.

### Remediation

**A. Include partial in the numerator.**

```python
# scripts/analyze_results.py:1063 — before:
rate = 100.0 * n_contam / n_sampled if n_sampled else 0.0
# after:
rate = 100.0 * (n_contam + n_partial) / n_sampled if n_sampled else 0.0
```

**B. Or: report two rates separately with clear labels.**

```
M2 Contamination Rates:
  Strict  (contaminated only):        5/30 = 16.7%
  Proposal (contaminated + partial): 20/30 = 66.7%   ← headline per proposal definition
```

A reviewer sees both numbers with clear framing. Chapter 6 tables cite the proposal-literal number; the "strict" number is a diagnostic refinement.

**C. Clopper-Pearson CI needs to be recomputed with the new numerator.**

```python
lo, hi = _clopper_pearson_ci(int(n_contam + n_partial), int(n_sampled))
```

**D. Regression test.**

```python
def test_m2_contamination_includes_partial_labels():
    # Seed DB with 5 contaminated, 15 partial, 10 correct.
    # Assert contamination_rate == 66.7%, not 16.7%.
    ...
```

### Recommendation

**Land with Chapter 6 methodology review.** ~10 LOC + 1 test. ~15 min. Chapter 4.5 should disclose which definition is used.

---

## Issue #85 — Failure Breakdown table reports counts not rates; identical counts across conditions with different N look equivalent when they're not (MEDIUM)

### Summary

`scripts/analyze_results.py::print_failure_breakdown` (lines 496-509) prints:

```
Condition             Reason                 Count
──────────────────────────────────────────────────
full                  chain_step_failed      5
static                chain_step_failed      5
```

No total-N column. No rate. A reader sees "identical failure counts across two conditions" — but if `full` has N=100 and `static` has N=20, the actual failure rates are 5% vs 25% — a 5× gap that's invisible in the table.

Missing denominator is a well-known reporting hazard; the proposal's Figure 6.4 (Failure Taxonomy) is one of the headline diagrams Chapter 6 will reference.

### Where

- **Print function without denominator:** `scripts/analyze_results.py:496-509`.
- **Query without N join:** `scripts/analyze_results.py:304` — `SELECT condition, failure_reason, COUNT(*) AS n ... GROUP BY condition, failure_reason`.

### Threat model / impact

- **Chapter 6 Figure 6.4 misrepresents rare-mode prevalence.** Without rates, readers can't compare across conditions.
- **Direction of bias: depends on which conditions have higher N.** If JIT has more tasks than static (which it does — static is bypassed for tasks without static coverage), JIT's failure counts look "as bad as" static's even when JIT's rate is lower.

### Remediation

**A. Add total-N per condition, compute rates.**

```python
# scripts/analyze_results.py::print_failure_breakdown — rewrite to include rates:
def print_failure_breakdown(failures: list[dict], condition_totals: dict[str, int]) -> None:
    lines = [
        f"\n{'='*80}",
        "  Failure Breakdown by Condition",
        f"{'='*80}",
    ]
    header = f"{'Condition':<22} {'Reason':<22} {'Count':>6} {'N':>6} {'Rate':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    for d in failures:
        label = CONDITION_LABELS.get(d["condition"], d["condition"])
        total = condition_totals.get(d["condition"], 0)
        rate = 100.0 * d["n"] / total if total else 0.0
        lines.append(f"{label:<22} {d['reason']:<22} {d['n']:>6} {total:>6} {rate:>7.1f}%")
    for line in lines:
        print(line)
```

Caller passes `condition_totals` from `query_condition_summary`'s already-computed `n` column.

**B. Regression test.**

```python
def test_failure_breakdown_shows_rates_not_just_counts(capsys):
    """A condition with N=100, 5 failures should show '5.0%' not just '5'."""
    failures = [{"condition": "full", "reason": "chain_step_failed", "n": 5}]
    condition_totals = {"full": 100}
    print_failure_breakdown(failures, condition_totals)
    out = capsys.readouterr().out
    assert "5.0%" in out
```

### Recommendation

**Methodology hygiene. ~15 LOC + 1 test. ~20 min.** Paired with ext8's #60 (per-event-severity vs per-category-severity-hits) as part of the Chapter 6 reporting-clarity pass.

---

## Issue #86 — `run_longitudinal.py` and `run_longitudinal_shift.py` rmtree `src/mcp_tools` (the project source directory) before snapshotting (P0 CRITICAL)

### Summary

Ext6 #31's remediation introduced a pre-snapshot `rmtree` loop to ensure a clean baseline. The loop iterates three paths:

```python
# run_longitudinal.py:137
for _d in (CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR):
    if _d.exists():
        _shutil.rmtree(_d)
    _d.mkdir(parents=True, exist_ok=True)
```

Where `MCP_TOOLS_DIR = ROOT / "src" / "mcp_tools"` (line 48). This is the **project's source directory** for reference hand-built MCP tools (per CLAUDE.md: "Reference hand-built MCP tools (fibonacci)"). It is tracked in git. Running `run_longitudinal.py` deletes this directory, the `fibonacci.py` reference tool (and any other seeded reference tools) along with it.

Under current architecture (per CLAUDE.md's "MCP Tool Loading Strategy"), generated MCP tools are stored in ChromaDB as `source_code` metadata — NOT on disk under `src/mcp_tools`. So `src/mcp_tools` has no runtime-accumulating state to clean. The ext6 #31 fix intended to clean eval-state directories (ChromaDB + skills) but over-included the project-source directory.

### Where

- **Destructive rmtree:** `scripts/run_longitudinal.py:137-139` + `scripts/run_longitudinal_shift.py:250-252`.
- **Path constant (project source):** `run_longitudinal.py:48` + `run_longitudinal_shift.py:77` — `MCP_TOOLS_DIR = ROOT / "src" / "mcp_tools"`.
- **Git-tracked contents:** `src/mcp_tools/fibonacci.py` (reference tool per CLAUDE.md).

### Reproduction

```bash
# Before: project has src/mcp_tools/fibonacci.py (tracked in git).
$ ls src/mcp_tools/
fibonacci.py  __init__.py

# Run longitudinal:
$ uv run scripts/run_longitudinal.py
# Line 137-139 deletes src/mcp_tools/ then recreates as empty.

# After:
$ ls src/mcp_tools/
# Empty directory. fibonacci.py is GONE.

# Subsequent project operations that reference fibonacci (tests, CLI, smoke runs) fail.
$ git status
# modified: src/mcp_tools/ (deleted files)
```

### Why this wasn't caught by ext6 #31 tests

Ext6 #31 was tested against contaminated ChromaDB + skills directories where the rmtree is correct. `src/mcp_tools` tests never covered the git-tracked-source scenario because smoke tests don't typically commit, run longitudinal, and then check git status. The fix's test coverage missed the "project source directory is ALSO in the rmtree list" interaction.

### Threat model / impact

- **Immediate data loss on every longitudinal run.** A user following the run sheet (row 9 or row 10) loses their seed-tool source.
- **Subsequent smoke tests + Phase 3 gates fail.** `test_pipeline.py` and other tests that reference `src/mcp_tools/fibonacci.py` crash with `ImportError` or `FileNotFoundError` after the longitudinal run.
- **Recovery requires `git checkout src/mcp_tools/`** — which the user has to remember to do. Running `git add -A` on a dirty tree after the rmtree would stage the deletion.
- **18-hour longitudinal campaigns abort after registry lookup failures** because the seeded fibonacci is gone.
- **Compounds with ext6 #31's framing:** the fix was "dirty baseline → clean baseline"; actual implementation over-reached into project source. Remediation is straightforward but urgent.

### Remediation

**A. Remove `MCP_TOOLS_DIR` from the rmtree loop; only clean eval state.**

```python
# scripts/run_longitudinal.py:137 — before:
for _d in (CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR):
    if _d.exists():
        _shutil.rmtree(_d)
    _d.mkdir(parents=True, exist_ok=True)

# after:
for _d in (CHROMA_DIR, SKILLS_DIR):
    if _d.exists():
        _shutil.rmtree(_d)
    _d.mkdir(parents=True, exist_ok=True)
# src/mcp_tools is project source; do NOT rmtree.
```

Same change at `run_longitudinal_shift.py:250`.

**B. Assert protection in `take_clean_snapshot`.**

```python
# tests/evaluation/snapshots.py — at the top of take_clean_snapshot:
_PROJECT_SOURCE_DIRS = {
    ROOT / "src",
    ROOT / "scripts",
    ROOT / "tests",
}

def take_clean_snapshot(chroma_dir, mcp_tools_dir, skills_dir, ...):
    for d in [chroma_dir, mcp_tools_dir, skills_dir]:
        resolved = Path(d).resolve()
        for source_root in _PROJECT_SOURCE_DIRS:
            if resolved.is_relative_to(source_root.resolve()):
                raise ValueError(
                    f"Refusing to snapshot {resolved} — it is inside project source "
                    f"{source_root}. Pass a data/eval/ path."
                )
    ...
```

Loud-fails any future attempt to clean a project-source directory.

**C. Regression test.**

```python
def test_longitudinal_runner_does_not_rmtree_src_mcp_tools(tmp_path):
    """Running longitudinal setup must not modify src/mcp_tools."""
    source_dir = ROOT / "src" / "mcp_tools"
    initial_files = set(source_dir.iterdir()) if source_dir.exists() else set()
    # Invoke the runner's setup phase (just prepare, no task loop)
    _run_longitudinal_prepare()
    assert source_dir.exists()
    assert set(source_dir.iterdir()) == initial_files  # unchanged
```

### Recommendation

**Land immediately — blocking any further longitudinal run.** Two-line fix per runner + 1 regression test + optional (B) assertion. ~10 LOC + ~15 min. Every longitudinal run pre-fix is destructive; warn users.

### Legacy data

If `src/mcp_tools` was deleted by a prior run, restore via `git checkout src/mcp_tools/`. No DB-level migration needed.

---

## Issue #87 — Longitudinal runners derive LangFuse `session_id` only from `--seed`; re-runs with same seed pool into one session (MEDIUM)

### Summary

`scripts/run_longitudinal.py:207`:
```python
_lf_session = f"longitudinal-{args.seed}"
```

`run_longitudinal_shift.py:312`:
```python
_lf_session = f"longitudinal-shift-{args.seed}"
```

The session_id is deterministic from `--seed`. Two invocations with the same seed (e.g., re-running after a bug fix to validate behavior) tag traces with the same session_id → LangFuse dashboards pool both runs into one session. Separation is impossible without ad-hoc per-trace timestamp filtering.

### Where

- **Deterministic-from-seed session ID:** `run_longitudinal.py:207`, `run_longitudinal_shift.py:312`.

### Reproduction

```bash
# Day 1:
$ uv run scripts/run_longitudinal.py --seed 42
# LangFuse session_id: "longitudinal-42"

# Day 2 (fix bug, re-run):
$ uv run scripts/run_longitudinal.py --seed 42
# LangFuse session_id: "longitudinal-42"  ← same session
# Dashboard queries filtered by this session return BOTH runs' traces.
```

### Threat model / impact

- **LangFuse dashboards can't separate runs.** Per-session aggregates mix pre-fix and post-fix data.
- **Reviewer-reproducibility check fails.** Reviewer re-running with the same seed gets traces pooled with author's prior traces.
- **Direction of bias: cross-run contamination**. Aligns with ext7 #49 (DB isolation) and ext7 #55 (analyzer run_id scoping) — runners write to shared namespaces without per-invocation uniqueness.

### Remediation

**A. Add timestamp to session_id.**

```python
from datetime import datetime
_lf_session = f"longitudinal-{args.seed}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
```

Automatic uniqueness per invocation.

**B. Accept `--session-suffix` CLI arg.**

User explicitly passes a campaign suffix; run sheet sets `$CAMPAIGN_ID` and threads through.

**C. Regression test.**

```python
def test_longitudinal_session_id_unique_per_invocation():
    """Two run_longitudinal invocations with same seed get different session_ids."""
    ...
```

### Recommendation

**MEDIUM — land before any longitudinal re-run.** ~3 LOC + 1 test. ~15 min.

---

## Issue #88 — `scripts/test_pipeline.py` doesn't use `build_initial_state`; integration smoke tests bypass contract injection (MEDIUM)

### Summary

Ext7 #53 introduced `src/orchestrator/task_state_builder.py::build_initial_state` as the canonical TaskState constructor; every pipeline entry point was supposed to use it to get consistent `output_contract` + `allow_network` threading.

`scripts/test_pipeline.py:107` still constructs TaskState directly:

```python
initial_state = TaskState(task_description=task_prompt)
```

No `output_contract=`, no `allow_network=`. Same shape as ext7 #53's bug in longitudinal runners, but in the integration test. Consequence: `test_pipeline.py` (row 2 of Phase 3 smoke gates in run_sheet.md) exercises a simpler pipeline path than Phase 4 production runs. The "did the fix work end-to-end?" smoke gate misses contract-driven decomposition behavior (writer-subtask synthesis, file-output routing).

### Where

- **Bypass site:** `scripts/test_pipeline.py:107` — `initial_state = TaskState(task_description=task_prompt)`.
- **Canonical builder:** `src/orchestrator/task_state_builder.py::build_initial_state` (per ext7 #53).
- **Run sheet context:** `docs/run_sheet.md` — test_pipeline.py is a Phase 3 gate.

### Threat model / impact

- **Phase 3 gate signal is weaker than it appears.** A pipeline change that breaks contract-driven decomposition passes `test_pipeline.py` but fails Phase 4. The "gate" doesn't catch it.
- **Same class as ext7 #53** — pipeline-entry-point asymmetry. Guard pattern already noted.
- **Lower severity than longitudinal runners' version** because test_pipeline.py is smoke not data-gen.

### Remediation

```python
# scripts/test_pipeline.py:107 — before:
initial_state = TaskState(task_description=task_prompt)
# after:
from src.orchestrator.task_state_builder import build_initial_state
initial_state = build_initial_state(task)
```

### Recommendation

**Land with any Phase 3 gate-integrity pass.** ~3 LOC + 1 import. ~5 min.

---

## Issue #89 — `decomposition.py` truncates `required_capabilities` to `MAX_CAPS_HARD=8` but keeps subtasks that reference dropped capabilities (P0)

### Summary

`src/orchestrator/decomposition.py:671-687`:

```python
if len(required_caps) > MAX_CAPS_HARD:
    logger.warning(
        "Decomposition produced %d caps — truncating to %d (MAX_CAPS_HARD).",
        len(required_caps), MAX_CAPS_HARD,
    )
    emit_langfuse_event(... dropped=required_caps[MAX_CAPS_HARD:] ...)
    required_caps = required_caps[:MAX_CAPS_HARD]
```

The truncation drops entries from `required_caps` but **doesn't modify `subtasks`**. Any subtask whose `required_capabilities` field references a truncated entry still exists in the chain. At classifier / gap-detection / execution time, that reference points at a capability the pipeline never generated — resulting in resolution failure → `CompositionalExecutor._resolve_step` returns `(None, None)` → chain fails with `"Step N has no resolved capability"`.

The circuit breaker's intent (prevent runaway LLM cap-list inflation) is legitimate, but the implementation is partial — truncating the cap list without pruning referenced subtasks leaves the chain structurally inconsistent.

### Where

- **Truncation without subtask pruning:** `src/orchestrator/decomposition.py:671-687`.
- **Consumer that fails on stale references:** `src/orchestrator/composition.py:330-350` (`_resolve_step`).
- **Failure surface:** chain fails with "Step N has no resolved capability" for each subtask referencing a dropped cap.

### Reproduction

```python
# Decomposer LLM emits 12 required_capabilities + 12 subtasks, one subtask per cap.
required_caps = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"]
subtasks = [
    SubTask(required_capabilities=["A"]),
    ...,
    SubTask(required_capabilities=["I"]),  # ← subtask 8 references "I" (index 8, 0-indexed)
    SubTask(required_capabilities=["J"]),  # subtask 9
    SubTask(required_capabilities=["K"]),  # subtask 10
    SubTask(required_capabilities=["L"]),  # subtask 11
]
# After MAX_CAPS_HARD=8 truncation:
required_caps = ["A", "B", "C", "D", "E", "F", "G", "H"]
# But subtasks 8, 9, 10, 11 still reference "I", "J", "K", "L".
# At execution time, subtask 8's _resolve_step returns (None, None) → chain fails.
```

### Threat model / impact

- **Any task whose decomposer produces >8 capabilities fails silently at execution.** The LangFuse `truncation_applied` event fires, but the downstream failure is reported as "chain_step_failed" — attributed to the execution layer, not to the truncation.
- **Direction of bias: depends on task complexity.** Simple tasks (≤8 caps) are unaffected; complex tasks (10+ caps, e.g., T11 multi-source integration) may hit it.
- **The warning at line 674 doesn't distinguish "dropped caps have no dependent subtasks" from "dropped caps leave dangling subtask references."** User has no signal that the chain is now broken.

### Remediation

**A. Also prune subtasks that reference dropped capabilities.**

```python
if len(required_caps) > MAX_CAPS_HARD:
    dropped_caps = set(required_caps[MAX_CAPS_HARD:])
    required_caps = required_caps[:MAX_CAPS_HARD]

    # NEW: drop subtasks that reference ANY dropped cap.
    kept_subtasks = []
    dropped_subtask_count = 0
    for st in subtasks:
        if any(cap in dropped_caps for cap in st.required_capabilities):
            dropped_subtask_count += 1
        else:
            kept_subtasks.append(st)
    subtasks = kept_subtasks

    logger.warning(
        "Truncation dropped %d subtasks referencing truncated caps", dropped_subtask_count,
    )
    emit_langfuse_event(... subtask_drop_count=dropped_subtask_count ...)
```

Renumber surviving subtasks' `depends_on` indices accordingly (or re-validate via `_validate_depends_on_indices` which would catch indexing issues).

**B. Alternative: fail-closed on truncation.**

Rather than truncating, treat "cap count exceeds MAX_CAPS_HARD" as a decomposition failure and hit the retry loop. If 3 retries all produce >8 caps, flip `decomposition_succeeded=False` and exit 2. Strict-gate consistent with ext6 findings.

**C. Regression test.**

```python
def test_decomposition_truncation_drops_stale_subtasks():
    """12-cap decomposition → truncated to 8 → subtasks 8..11 (referring to dropped caps) also dropped."""
    ...

def test_decomposition_truncation_preserves_dag_validity():
    """After truncation, surviving subtasks' depends_on still reference valid indices."""
    ...
```

### Recommendation

**Land before Phase-5 eval.** ~30 LOC + 2 tests. ~45 min. Option (B) is the proposal-stricter fix; Option (A) is the narrower correctness fix.

---

## Issue #90 — Decomposition retry loop doesn't trigger on DAG validation failures; invalid DAGs bypass the 3x retry budget (P1)

### Summary

Ext8 #56's analysis and `_validate_depends_on_indices` (added at `decomposition.py:157`) correctly detect malformed DAGs (cycles, out-of-range indices, self-loops). But the validator runs **AFTER** the decomposer retry loop exits:

```python
# decomposition.py — flow:
for attempt in range(1, MAX_DECOMPOSITION_ATTEMPTS + 1):
    try:
        # invoke decomposer LLM
        ...
        break  # success — exit retry
    except TimeoutError | Exception:
        continue  # retry

# AFTER retry loop:
dag_violations = _validate_depends_on_indices(subtasks)
if dag_violations:
    decomposition_succeeded = False
    ...
```

So an LLM that produces a malformed DAG on the first attempt flips `decomposition_succeeded=False` but doesn't get three more chances. The "3x retry + sticky flag" strict-gate pattern only applies to exceptions, not to structural invalidity.

User's claim: "retries only on timeouts/exceptions — malformed DAGs simply move to next node and crash later."

**CONFIRMED structurally**, but with a correction: with `decomposition_succeeded=False`, CLI exits 2 on retry exhaustion (per ext6 findings) rather than crashing at execution. The failure is loud — just not via retry.

### Where

- **Retry loop (exception-only):** `decomposition.py:552-578` + `:619-638`.
- **DAG validator post-loop (no retry):** `decomposition.py:759-774`.
- **Strict-gate reference pattern:** verification + classification retry on validation failures, not just exceptions.

### Threat model / impact

- **LLM that emits a malformed DAG gets the sticky-flag/exit-2 outcome immediately, not after 3 attempts.** Missed opportunity for the LLM to self-correct within the retry budget.
- **Direction of bias: anti-JIT.** Any transient malformed DAG (e.g., cycle from LLM forgetting the DAG constraint once) causes a pipeline failure without retry, even though a second attempt would likely produce a valid DAG.
- **Pillar 1 M1 measurement** may appear lower than it would with full retry coverage. Acceptable as long as Chapter 4.5 discloses the retry scope.

### Remediation

**A. Route DAG validation failures through the retry loop.**

```python
for attempt in range(1, MAX_DECOMPOSITION_ATTEMPTS + 1):
    try:
        # invoke decomposer LLM
        ...
        # NEW: validate DAG inside the retry loop
        dag_violations = _validate_depends_on_indices(subtasks)
        coherence_violations = _validate_chain_coherence(subtasks)
        if dag_violations or coherence_violations:
            logger.warning(
                "Decomposition attempt %d/%d: DAG/coherence violations %s — retrying",
                attempt, MAX_DECOMPOSITION_ATTEMPTS, dag_violations + coherence_violations,
            )
            continue
        break  # success — valid DAG
    except ...:
        continue
```

The post-loop sticky-flag setting still fires if the 3rd attempt also fails validation.

**B. Regression test.**

```python
def test_decomposition_retries_on_malformed_dag():
    """LLM produces cyclic DAG on attempt 1, valid DAG on attempt 2 → decomposition succeeds."""
    ...
```

### Recommendation

**Bundle with strict-gate coverage audit (ext6 #14/#15/#16 family).** ~20 LOC + 1 test. ~30 min.

---

## Issue #91 — `execute_mcp_tool_in_chain` loads ALL `step_*_output.json` files regardless of `depends_on`; parallel-branch state leakage (MEDIUM)

### Summary

`tests/evaluation/execution.py:761`:

```python
for _sf in sorted(glob.glob("/workspace/step_*_output.json")):
    _step_name = os.path.basename(_sf).replace("_output.json", "")
    ...
```

The sandbox code reads EVERY `step_*_output.json` file in the workspace and merges them into the `upstream_data` dict available to the tool. No filtering by the subtask's declared `depends_on`.

In a chain with parallel branches (e.g., T11 decomposition producing two parallel writer steps that both depend on the same upstream but not on each other), each writer sees the other writer's output if emitted earlier. Two parallel MCP_TOOL steps writing to `step_5_output.json` and `step_6_output.json` each load each other's output despite not being dependencies.

The actual impact depends on whether the tool code uses `upstream_data` wisely — if the tool merges all upstreams into one dict and the sibling writer used keys that clash, the wrong data is used.

### Where

- **Unfiltered glob load:** `tests/evaluation/execution.py:761-762` (first load into `upstream_inputs`), `:797` (second load into `_upstream_merged`).

### Threat model / impact

- **Parallel-branch state corruption.** T11-shape chains (multi-file writers, parallel after common upstream) get cross-contamination.
- **Direction of bias: unpredictable**. Depends on which branch's output comes first alphabetically (since `sorted(glob)` orders by filename).
- **Compounds with ext8 #64 + #65** (other compositional-execution correctness issues).

### Remediation

**A. Pass the current subtask's `depends_on` indices to the sandbox code + filter.**

```python
# Before building sandbox_code, filter the set of files to load:
deps_json = json.dumps(sorted(depends_on))  # depends_on is the calling subtask's deps
sandbox_code = f"""
import glob, json, os
deps = set(json.loads('''{deps_json}'''))
for _sf in sorted(glob.glob("/workspace/step_*_output.json")):
    _step_name = os.path.basename(_sf).replace("_output.json", "")
    _step_idx = int(_step_name.replace("step_", ""))
    if _step_idx not in deps:
        continue
    ...
"""
```

**B. Regression test.**

```python
def test_execute_mcp_tool_in_chain_filters_by_depends_on():
    """Subtask with depends_on=[0] should NOT see step_1_output.json from a sibling."""
    ...
```

### Recommendation

**Land before Phase-5 eval on multi-step tasks.** ~15 LOC + 1 test. ~30 min.

---

## Issue #92 — `CompositionalExecutor._resolve_step` returns the first resolving capability; multi-capability subtasks silently drop all but the first (MEDIUM)

### Summary

`src/orchestrator/composition.py:330-350`:

```python
def _resolve_step(self, subtask):
    for cap_name in subtask.required_capabilities:
        ref = self.resolution.get(cap_name)
        if ref is None:
            continue
        # ... return first resolving cap
        return ref, match
    return None, None
```

A `SubTask` declaring `required_capabilities=["capA", "capB"]` resolves to capA only. capB is silently unused. If the subtask logically needs BOTH (e.g., "use capA to fetch, capB to transform, return the chained result"), capB's contribution is lost.

Whether this is a bug depends on whether the decomposer ever emits multi-cap subtasks. The design intent (from observation) is one-cap-per-subtask. If the LLM produces multi-cap subtasks anyway, capB is silently ignored.

### Where

- **First-only resolution:** `src/orchestrator/composition.py:330-350`.

### Threat model / impact

- **If decomposer emits `required_capabilities=["capA","capB"]`, capB is silently ignored.**
- **Direction of bias: anti-completeness.** Chain executes less than what was decomposed.
- **Latent** if the decomposer's prompt enforces one-cap-per-subtask (grep for prompt text confirms design intent); loud if the LLM ever deviates.

### Remediation

**A. Either: enforce one-cap-per-subtask at validation, OR handle multi-cap execution.**

Option A1: validate at decomposition:
```python
# decomposition.py — post-validation:
for i, st in enumerate(subtasks):
    if len(st.required_capabilities) > 1:
        violations.append(f"Subtask {i} declares {len(st.required_capabilities)} caps; expected 1")
```

Option A2: execute all in sequence, return last:
```python
# composition.py::_resolve_step — return list of refs, not first match
# run loop — iterate refs, feed prior output as upstream to next
```

Option A1 is simpler + aligns with design intent. A2 is more expressive but adds complexity.

**B. Regression test.**

```python
def test_resolve_step_rejects_multi_capability_subtask():
    """Multi-cap subtask should raise a validation error, not silently drop cap 2+."""
    ...
```

### Recommendation

**MEDIUM — pick Option A1 for simplicity.** ~10 LOC + 1 test. ~20 min.

---

## Issue #93 — `CompositionalExecutor.run()` only checks the TERMINAL step for `_output_file_content`; intermediate SKILL/AGENT file outputs lost (MEDIUM)

### Summary

`src/orchestrator/composition.py:961-964`:

```python
if step_results:
    last_output = step_results[-1]
    file_name = last_output.get("_output_file_name")
    file_content = last_output.get("_output_file_content")
```

Only the LAST step's `_output_file_content` is extracted. If an intermediate SKILL or AGENT step emits `_output_file_content` (the workaround for SKILL/AGENT being unable to write the Docker workspace), that file is lost.

`_collect_output_files` at line 957 also runs, scanning the workspace for files written by any MCP_TOOL step — so MCP_TOOL intermediate files ARE captured via that path. The gap is specifically: SKILL/AGENT steps that emit `_output_file_content` as an in-band payload are only checked on the terminal step.

### Where

- **Terminal-only check:** `src/orchestrator/composition.py:961-964`.
- **MCP_TOOL path (works):** `_collect_output_files` at line 957.

### Threat model / impact

- **Intermediate file-producing SKILL/AGENT content is discarded.** A 2-step chain (SKILL produces a chart image → MCP_TOOL aggregates metrics) loses the chart.
- **Direction of bias: anti-intermediate-file-output-tasks.** Affects only specific chain shapes.
- **Compounds with ext6 #33** (T07/T11 file validators fail-open on missing files) — both issues relate to file-output handling in mixed chains.

### Remediation

**A. Check every step for `_output_file_content`.**

```python
# composition.py:961-964 — before:
if step_results:
    last_output = step_results[-1]
    file_name = last_output.get("_output_file_name")
    file_content = last_output.get("_output_file_content")
    ... # process

# after:
for step_output in step_results:
    if not isinstance(step_output, dict):
        continue
    file_name = step_output.get("_output_file_name")
    file_content = step_output.get("_output_file_content")
    if file_name and file_content and file_name not in collected_files:
        ... # same processing logic
```

### Recommendation

**MEDIUM — land with Phase 5 if file-output tasks with intermediate SKILL/AGENT steps are in the testbed.** Check the testbed; if no task requires intermediate file output, this is Chapter 7. ~20 LOC. ~30 min.

---

## Issue #94 — `analyze_context_utilization.py` reports per-agent peak tokens; chain-cumulative token cost is not measured (MEDIUM)

### Summary

`scripts/analyze_context_utilization.py:216-217`:

```python
peak_input = max(s["input_tokens"] for s in steps)
peak_output = max(s["output_tokens"] for s in steps)
```

The "peak" is the single step with the highest token usage within a single agent trace. The metric is used as Row 13's T3 evidence ("naive-verbose context utilization").

Pillar 3's "Context Economy" tension is about total prompt-token cost across the whole MAS — a chain using three agents at 20K tokens each has 60K cumulative tokens (the interesting number for "does the optimizer reduce prompt bloat?"). Peak-per-agent reports 20K — under-represents cumulative cost.

### Where

- **Per-agent peak only:** `scripts/analyze_context_utilization.py:216-217`, reported at `:229` as `context_utilization_pct`.
- **No chain-cumulative metric** in the current analyzer.

### Threat model / impact

- **Pillar 3 "Context Economy" claim hard to ground.** The metric Chapter 6 cites as T3 evidence measures per-agent peak, not cumulative chain cost.
- **Direction of bias: understates cost.** A chain-heavy task that uses 5 × 20K = 100K tokens gets reported as "20K peak" — 5× understatement.
- **Compounds with ext8 #66** (compute_utility ignores `definition_token_cost`): both are Context Economy measurement gaps.

### Remediation

**A. Add chain-cumulative metric alongside per-agent peak.**

```python
# analyze_context_utilization.py — extend the metric dict:
{
    "peak_input_tokens": peak_input,
    "total_chain_input_tokens": sum(s["input_tokens"] for s in steps),  # NEW
    ...
}
```

**B. Report both in Row 13 output.**

Chapter 6 table can show per-agent peak (for "does any single agent exceed context window?") AND chain cumulative (for "total MAS cost per task").

### Recommendation

**MEDIUM — methodology disclosure.** ~10 LOC. ~15 min.

---

## Issue #95 — `_extract_result` falls back to single-artifact path when only one artifact exists; lead-agent serving multiple subtasks silently skips subtasks 2..N (MEDIUM)

### Summary

`tests/evaluation/harness.py:1093`:

```python
if len(subtasks) > 1 and (len(artifacts) + len(matches) > 1):
    return _extract_result_compositional(...)
# ELSE falls through to single-artifact execution
```

The condition requires at least one artifact per subtask. If the lead-with-subagents pattern produces ONE AGENT artifact that covers N subtasks (which is the design intent — per CLAUDE.md's "Lead-with-subagents (Phase 3.5b)"), the condition `len(artifacts) + len(matches) > 1` is false. The path falls through to single-artifact execution, which only runs the one artifact — skipping subtasks 2..N entirely.

### Where

- **Conditional compositional-vs-single:** `tests/evaluation/harness.py:1093`.
- **Single-artifact path:** handled downstream at `_extract_result`'s non-compositional branch.

### Reproduction

```python
# Lead-with-subagents scenario:
# Decomposer produces 3 subtasks.
# AGENT generator emits 1 AGENT artifact with agent_config.subagents (covers all 3).
# len(subtasks) = 3, len(artifacts) = 1, len(matches) = 0.
# Condition: 3 > 1 AND (1 + 0 > 1) → True AND False → False.
# Fall through to single-artifact path; subtasks 2 and 3 never execute.
```

### Threat model / impact

- **Lead-with-subagents chains execute only 1/N of their subtasks.** The CompositionalExecutor has lead-cache logic to handle this case (ext6 #29); the precondition for using CompositionalExecutor is the bug.
- **Direction of bias: under-executes lead-with-subagents tasks.** M1 drops for these cases.

### Remediation

**A. Loosen the condition to require >= 1 artifact OR match, not >1 combined.**

```python
# harness.py:1093 — before:
if len(subtasks) > 1 and (len(artifacts) + len(matches) > 1):

# after — use compositional whenever >1 subtasks, regardless of artifact count:
if len(subtasks) > 1 and (len(artifacts) + len(matches) >= 1):
```

CompositionalExecutor's `_resolve_step` correctly handles the case where a single artifact/match resolves multiple subtasks.

**B. Regression test.**

```python
def test_extract_result_uses_compositional_for_lead_with_subagents():
    """3 subtasks + 1 lead-agent artifact → compositional execution (not single-artifact fallback)."""
    ...
```

### Recommendation

**MEDIUM — bundle with the compositional-execution integrity cluster.** ~3 LOC + 1 test. ~15 min.

---

## Issue #96 — Gini coefficient excludes zero-invocation active capabilities; "1 cap used 100 times" reports perfect equality (MEDIUM)

### Summary

`scripts/analyze_results.py::print_gini_summary` computes Gini from the counts list produced by the invocation_events GROUP BY (line 673-681). Only capabilities WITH at least one invocation appear in the counts list. Capabilities in the active registry with ZERO invocations are excluded.

Example: a run where cap_X was invoked 100 times and 19 other active caps were invoked zero times. Current Gini computation: `[100]` → Gini = 0.0 (perfect equality of a 1-element set). Expected Gini over the full active registry: `[100, 0, 0, ..., 0]` → Gini ≈ 0.95 (extreme concentration).

The proposal's Gini claim is "concentration of invocations across the registry." "The registry" should mean "all active capabilities" — the inequality reflects how concentrated usage is among things the system COULD use.

Pairs with ext8 #78 (Gini includes PRUNED capabilities' historical rows — over-counts). Together: current Gini includes ghost high-count rows AND excludes zero-count active rows. Both distort in opposite directions. The proposal-correct population is "currently-active capabilities, each counted including zeros."

### Where

- **Counts list source (invocation-events-only, no zero rows):** `scripts/analyze_results.py:673-681` + `:701` (`condition_ginis[cond].append(_gini_coefficient(counts))`).
- **Gini formula:** `scripts/analyze_results.py:611-635`.
- **Related:** ext8 #78 (historical pruned rows included).

### Reproduction

```python
# Single-capability heavy use:
counts_from_query = [100]   # only cap_X invoked (100 times)
_gini_coefficient(counts_from_query)  # → 0.0 (misleading — reports perfect equality)

# With zero-padding for active caps:
active_cap_count = 20
counts_zero_padded = [100] + [0] * 19
_gini_coefficient(counts_zero_padded)  # → ~0.95 (correctly reports extreme concentration)
```

### Threat model / impact

- **Pillar 3 Gini claim systematically understates concentration** when tasks use few of the many available capabilities.
- **Direction of bias: pro-healthy-registry.** Registries that SHOULD be flagged as "extreme skew" look "moderate" because only the used caps are measured.
- **Compounds with ext8 #78:** one understates (excludes zeros), the other overstates (includes ghost rows). Neither matches the proposal's "concentration across the registry."

### Remediation

**A. Zero-pad the counts list with all active registry capabilities.**

```python
# analyze_results.py — inside print_gini_summary, before each Gini compute:
from src.registry.store import SkillRegistry
registry = SkillRegistry(persist_directory=args.chroma_dir)
active_ids = {cap.name for cap in registry.list_all(is_active=True)}

# For each (condition, run), build the per-cap counts as a dict first:
counts_by_cap = {row["capability_id"]: row["count"] for row in per_run_rows if matches (condition, run)}
# Then pad with zero for every active cap not in counts_by_cap:
counts = [counts_by_cap.get(name, 0) for name in active_ids]
_gini_coefficient(counts)
```

**B. Optional: also exclude pruned caps per ext8 #78 fix.**

Same join; use `is_active=True` filter on the registry read, and exclude inactive caps from the counts list.

**C. Regression test.**

```python
def test_gini_zero_pads_for_unused_active_capabilities():
    """Registry with 20 active caps, only 1 invoked → Gini near 1.0, not 0.0."""
    ...
```

### Recommendation

**MEDIUM — bundle with ext8 #78.** Both need registry joins. ~25 LOC + 1 test. ~30 min combined with #78.

---

## Issue #97 — `_run_static_implementation` whitelist doesn't cover all tasks with `has_static_coverage=True`; T19 crashes with ValueError on every static-condition run (P0)

### Summary

`tests/evaluation/harness.py:1646-1658`:

```python
def _run_static_implementation(task_id, fixture_paths):
    if task_id == "T01": return _static_t01(...)
    if task_id == "T02": return _static_t02(...)
    if task_id == "T03": return _static_t03(...)
    if task_id == "T05": return _static_t05(...)
    if task_id == "T09": return _static_t09(...)
    raise ValueError(f"No static implementation for {task_id}")
```

Whitelist: **T01, T02, T03, T05, T09**. Any other task reaching this function raises ValueError.

The function is only called after `_run_static`'s guard (`harness.py:535: if not task.has_static_coverage: return {...}`). So only tasks with `has_static_coverage=True` reach `_run_static_implementation`.

Cross-checking `tests/evaluation/tasks.py` for `has_static_coverage=True`:
- **T01** ✓ (implementation present)
- **T02** ✓ (implementation present)
- **T05** ✓ (implementation present)
- **T09** ✓ (implementation present)
- **T19** ✓ — **NO IMPLEMENTATION BRANCH** → ValueError

Additionally: `_run_static_implementation` has a branch for T03 (line 1652), but T03 has `has_static_coverage=False` in tasks.py (line 233) → T03 branch is dead code.

Two coupling inconsistencies:
1. **Missing T19 branch**: T19 declares static coverage but has no implementation → production crash.
2. **Dead T03 branch**: T03's `has_static_coverage=False` means the T03 branch never executes, masquerading as "coverage exists" to a code reader.

### Where

- **Whitelist:** `tests/evaluation/harness.py:1648-1658`.
- **Coverage declarations (source of truth):** `tests/evaluation/tasks.py` — T19 at line 1103-ish with `has_static_coverage=True`.
- **Short-circuit guard for no-coverage tasks:** `tests/evaluation/harness.py:535-540` — protects tasks with `has_static_coverage=False` from ever hitting `_run_static_implementation`.

### Reproduction

```bash
$ uv run scripts/run_pilot.py --tasks T19 --condition static --n-runs 1
# _run_static(task_T19, registry) executes
# task_T19.has_static_coverage = True → passes guard at line 535
# → proceeds to _run_static_implementation("T19", fixture_paths)
# → no branch matches "T19"
# → ValueError: "No static implementation for T19"
# → caught at _run_static line 567 "except Exception as exc:" → returns
#   {"gate_passed": False, "output_in_range": False, "failure_reason": "generation_failed"}
# → T19 static row records as failure with misleading reason "generation_failed"
```

The ValueError is caught, but it's silently transformed into `failure_reason="generation_failed"` — which tells the analyzer "the static baseline failed on T19 because of a generation failure." This is wrong: the failure is a missing implementation, not a generation failure. Chapter 6's static-condition M1 is artificially depressed on T19 with mis-attribution.

### Threat model / impact

- **Static condition M1 depressed on T19.** The task contributes 5 failures per run (one per seed) in the static column, mis-labeled as `generation_failed` instead of `missing_static_implementation`.
- **Failure taxonomy contamination (ext9 #85 related).** The row enters Row 17's failure breakdown under the wrong category. Chapter 6's "static baseline failure modes" table includes phantom `generation_failed` entries.
- **Direction of bias: anti-static-baseline.** Static looks worse than it is on T19; the JIT-vs-static gap is inflated for that task.
- **Ghost T03 branch** wastes code-reader attention but doesn't affect correctness (unreachable).

### Remediation

**A. Add T19 implementation OR flip `has_static_coverage=False` for T19.**

Option A1 — add implementation:
```python
# harness.py — new static impl for T19:
def _static_t19(fixture_path: Path) -> dict:
    """T19: exploratory data analysis — static implementation."""
    # ... reference hand-written code ...
    return {"analysis_summary": ..., ...}

# harness.py:1657 — add branch:
if task_id == "T19":
    return _static_t19(fixture_paths[0])
```

Option A2 — flip the flag if no reference implementation exists:
```python
# tests/evaluation/tasks.py:
T19 = TaskDefinition(
    ...,
    has_static_coverage=False,   # was True
    static_capability_name=None,   # was "<something>"
)
```

A2 is faster (no new code); A1 preserves T19 as a static-comparable task (better for Chapter 6 completeness).

**B. Remove the dead T03 branch.**

```python
# harness.py:1652 — delete:
# if task_id == "T03": return _static_t03(fixture_paths[0])
```

Confirms the whitelist matches reality.

**C. Add a startup consistency check.**

```python
# harness.py — module-level assertion:
from tests.evaluation.tasks import ALL_TASKS
_STATIC_IMPL_TASKS = {"T01", "T02", "T05", "T09", "T19"}  # update as implementations land
_COVERAGE_TASKS = {t.task_id for t in ALL_TASKS if t.has_static_coverage}
assert _STATIC_IMPL_TASKS == _COVERAGE_TASKS, (
    f"has_static_coverage/implementation drift: "
    f"tasks_with_coverage={_COVERAGE_TASKS}, impl_branches={_STATIC_IMPL_TASKS}"
)
```

Module-load-time fail-loud when drift occurs.

**D. Regression test.**

```python
def test_every_has_static_coverage_task_has_implementation():
    from tests.evaluation.tasks import ALL_TASKS
    for task in ALL_TASKS:
        if task.has_static_coverage:
            # Attempt to execute (with mock fixtures) and verify no ValueError:
            try:
                _run_static_implementation(task.task_id, [Path("/dev/null")])
            except ValueError as e:
                if "No static implementation" in str(e):
                    pytest.fail(f"Task {task.task_id} has coverage but no implementation")
```

### Recommendation

**Land before any Phase-5 static-condition run.** Options: (A2) 2-line flag flip (30 sec) OR (A1) ~40 LOC for T19 implementation (~30 min). (C) assertion + (D) test ~15 min. Total: 15-45 min depending on path.

### Legacy data

T19 static rows in any pre-fix DB are mis-attributed as `generation_failed`. Re-run the static condition for T19 after fix; or document the mis-attribution in Chapter 4.5.

---

## Claims submitted but NOT added (not-reproducible / already-covered / documented-design)

### User's #84: `store.py::search` filters inactive caps after retrieval → **NOT CONFIRMED**

**Actual behavior (`src/registry/store.py:265-266`):** `is_active` is a WHERE clause applied at ChromaDB query time, not post-filtering. Search pre-filters by `is_active={"$eq": True}` before ChromaDB computes similarity scores. No post-retrieval inactive rows are returned to filter away.

### User's #85: `detect_gaps` ignores deactivation reason → **NOT CONFIRMED**

`detect_gaps` operates on the active-only pool (since `search` pre-filters). It cannot "report a deactivated capability as missing" because it never sees deactivated capabilities. The scenario user describes (optimizer pruning a capability, then a later task sees it as "missing") is actually the **intended** Pillar 3 behavior: pruned means "this capability is no longer part of the registry," which should register as a gap in the next task that needs something similar. The "reason for deactivation" is irrelevant to gap detection.

### User's #86: Generator and Executor both write to `SKILLS_OUTPUT_DIR` → **NOT CONFIRMED (only generator writes)**

Generator writes (`src/orchestrator/generators/skill.py:318` — `target_dir = (output_dir or SKILLS_OUTPUT_DIR) / forced_name`). Executor reads from the directory via the DeepAgents skill-loader; it does not write. The `set_skills_output_dir` helper at line 38 allows per-process override; combined with ext7 #51's eval_registry_lock, cross-process collisions are prevented.

Residual concern: within a single process, the same `SKILLS_OUTPUT_DIR` may be written to by sequential generator calls that produce same-named skills. The `forced_name` is derived from the capability name; name collisions would overwrite. That's a minor concern about generator-output reproducibility, not a 18-hour-campaign crash. Not distinct enough to add.

### User's #87: `run_longitudinal_shift.py` Phase A/B asymmetric task counts → **DESIGN CHOICE**

The runner's CLI accepts `--phase-a-tasks` and `--phase-b-tasks` as arbitrary-length lists. Asymmetric counts (e.g., 8 Phase A + 12 Phase B) are explicitly supported by design — the distribution-shift experiment is meant to test adaptation to a NEW task distribution, which can be of different size/complexity than Phase A.

The KL divergence measurement at the shift boundary is sensitive to the baseline's sample size, but that's a known property of KL, not a bug. Chapter 4.5 should disclose if asymmetric counts were used in the eval; the code doesn't need to be "fixed."

### User's #92: `backfill_token_costs.py` blind subtract-3 → **DUPLICATE of ext8 #58 Bug B**

Already logged with full remediation. See ext8 #58.

### User's #93: `run_pilot.py --all-conditions` mismatch → **NOT A CURRENT BUG**

User's submission acknowledges: "I found this was fixed, but the gap remains high-risk." Confirmed: `run_pilot.py:71` has `--all-conditions` flag. Not a finding.

### User's #94: `attempt_latency_ms` includes harness overhead and DB write → **PARTIALLY CONFIRMED (covered by ext8 #70)**

DB write is NOT included: `elapsed_ms` at `harness.py:492` is captured BEFORE `EvaluationRecord` construction + `record_run` at line 493-524. But harness overhead during `_run_jit` (LangFuse callbacks, `metrics_store.record_invocation` calls) IS included. This is the same class of concern as ext8 #70 (harness-affordance ecological validity). Not a distinct finding; bundle messaging with #70 Chapter 4.5 disclosure.

### User's #95: Token-accounting LLM call discarded → **DOCUMENTED DESIGN**

`tests/evaluation/harness.py:715-729` explicitly documents: "run the extractor inside the LangFuse-tagged invocation context so its tokens count toward the per-task budget; discard the result." This is **intentional** for token-cost honesty — the eval would under-count M4 if it skipped the call the CLI makes.

The failure handling at line 724-729 catches exceptions and logs debug. User's concern: "Failures in this call don't fail the task, creating hidden cost-discrepancies."

This is a minor concern. If the accounting call fails, M4 is under-counted for that task by the extractor's missed cost. Post-fix, loud-failing would be safer — but the current gentle-degradation is defensible (the accounting call isn't essential to the task). Bundle with ext8 #70's Chapter 4.5 disclosure rather than a distinct finding.

### User's #96: `output_shape_mismatch` doesn't flip `output_in_range` → **FIXED BY ext8 #64 + #68**

**Already fixed.** Line 594-595 (compositional path) and line 1001-1002 (`_extract_result_compositional`) both flip `output_in_range = False` on shape mismatch per ext8 #64. Line 595 (`_run_static`) flips per ext8 #68. All three paths addressed.

### User's #98: M2 sample/rate calculation → **COVERED by #84**

User's #97 (partial excluded from contamination) and #98 (rate formula contradicts proposal) are the same concern framed two ways. The remediation for #84 above addresses both — including partial in the numerator aligns with the proposal's definition.

---

### Second-batch submissions, claims that did NOT become ext9 findings

### User's #105: `execute_mcp_output` default function name "tool" → **NOT REPRODUCIBLE**

Claim: "`execute_mcp_output` in `test_pipeline.py` defaults the function name to `tool`."

**Actual:** grep for `execute_mcp_output` in `scripts/test_pipeline.py` returns no match. The function doesn't exist in the current file. The user may be referring to a function that was renamed or removed in an earlier refactor.

The nearest match is `execute_mcp_tool_in_chain` in `tests/evaluation/execution.py:722` with `capability_name: str = "tool"` as a parameter default. But:
- That's a parameter default, not a function-name default.
- The function-name resolution at line 744 uses `_extract_function_name(source_code) or capability_name.replace("-", "_")` — the decorator-based extractor (post ext6 #20) resolves correctly for any well-formed MCP artifact.
- The "tool" fallback only fires when source_code can't be parsed AND `capability_name` is the literal "tool" — extremely rare in production.

Not reproducible as stated. Near-miss related to ext6 #20 which is already fixed.

### User's #130: Default function name "parse_csv" → **NOT CONFIRMED (wrong string)**

Claim: "The evaluation harness defaults tool function names to `parse_csv` if regex extraction fails."

**Actual (`tests/evaluation/harness.py:1408-1410`):**
```python
func_name = _extract_function_name(source_code) or getattr(
    artifact, "capability_name", "tool"
).replace("-", "_")
```

The fallback is `"tool"` (then `.replace("-", "_")`), NOT `"parse_csv"`. User's specific claim about `parse_csv` is wrong.

The pattern (there IS a fallback) is weakly relevant, but post-ext6-#20 the decorator-based extractor resolves function names correctly for every well-formed artifact. The fallback rarely fires; when it does, it uses the artifact's capability_name (usually a reasonable proxy). Not a distinct finding.

### Third-batch claims that did NOT become findings

### User's #179: Holm-Bonferroni step-down flaw (requires independence) → **NOT CONFIRMED (statistical claim is wrong)**

Claim: "Holm-Bonferroni... only valid if the p-values are independent."

**Actual statistical fact:** Holm-Bonferroni is a **closed-testing procedure** that is valid under **arbitrary dependence** of p-values. It controls the familywise error rate (FWER) without any independence assumption — this is one of its advantages over Benjamini-Hochberg (which assumes positive regression dependence). The user's concern confuses Holm's step-down with Hochberg's step-up (which DOES require independence).

Holm-Bonferroni is conservative (less powerful than methods that exploit independence), but **not invalid** under dependence. The current implementation is correct for task-level comparisons.

### User's #180: Fisher exact division-by-zero → **PARTIAL, low-impact**

Claim: "`fisher_exact_2x2` does not handle cases where a condition has zero successes and zero failures."

**Actual:** `fisher_exact_2x2` delegates to `scipy.stats.fisher_exact`, which handles degenerate [[0,0],[0,0]] tables by returning `(nan, 1.0)` (or similar) in current scipy versions — it does NOT crash. Edge cases may return NaN that propagates into downstream reports, but no mid-analysis crash.

Defensive hardening (catching the empty-stratum case at the call site and returning "N/A" instead of NaN) would be a polish improvement, but not a production-blocking bug. Not added as a finding.

### User's #181: KL divergence crashes on zero probability → **NOT CONFIRMED (smoothing IS applied)**

Claim: "KL divergence calculation fails to apply Laplace smoothing."

**Actual (`analyze_longitudinal.py:130-131`):**
```python
# epsilon-smooth + normalize
prev = prev + 1e-10
curr = curr + 1e-10
```

Epsilon smoothing IS applied BEFORE the KL computation. No zero-probability crash. User's claim doesn't match current code.

### User's #182: Integer truncation in cost attribution → **NOT MATERIAL**

Claim: "`//` (integer division) truncates costs to zero for small tasks."

**Actual:** `//` truncates to zero only when total_tokens < n_divisor. For realistic token counts (1000s-10000s per task) and divisors ~20-40, truncation is 1-digit precision loss at worst. Materially affects nothing in Chapter 6.

The concern is theoretically valid (precision loss on sub-1-token-per-task scenarios) but unrealistic for the actual workload. Not added.

### User's #186: Sequential Lead Agent corruption → **NOT CONFIRMED (cache properly gated)**

Claim: "Subtask 2 incorrectly receives the cached result from Subtask 1."

**Actual (`composition.py:842-848`):** Cache population is gated:
```python
if (
    ref.capability_type == CapabilityType.AGENT
    and isinstance(artifact_or_match, GeneratedArtifact)
    and artifact_or_match.agent_config
    and artifact_or_match.agent_config.get("subagents")
):
    _lead_cache[ref.capability_name] = output
```

Only **lead-with-subagents** AGENT artifacts are cached. Regular AGENT artifacts (no subagents) are re-invoked fresh for each subtask — no cache hit, no corruption.

For lead-with-subagents artifacts, the semantics are correct BY DESIGN: the lead agent is supposed to process all subtasks' work in a single invocation via its subagents. The cached output ALREADY covers all subtasks. Returning it for subtasks 2..N is the intended behavior.

User's concern about "different inputs" doesn't apply because lead-with-subagents receives all subtasks' descriptions at initial invocation time.

### User's #187: Advisory lock path mismatch → **NOT CONFIRMED (paths are identical)**

Claim: "`run_pilot.py` and `run_longitudinal.py` use different logic for the advisory lock file path."

**Actual:** All three runners pass `data_dir=DATA_DIR / "eval"` to `acquire_runner_lock`:
- `run_pilot.py:164` — `data_dir=DATA_DIR / "eval"`
- `run_longitudinal.py:127` — `data_dir=DATA_DIR / "eval"`
- `run_longitudinal_shift.py:244` — `data_dir=DATA_DIR / "eval"`

Inside `acquire_runner_lock` (`_eval_registry_lock.py:47`): `lock_path = data_dir / ".eval_runner.lock"`. All three runners compute the SAME lock path: `{ROOT}/data/eval/.eval_runner.lock`. Cross-runner locking works correctly.

### User's #188: Trace tag truncation by LangFuse SDK → **SPECULATIVE, not verifiable in code**

Claim: "LangFuse tags are limited in length; the harness generates tags like `format:csv,format:json...` which get silently truncated."

**Actual:** LangFuse tag length limits are SDK-internal. The harness generates per-tag strings like `format:csv` (10 chars each) which are well under any per-tag limit. TOTAL list size may have a limit but is not documented in the current reference; tags submitted have been observed to round-trip correctly in practice.

Without reproducible evidence (e.g., "here's a trace that shows tag loss"), this is speculative. Not added. If a concrete case of tag-list truncation is observed during Phase 5, re-evaluate.

### User's #189: test_mint.py environment variable overwrite → **PARTIAL (within-process, not cross-process)**

Claim: "scripts/test_mint.py forcefully sets `LANGFUSE_TRACING_ENVIRONMENT='test'`. If an auditor runs this script in the background while the main eval is running, it may poison the environment for the main process."

**Actual (`scripts/test_mint.py:38`):** `os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")`. `setdefault` only sets if the variable is ABSENT. If the main eval process has already set this variable (via .env load or CLI), test_mint respects the pre-existing value.

Cross-process: environment variables are per-process; test_mint's modification can't affect a different process's env.

Within-process: if a test orchestration loads test_mint's module, then loads the main eval's module WITHOUT pre-setting the env var, the main eval inherits "test". Narrow scenario, rare in practice. Not added.

### User's #194: M2 partial inconsistency → **DUPLICATE of ext9 #84**

Claim: "analyze_results.py treats `HumanLabel.PARTIAL` as success in one table but as failure in summary count."

**Actual:** Partial is treated as its own bucket — reported as a separate column `n_partial` (line 1054-1068) and NOT added to `n_contam` for the rate calculation. The user's framing of "success in one / failure in another" is slightly off; actual behavior is "separate bucket that the rate formula excludes."

This is the exact concern of ext9 #84 (M2 rate formula excludes partial, contradicting the proposal's "fraction containing fabricated elements" definition). Same remediation applies.

### User's #196: Schema metadata amnesia for pip_packages → **DUPLICATE of ext9 #83**

Covered in full detail at ext9 #83 (three-layer persistence chain). No new content.

### User's #200: Gini zero-count blindness → **DUPLICATE of ext9 #96**

Covered in full detail at ext9 #96 (zero-padding active capabilities). No new content.

---

### Third-batch verification note — ext8 #59/#64 confirmed correct

The prior-batch claim #149 (EvaluationRecord.success ignores shape_mismatch) was logged as ALREADY FIXED. The third-batch claim #196 (pip_packages amnesia) was logged as DUPLICATE. Both duplicates reaffirm that ext8's #59/#64 and ext9's #83 fixes land correctly against the current tip.

---

### User's #149: `EvaluationRecord.success` ignores `output_shape_mismatch` → **ALREADY FIXED (ext8 #59/#64)**

Claim: "`EvaluationRecord.success` ignores `output_shape_mismatch`, but `analyze_results.py` SQL includes it."

**Actual (`src/schemas/evaluation.py:348`):**
```python
return gate_passed and output_in_range and not output_shape_mismatch
```

The `is_success` classmethod at line 328-348 (and the `success` property at line 351-361 that delegates to it) DOES include `output_shape_mismatch`. This was the ext8 #59/#64 fix's exact purpose: factor success into a shared canonical predicate so Python objects + DuckDB SQL + LangFuse score all compute identically.

Docstring at line 344 explicitly cites the pre-fix divergence the ext8 fix closed. Current state: fixed. User's claim reflects pre-ext8-fix behavior.

---

## Relationship to ext6/ext7/ext8 findings

ext9's nineteen findings (first batch 7 + second batch 11 + third batch 1) span several classes:

| # | Class | Notes |
|---|---|---|
| 79 | Reporting inconsistency | Table 1 columns apply different predicates; reader confusion |
| 80 | Methodology conservativeness | JIT gap framing under-sells; honest but presentationally conservative |
| 81 | Statistical correctness risk | Pooled Fisher flagged in comment but printed anyway |
| 82 | Shift-marker timing off-by-one | Bundle with ext8 #56 (cycle attribution) |
| 83 | **Multi-layer persistence gap** | pip_packages lost at 3 layers; registry reuse structurally broken |
| 84 | Proposal-vs-implementation metric mismatch | M2 formula excludes partial labels |
| 85 | Missing-denominator reporting hazard | Failure taxonomy shows counts not rates |
| 86 | **Destructive over-reach in cleanup** | Ext6 #31 fix rmtree'd project source; uncovered hazard in the fix itself |
| 87 | Non-unique session ID | Cross-run LangFuse pooling; symmetric with ext7 #49 (DB isolation) |
| 88 | Pipeline-entry-point asymmetry | test_pipeline.py skips build_initial_state; same class as ext7 #53 |
| 89 | **Partial-truncation structural inconsistency** | MAX_CAPS_HARD truncates caps but keeps subtasks referencing dropped caps |
| 90 | Strict-gate coverage gap (retry scope) | DAG validator runs post-retry; not inside the retry budget |
| 91 | DAG-unaware sandbox state loading | Parallel branches see each other's state |
| 92 | Multi-capability subtask silent drop | First-only resolution in `_resolve_step` |
| 93 | Terminal-only in-band file collection | Intermediate SKILL/AGENT file content lost |
| 94 | Per-agent peak vs chain cumulative | Context Economy measurement mismatch |
| 95 | Compositional-execution precondition gap | Lead-agent-covers-many-subtasks triggers single-artifact fallback |
| 96 | **Gini population scope (zero exclusion)** | Pairs with ext8 #78 (historical pruned inclusion) |
| 97 | **Configuration-drift coupling** | `has_static_coverage` flag and `_run_static_implementation` whitelist drift; T19 crashes |

Cross-references:
- **#82 + ext8 #56**: both concern cycle attribution. #56 is the general off-by-one in `_load_cycle_data`; #82 is the shift marker's specific instance. Shared fix + same test suite.
- **#83 + ext8 #74**: both concern dependency handling. #74 (pip install timeout) is downstream of #83 (dependency info LOST before install attempted). Fix #83 first so #74 has accurate input.
- **#84 + ext8 #57 + #72**: three layers of M2 under-reporting. #57 resume-sampling bias; #72 variation-probe bypass; #84 partial-label exclusion. Bundle disclosure in Chapter 4.5.
- **#85 + ext8 #60**: reporting clarity cluster. #60 (severity-by-category overcount labels); #85 (failure taxonomy missing denominator). Chapter 6 reporting pass.
- **#86 + ext6 #31**: "the fix created a new bug." Ext6 #31 added the rmtree pre-snapshot logic; #86 is that it rmtree'd too widely. Pattern-name candidate: **fix-induced bug**. Guard: any "add rmtree" PR should document which paths are eval-state vs project-source.
- **#87 + ext7 #49 + ext7 #55**: cross-run isolation cluster. #49 DB isolation, #55 analyzer run_id scoping, #87 LangFuse session_id uniqueness. Same root issue: shared namespaces with no per-invocation uniqueness primitive.
- **#88 + ext7 #53**: both are pipeline-entry-point asymmetry (missing `build_initial_state`). #53 fixed longitudinal runners; #88 is the remaining smoke-test site.
- **#89 + ext8 #56**: #56 validates the DAG; #89 is that truncation breaks the DAG structurally before validation runs. Sequence: truncate (#89) → validate DAG (#56 guards against cycles but not against dangling subtask references).
- **#90 + ext6 §12 #14/#15/#16**: strict-gate-retry-coverage audit. #90 is the decomposer's specific gap for DAG failures. A one-pass audit for "which validators run inside the retry loop vs post-loop" catches future regressions.
- **#91 + ext8 #56 + #82**: compositional-execution correctness cluster. #56 attributes cycles correctly; #82 times the shift marker correctly; #91 filters upstream state correctly. All three must land for multi-step compositional chains to be correct.
- **#92 + #95**: CompositionalExecutor behavior gaps. Both silently-ignore cases (multi-cap subtasks, single-artifact-covering-many-subtasks).
- **#93 + ext6 #33**: file-output handling. #33 (T07/T11 validator fail-open on missing file); #93 (intermediate file content lost). Chapter 4.5 file-output methodology note.
- **#94 + ext8 #66**: Context Economy measurement. #66 (U(c) ignores definition_token_cost); #94 (per-agent peak vs chain total). Both under-represent the cost Pillar 3 is supposed to minimize.
- **#95 + ext6 #29**: lead-cache handling. #29 (cache-hit records no phantom invocation); #95 (precondition for CompositionalExecutor was wrong so the cache never got to run). Fix #95 first.
- **#96 + ext8 #78**: Gini population scope. #78 excludes pruned historical; #96 excludes zero-invocation active. The proposal-correct scope is "currently-active capabilities, zero-padded." Bundle both.
- **#97 + ext6 #20 / ext7 #53**: configuration-drift cluster. All three are "two sources of truth for the same fact drift out of sync" — #20 (schema vs extractor), #53 (pipeline-entry constructor), #97 (has_static_coverage vs implementation whitelist). Guard pattern: single-source-of-truth + startup-assertion for the derived sites.

Guard patterns added by this extension:

- **Consistent predicates across table columns** (#79): every column in the same table should apply the same correctness criteria, or clearly label its scope.
- **Report full-system comparison separately from cluster-range** (#80): when reporting a range across a cluster, also report the headline member's individual comparison.
- **Remove or clearly segregate statistically-fallacious diagnostics** (#81): internal diagnostics with known limitations shouldn't appear in tables a reviewer would quote. Either gate them behind a `--diagnostic` flag or clearly mark "DO NOT CITE."
- **Cycle-boundary timing for synthetic events** (#82): when emitting a cycle_events row tied to a cycle boundary, explicitly set `occurred_at` to the boundary timestamp rather than inheriting the wall-clock.
- **Dependency metadata as a first-class schema field** (#83): anything the executor needs at runtime should be part of the artifact's persistent representation; implicit "generator knows" is a recipe for round-trip data loss.
- **Proposal-vs-implementation metric audit** (#84): every metric's formula should have a one-line comment citing the proposal phrase it operationalizes, so drift is visible.
- **Denominator-always rule** (#85): any table of counts across categories should include the denominator, ideally both count and rate.
- **Destructive-operation path audit** (#86): every `shutil.rmtree` call should pass through a whitelist (eval-state paths only); project-source paths should be flagged at static-analysis time.
- **Namespace uniqueness at producer side** (#87): any external-system identifier (LangFuse session_id, DB path, run_id) produced from a non-unique input (seed) must be combined with a per-invocation unique suffix (timestamp or uuid).
- **Validator scope inside retry budget** (#90): a retry loop should cover every validation that declares "this is a retryable failure." A one-pass audit of retry-loop contents vs validators defined in the same module catches future regressions.
- **DAG-aware state propagation** (#91): sandbox code that merges upstream state should filter by the calling subtask's `depends_on`, not glob-match all prior outputs.
- **Compositional-precondition integrity** (#95): fall-through conditions between compositional vs single-artifact execution should be tested against every supported chain shape (including lead-with-subagents, multi-artifact parallel, single-artifact single-subtask).
- **Population-scope disclosure for metric definitions** (#96 + ext8 #78): every aggregate metric should document the exact population it aggregates over, especially when "active" or "pruned" or "historical" is ambiguous.
- **Configuration drift detection** (#97): any pair of sites that encode the same fact (e.g., "which tasks have static coverage" declared in tasks.py AND whitelisted in harness.py) should have a module-load-time assertion that the two agree. Prevents silent drift from accumulating across edits.

---

## Issue #98 — Writer-subtask description uses bare filename; LLM emits `open("/<filename>")` (root path) instead of `open("/workspace/<filename>")` (P1)

### Summary

When a chain step needs to write a file, the writer subtask description carries a bare filename (`report.md`, `metrics.csv`) without the `/workspace/` prefix. The MCP_TOOL generator's prompt example uses `/workspace/output.<ext>` literally — the LLM sees both signals and sometimes resolves the contradiction by emitting `open("/<filename>")` (filesystem root). The Docker sandbox correctly returns `[Errno 13] Permission denied: '/report.md'` and the chain fails at the writer step.

Two paths produce bare-filename writer descriptions:

1. **Decomposer-emitted writers** (T16-shape, AGENT or any task whose user prompt names files without `/workspace/`). The decomposer LLM mirrors the user-facing filename text (e.g. *"write to report.md"*) into a writer subtask. Example: `gap_query="write-markdown-report"`, description includes `report.md`.
2. **`_ensure_file_writer_last_step` synthesized writers** (T07/T11-shape). Writer description literally says `f"Write the upstream result to {filename}..."` with `{filename}` as the bare entry from `OutputContract.required_files`.

The bug is **not test-only** — `scripts/test_mint.py` invokes `src/cli/runner.py::execute_task` directly, the same entry point a real `mint run` user hits. Production CLI invocations on tasks like *"write a markdown report to report.md"* would fail identically.

### Where

- **Bare-filename in synthesized writer:** `src/orchestrator/decomposition.py::_ensure_file_writer_last_step` (line ~418, commit `02e7268` 2026-04-16). Description: `f"Write the upstream result to {filename} as {fmt_label}, ..."`.
- **MCP_TOOL prompt's narrow example:** `src/orchestrator/prompts/mcp_tool_generator.py` (line ~240). Used `/workspace/output.<ext>` literally — gave the LLM no anchor for arbitrary filenames.
- **Failure surface:** `tests/evaluation/execution.py::execute_mcp_tool_in_chain` log: `Chain MCP_TOOL <name> raised: [Errno 13] Permission denied: '/report.md'`. ChainResult: failed at writer step.
- **Test that exposed it:** `scripts/test_mint.py --agent-only` on T16-shape AGENT-YAML incident task (2026-04-20).

### Reproduction

Run `mint run "Write a markdown report to report.md from the upstream analysis"` (or `test_mint.py --agent-only`). Pipeline succeeds through decomposition + classification + generation + verification (all gate-passing). Chain executes upstream steps fine. Writer step crashes with `PermissionError: '/report.md'`. CLI exit code 6 (execution failure).

LLM-generated source code grep confirms:
```
85:     # Write Markdown report to /report.md
86:     md_path = "/report.md"
87:     with open(md_path, "w", encoding="utf-8") as f:
105:    csv_path = "/metrics.csv"
106:    with open(csv_path, "w", encoding="utf-8", newline="") as csvf:
```

### Threat model / impact

- **Production CLI breaks on file-output tasks.** Any user-facing prompt that names file outputs without the `/workspace/` prefix has a non-trivial probability of hitting this — the failure rate depends on which tool happens to be generated.
- **Decomposer-emitted writers are the worst case.** `_ensure_file_writer_last_step`-synthesized writers at least share the `output.<ext>` shape with the prompt example (T07/T11 land OK most of the time). Decomposer-emitted writers (T16-shape) have zero anchor.
- **Verifier doesn't catch it.** Tier 1 static scanner doesn't currently flag `open("/<bare-name>", "w")` as a privileged-path write. The bug only surfaces at sandbox execution time.
- **Direction of bias:** anti-AGENT (Chapter 6 M1 depressed for AGENT-shape file-output tasks) and anti-multi-step-chains (writers are typically last steps so the entire chain's prior work is wasted).

### Remediation

**A. Update `_ensure_file_writer_last_step` description to carry the explicit `/workspace/` prefix.**

```python
description=(
    f"Write the upstream result to /workspace/{filename} as {fmt_label}, "
    "serializing the per-key upstream data into the requested file format "
    "and returning the output file path. The file MUST be written to "
    f"/workspace/{filename} (NOT /{filename} or any other absolute path) "
    "so the compositional executor can collect it from the sandbox and "
    "ship it to the host's data/<env>/mint_output/<run>/ directory."
)
```

**B. Strengthen the MCP_TOOL generator prompt with an explicit path-coercion rule.**

```
**CRITICAL — file path placement.** The Docker sandbox ONLY mounts
/workspace/. Writing to any other absolute path (/report.md,
/output.csv, /tmp/report.md) will either raise PermissionError
(root paths) or land in a directory _collect_output_files does
not scan. If the subtask description references a bare filename
like report.md or metrics.csv (no leading /), you MUST treat it
as a workspace path: open("/workspace/report.md", "w") — NEVER
open("/report.md", "w") and NEVER open("report.md", "w").
```

**C. Tier 1 static scanner gap (follow-up).**

Add an AST regex for `open("/<word>", "w"|"a"|"x")` patterns where the path doesn't start with `/workspace/`. This would catch the bug at verification time instead of execution time. Defer to a follow-up finding (separate scope).

**D. Regression test.**

```python
def test_writer_description_includes_workspace_prefix():
    """_ensure_file_writer_last_step writer descriptions must say '/workspace/<filename>'."""
    subtasks = [SubTask(description="upstream", required_capabilities=["x"], depends_on=[])]
    augmented = _ensure_file_writer_last_step(subtasks, ["report.md"])
    writer = augmented[-1]
    assert "/workspace/report.md" in writer.description
    assert "/report.md" not in writer.description.replace("/workspace/", "")
```

### Recommendation

**P1 — land before any Phase-5 file-output task.** ~30 LOC across two files (decomposition.py + mcp_tool_generator.py prompt) + 1 regression test. ~30 min. Both fixes are safe to ship together; they reinforce each other (decomposer description steers the LLM correctly, generator prompt corrects bare filenames defensively).

### Legacy data

Generated tools registered with hardcoded `/<filename>` paths in their `source_code` are still in ChromaDB. Either: (a) `mint reset` to clear the cache (preferred for clean baselines), or (b) leave them and let registry-match misses re-trigger generation under the new prompt.

### Cross-references

- Surfaced during ext9 verification re-runs (`scripts/test_mint.py --agent-only` on 2026-04-20) AFTER ext9 #92 was downgraded to a soft warning. The downgrade unblocked a chain that previously stopped at decomposition; chain ran further and exposed the path-resolution bug.
- Pre-existing in commit `02e7268` (2026-04-16, §12 finding #10). Latent for 4 days because none of the testbed tasks with `required_files` named files outside the `output.<ext>` shape, and no eval was run against T16-shape user prompts in the interim.
- **Verifier-coverage gap (separate concern):** Tier 1 doesn't flag `open("/<word>", "w")` writes outside `/workspace/`. This is a sibling finding worth filing in ext10 — same root pattern as ext9 #97 ("verification didn't catch it") but for path safety instead of static-coverage drift.

---

## Parent-document integration (pending)

When ext9 lands, fold forward into `security_audit_2026_04_19.md`:

1. Update header: "Seventy-eight findings → Ninety-eight findings; #56-#98 open as of 2026-04-20."
2. Insert TL;DR rows #79-#98 after ext8's #78.
3. Tranche 3 (eval-blocking) gains **four P0** (#83, #86, #89, #97), **three P1** (#82, #90, #98), **thirteen MEDIUM** (#79, #80, #81, #84, #85, #87, #88, #91, #92, #93, #94, #95, #96).
4. Extend "Behavioral regressions expected":
   - (#79-#85 first batch) as previously listed.
   - (#86) Longitudinal runners no longer rmtree `src/mcp_tools`; `take_clean_snapshot` asserts the path is not inside project source.
   - (#87) Session ID includes timestamp suffix; two same-seed runs get distinct LangFuse sessions.
   - (#88) `test_pipeline.py` uses `build_initial_state`.
   - (#89) Decomposition truncation also drops subtasks referencing dropped caps (or fail-closes on >MAX_CAPS_HARD).
   - (#90) DAG validator runs inside the retry loop; exhaustion flips sticky flag.
   - (#91) `execute_mcp_tool_in_chain` filters `step_*_output.json` by the subtask's `depends_on`.
   - (#92) Multi-capability subtasks raise a validation error (or execute sequentially).
   - (#93) `run()` checks every step's `_output_file_content`, not only the terminal step.
   - (#94) `analyze_context_utilization.py` reports chain-cumulative tokens alongside per-agent peak.
   - (#95) Compositional precondition widened: any multi-subtask chain with ≥1 resolved artifact routes through CompositionalExecutor.
   - (#96) Gini zero-pads counts list with all active registry capabilities.
   - (#97) `_run_static_implementation` whitelist aligned with `has_static_coverage` flags; missing T19 branch added OR T19 flag flipped to False; dead T03 branch removed; startup assertion catches future drift.
   - (#98) Writer-subtask description includes the `/workspace/` prefix (decomposition.py); MCP_TOOL generator prompt forbids non-`/workspace/` absolute writes and explicitly coerces bare filenames; surfaced post-#92-downgrade by `test_mint --agent-only` on T16-shape AGENT-YAML; affects production CLI on file-output prompts named without the workspace prefix.
5. End-of-audit note: "Extended 2026-04-20 (ninth extension) with findings #79 - #98 (detail sections in `security_audit_2026_04_19_ext9.md`). Three reviewer batches submitted 49 total claims; 20 distinct findings confirmed (#79-#98), 30 logged as overstated/duplicate/design-choice/already-fixed in §Claims not added. #98 surfaced during ext9's own verification re-runs and is filed inline rather than as a separate ext10."

---

**End of extension.**
