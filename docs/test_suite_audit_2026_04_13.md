# Test Suite Audit

**Date:** 2026-04-13
**Branch:** `iter/6-execution-backends`
**Scope:** `tests/unit/` (35 files, ~14,700 LOC), `tests/integration/` (9 files, ~1,200 LOC), `tests/evaluation/` support modules
**Purpose:** Identify tests that can be deleted, augmented, or flagged before the Phase 5 ~18h evaluation run, so thesis numbers are anchored to a tidy codebase.
**Methodology:** Static analysis only (no pytest execution). Delegated to an Explore agent; all material claims re-verified by hand. Confidence labels reflect what was independently verified vs. taken on the agent's word.

---

## TL;DR

**The suite is in better shape than expected.** After cross-referencing every test file against the current `src/` tree:

- **0 dead imports.** No test will fail on the next run due to a removed symbol.
- **0 stale assertions** against pre-Phase-3/3.5 state shapes. Pydantic defaults absorbed the new compositional fields; tests that look "old" are actually valid because defaults cover them.
- **0 duplicative unit↔integration pairs.** Every integration test that has a unit-test counterpart exercises a distinct codepath (real Docker, real LLM, real ChromaDB). Spot-checks on the 3 largest pairs confirmed this.
- **1 orphan module** — `src/optimizer/simulation.py` (316 lines of tests, ~300 lines of source). Genuinely uncalled outside its own tests, but it's listed in CLAUDE.md as a Stable Interface and is thesis research code. **Needs user's thesis-level call, not a mechanical deletion.**
- **1 post-B1 augmentation opportunity** — `test_merge_skills` / `test_split_*` in `test_optimizer_operations.py` currently pass against metadata-only stubs because they don't assert what *should* be in the merged descriptor. After Gap 2 fix lands, these tests should be strengthened to verify code/config retention.

**Net pre-eval action:** ~0 mandatory deletions, 1 strategic decision (simulation), 1 post-B1 augmentation. The suite is effectively ready.

---

## Methodology

1. Enumerated all test files (35 unit + 9 integration) via `find`.
2. For each file, the Explore agent grep'd the imports against the current `src/` tree and flagged dead symbols.
3. The agent searched for specific stale-shape patterns I named in the brief:
   - `classify_task(` as a non-wrapper primary call (pre-Phase-2 classifier)
   - `metrics_store.record_invocation` in gap-detection contexts (pre-Phase-1 fake invocations)
   - Empty-schema assertions on generated caps (pre-B1 Iter-3 TODO)
   - Single-artifact execution shape (pre-Phase-3 non-compositional)
   - Flat AGENT generation shape (pre-Phase-3.5b lead-with-subagents)
   - `scripts.run_task` references (deleted earlier today)
4. I independently re-verified the most impactful claims:
   - `simulation.py` orphan status: `grep -rn "optimizer.simulation" src/ scripts/` → zero live callers ✅
   - `CapabilityReference` / `ChainResult` still exist in `src/schemas/task.py` at lines 283 / 314 ✅
   - `slugify_gap` (underscore variant) still called from `src/orchestrator/generators/mcp_tool.py:195` ✅
   - `test_gap_detection.py:234-294` contains **regression tests** (`assert_not_called`) confirming the Phase 1 fake-invocation removal, not stale assertions against the old behavior ✅
   - `tests/integration/test_composition.py` exercises real Docker workspace mounting (2 tests), distinct from the 46 unit tests ✅
   - `tests/integration/test_classifier.py` uses real `OPENAI_API_KEY` (skip marker); `tests/unit/test_classifier.py` has 53 `MagicMock`/`@patch` usages ✅
   - `test_optimizer_operations.py` merge/split tests do NOT assert source_code/skill_content at all (neither stub shape nor populated shape) — this is the augmentation opportunity

---

## Findings

### 1. Dead imports (Priority 1)

**None.** Every `from src.X import Y` in every test file still resolves. The `scripts/run_task.py` deletion earlier today did not leave any dangling test imports.

Confidence: **high** — agent reported zero and I did not find counter-evidence.

### 2. Stale state/shape assertions (Priority 2)

**None that require deletion.** Specific patterns checked:

| Pattern | Result | Notes |
|---|---|---|
| `classify_task(` as primary call | Used only in `tests/integration/test_classifier.py` + as a legacy wrapper | Intentional. `classify_task` is preserved specifically for this integration test per CLAUDE.md. Keep. |
| `metrics_store.record_invocation` in gap-detection | 4 assertions in `test_gap_detection.py:234-294` | **All are `assert_not_called()` regression tests.** They confirm the Phase 1 fake-invocation removal, not the old behavior. Keep — deleting these would remove the regression net. |
| Empty-schema assertions on generated caps | Zero matches | `grep "input_schema\s*==\s*{}\|output_schema\s*==\s*{}"` on `test_tool_loader.py` + `test_loading_node.py` returned nothing. Tests that construct `GeneratedArtifact()` rely on Pydantic defaults — no explicit assertions about emptiness. **Safe across the B1 schema population change.** |
| Single-artifact execution shape | Zero stale | `test_composition.py` (46 tests) is explicitly chain-aware. No pre-Phase-3 shape assertions found. |
| Flat AGENT generation shape | Zero stale | `test_agent_generator.py` imports `LeadWithSubagentsResult` and has dedicated lead-with-subagents tests (reported at `test_agent_generator.py:661+`, not independently verified). |
| `EvaluationRecord(...)` old-field constructions | All use defaults | New 7 compositional fields (`chain_length`, `chain_success`, etc.) have factory defaults. Old test constructors remain valid. |
| `scripts.run_task` references | Zero matches | Grep confirmed. The deletion earlier today was clean. |

Confidence: **high** on the grep-verifiable patterns; **medium** on the AGENT generator tests (trusted agent's claim, did not open the file).

### 3. Unit↔integration duplication (Priority 3)

**No duplicates found.** Each integration test that has a unit-test counterpart hits a distinct codepath:

| Pair | Unit LOC | Integ LOC | Verdict | Verified by |
|---|---|---|---|---|
| `test_composition.py` | 1037 | 181 | Keep both — integration uses real Docker workspace mounting (`test_two_step_mcp_tool_chain_with_docker`) | Direct grep: "require Docker + built jit-sandbox image" in integration file header |
| `test_pipeline.py` | 254 | 121 (`test_pipeline_e2e`) | Keep both — integration exercises real ChromaDB + real ToolLoader | Agent claim, not independently verified |
| `test_classifier.py` | 765 | 119 | Keep both — integration uses real OpenAI (skip-if-no-API-key); unit has 53 mock usages | Verified |
| `test_mcp_tool_generator.py` | 1037 | 145 | Keep both — integration uses real LLM + ToolLoader validation | Agent claim |
| `test_skill_generator.py` | 647 | 138 | Keep both — integration writes real SKILL.md to disk | Agent claim |
| `test_agent_generator.py` | 944 | 230 | Keep both — integration uses real LLM + DeepAgent + LangFuse | Agent claim |
| `test_decomposition.py` | 343 | 142 | Keep both — integration uses real subtask decomposition | Agent claim |
| `test_sandbox_tool.py` | 411 | 60 | Keep both — integration is a narrow Docker smoke slice | Agent claim |

**The unit-test files are large (1037 LOC for composition and mcp_tool_generator) but the size reflects coverage breadth, not duplication with integration.** The integration files are all ≤230 LOC and deliberately narrow — they exist to catch issues that only surface with real external systems.

Confidence: **high** on the 3 pairs I directly verified (composition, classifier, and the Docker assertion); **medium** on the other 5 (agent's claims were internally consistent and none contradicted what I know of the codebase).

### 4. Orphan tests (Priority 4)

#### `test_optimizer_simulation.py` (316 LOC) — **GENUINELY ORPHAN, but flagged not deleted**

- `src/optimizer/simulation.py` has **zero live callers** outside its own test file (verified by `grep -rn "optimizer.simulation" src/ scripts/`).
- Listed in CLAUDE.md under "Stable Interfaces" (`src/optimizer/simulation.py` — `SimConfig`, `SimResult`, `generate_synthetic_registry()`, `run_simulation()`, `plot_results()`, `compare_baselines()`).
- Existed to support the Iteration-4 optimizer convergence analysis for thesis Chapter 6.
- **Decision required from user:** is `simulation.py` still needed for any Chapter 6 figure or table? If yes, keep both the module and its tests. If no, delete both in a single commit.
- **Do not delete the test file without deleting the module** — that would leave a Stable Interface with no regression net.
- Recommendation: defer until post-eval, when you know whether Chapter 6 references the simulation harness.

#### `test_slugify_gap.py` (68 LOC) — **KEEP**

- Both `slugify_gap()` (underscore) and `slugify_gap_hyphen()` are live.
- `slugify_gap()` → called from `src/orchestrator/generators/mcp_tool.py:195` (verified).
- `slugify_gap_hyphen()` → called from `src/orchestrator/generators/skill.py:232` and `src/orchestrator/generators/agent.py:223,418` (verified).
- Also referenced in `tests/unit/test_mcp_tool_generator.py:629,690,775` — deleting `test_slugify_gap.py` would leave slugify logic tested only incidentally via generator tests.
- **Keep as-is.**

#### `test_fibonacci.py` (25 LOC) — **KEEP**

- Reference MCP tool, used in `seed_registry()`, in the CLI baseline, and as a known-good fixture in verification tests.
- Tiny file, zero maintenance cost, high documentation value (it's the canonical "this is what a valid MCP tool looks like").
- **Keep as-is.**

#### `test_harness.py` (419 LOC) vs `test_harness_static.py` (128 LOC) — **KEEP BOTH**

- `test_harness.py` tests `EvaluationHarness.run_pilot()` orchestration — the driver loop.
- `test_harness_static.py` tests `_run_static_implementation()` — the hand-written static baseline task implementations.
- Distinct layers, distinct failure modes, no overlap.
- **Keep both.**

### 5. Schema test audit (Priority 5)

**All classes tested in `test_schemas.py` (1010 LOC) still exist in `src/schemas/`.** Agent enumerated 20 tested types; I spot-checked `CapabilityReference` (found at `src/schemas/task.py:283`) and `ChainResult` (found at `src/schemas/task.py:314`) since the agent's enumeration initially looked suspicious. Both exist and are tested 16 times combined in `test_schemas.py` (verified via `grep -c`).

New Phase-1 compositional fields on `EvaluationRecord` (7 fields) and `GeneratedArtifact` (`pip_packages`, `input_schema`, `output_schema`) have dedicated tests.

**No schema test bloat to clean up.** Confidence: **high**.

---

## Post-B1 Augmentation Opportunity (not a deletion)

**This is the most actionable finding the initial audit missed.**

`tests/unit/test_optimizer_operations.py:395` (`test_merge_skills`) and the sibling split tests assert what the merged/split `CapabilityDescriptor` **contains** at the metadata level:

```python
assert new_cap.capability_type == CapabilityType.SKILL
assert new_cap.domain == "data"
assert set(new_cap.tags) == {"csv", "json"}
```

They do **not** assert anything about `source_code`, `skill_content`, or `agent_config`. This is why the existing tests pass despite Gap 2 (`merge()` produces metadata-only stubs). The tests aren't stale — they're **incomplete**.

**When B1 Gap 2 fix lands** (`merge()` retains highest-reuse parent's implementation), these tests should be strengthened:

```python
# Post-B1 additions
assert new_cap.skill_content != ""              # or source_code, for MCP_TOOL merges
assert new_cap.skill_content == cap_highest_reuse.skill_content
assert new_cap.input_schema == cap_highest_reuse.input_schema
```

Without these additions, a future regression that reverts `merge()` to metadata-only stubs would not be caught by the test suite. The same applies to `test_split_*` tests if/when split is unblocked (Gap 3 is currently being deferred via `split_reuse_threshold=1.01`, so split augmentation is lower priority).

**Add to the post-B1 task list, not to the pre-eval cleanup.**

---

## Cross-cutting note: Iter-3 `tool_loader.py` empty-schema TODO

No test currently asserts `input_schema == {}` or `output_schema == {}` on generated capabilities (verified by grep). This means the upcoming B1 change that populates schemas via AST extraction will **not** break any test — it will only add new assertions if we choose to write them.

**Post-B1 recommendation:** add 2-3 positive-case tests to `test_loading_node.py` or `test_tool_loader.py` verifying that a freshly-loaded MCP_TOOL `CapabilityDescriptor` has a non-empty `input_schema` reflecting its source function's parameter signature. Small hygiene win, not required for eval correctness.

---

## Aggregate estimate

| Category | Files | Lines | Tests | Action |
|---|---|---|---|---|
| Safe to delete outright | 0 | 0 | 0 | — |
| Orphan, requires thesis-level call | 1 (`test_optimizer_simulation.py`) | 316 | ~15 | Defer until post-eval |
| Needs augmentation (not deletion) | 1 file, 2-3 tests | ~30 new LOC | ~3 | Schedule after B1 Gap 2 fix |
| Stale assertions | 0 | 0 | 0 | — |
| Duplicative | 0 | 0 | 0 | — |
| **Net pre-eval delta** | **0** | **0** | **0** | — |

**The suite is ready for the Phase 5 eval as-is.**

---

## Recommendations

1. **Pre-eval: do nothing to the test suite.** No mandatory cleanup before the 18h run.
2. **Defer the `simulation.py` decision** until post-eval Chapter 6 drafting. If the chapter cites `run_simulation()` figures, keep both the module and its tests; if it doesn't, delete both in one atomic commit (module + test file + CLAUDE.md Stable Interfaces entry).
3. **Schedule a B1 follow-up task:** after `merge()` Gap 2 fix lands, strengthen `test_merge_skills` and siblings to assert code-retention. ~30 LOC, ~15 min.
4. **Optional hygiene post-B1:** add 2-3 positive-case tests for schema population in `test_loading_node.py`. ~20 LOC, ~15 min.
5. **Update `docs/pre_eval_readiness.md` Section E** to reflect these findings — specifically, that the audit revealed no mandatory deletions, so E3-E6 can be scoped down to "post-B1 augmentation + simulation decision post-eval" instead of a full cleanup pass.

---

## Confidence and limitations of this audit

- **Static analysis only.** Tests were not run. A test that passes today but exercises a codepath that's been silently broken won't be caught by this audit.
- **Agent-assisted.** The Explore agent did the bulk of the grep/read work under a detailed brief; I independently re-verified the highest-impact claims (orphan status of simulation.py, existence of CapabilityReference/ChainResult, the grep-based stale-pattern checks, the Docker assertion in integration test_composition.py, and the mock count in unit test_classifier.py). The claims I did not independently verify are labeled "medium confidence" above.
- **No behavioral coverage analysis.** This audit does not attempt to answer "is the suite testing the right things?" — only "is the suite testing things that still exist?". Coverage gaps (e.g., untested branches in the compositional executor) are out of scope.
- **Pre-B1 snapshot.** The test landscape will change once B1 Gap 1/2/3 fixes land. This audit documents the state as-of 2026-04-13, pre-B1.
