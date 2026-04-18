# Test Suite Behavioral Audit — Master Synthesis

**Date:** 2026-04-13
**Branch:** `iter/6-execution-backends`, post-Phase-3.5c, pre-B1
**Scope:** All 44 test files in `tests/unit/` + `tests/integration/` (~16,655 LOC), cross-referenced against `src/`
**Follow-up to:** `docs/archive/test_suite_audit_2026_04_13.md` (import-level audit, same day)
**Cluster reports:** `docs/archive/test_audit_cluster_{A,B,C,D}_2026_04_13.md`

---

## The question this audit answers

The earlier import-level audit asked: *"does each test still compile against the current source?"* — answer: yes, zero dead imports. This audit asks a harder question: ***"does each test still meaningfully protect what matters, given what the source currently does?"***

A test can import valid symbols, run, and pass — and still be useless if:
- It mocks at the wrong layer, so the assertion is trivially satisfied
- It asserts a property that used to matter but has been subsumed
- It tests a degenerate case the code no longer takes
- It would **still pass even if the real invariant were broken** — which is the failure mode that matters most when a fix is imminent

## Headline

**The suite is in good shape. Zero mandatory deletions. But 5 findings are critical and 4 of those are B1-adjacent — they describe tests that will keep passing even when B1 silently breaks the code they're supposed to protect.** The most important action item is **augmenting 2-3 optimizer tests before B1 lands**, not deleting anything.

---

## The 5 critical findings

### Critical #1 — Optimizer merge/split tests pass against metadata-only stubs

**This is the single most important finding of the audit.** Cluster D, findings #1 and #2.

`tests/unit/test_optimizer_operations.py:395` (`test_merge_skills`) and `:159` (`test_split_skill`) assert only `capability_type`, `domain`, and `tags` on the merged/split `CapabilityDescriptor`. They **do not** assert anything about `source_code`, `skill_content`, or `agent_config`.

Today, `merge()` and `split()` in `src/optimizer/operations.py:304-311, 190-198` produce metadata-only stubs — no code fields at all. The tests pass **not because the code is correct**, but because the assertions don't check the fields that are missing.

**Consequence for B1:** when Gap 2 fix lands (`merge()` retains the highest-reuse parent's implementation), the test will **still pass** — it won't verify that the fix actually worked, and a future regression that reverts merge to stubs would not be caught. The tests are "pass for the wrong reason" right now, and will remain so after B1 unless augmented.

**Action (pre-B1, ~15 min):** add assertions like:
```python
assert new_cap.skill_content or new_cap.source_code or new_cap.agent_config
```
The assertions can be added **before** the B1 fix — they'll *currently fail*, which is exactly what you want: a red test that turns green when the fix lands. This is a much stronger signal than "green both before and after."

### Critical #2 — Merge candidate selector has zero regression protection for the Jaccard conjunct

Cluster D, finding #3.

`tests/unit/test_optimizer_loop.py:180-209` (`test_merge_candidates_use_similarity`) verifies only that description cosine ≥ 0.5 forms a candidate pair. It does **not** cover the "overlapping input schemas" conjunct that B1 is about to add.

**Consequence:** B1 will add `_schema_jaccard()` + a `[0.5, 1.0)` gate to `identify_candidates_node`. The existing test will still pass because it doesn't exercise the new gate at all. And after B1 lands, nothing in the suite protects the second conjunct from silent removal.

**Action (at B1 time, ~15 min):** when the `_schema_jaccard` helper is added, also add `test_merge_candidates_reject_low_schema_overlap` — a test that constructs two capabilities with high description cosine but disjoint schemas and asserts NO merge pair is formed. Requires the helper to exist, so this is strictly post-B1.

### Critical #3 — Verification fail-open contract has zero exception-path coverage

Cluster C, findings #2, #9, #10.

The verification layer's most important contract is *never-raises*: `verify()` catches any exception and returns a MEDIUM `gate_error` report; `sandbox_execute()` always returns a dict; `behavioral.run()` always returns a list; `adversarial.probe()` always returns a `VerificationReport`. This contract exists because the pipeline cannot tolerate verification crashing — it has to fail open and continue.

**But the exception paths are never exercised in tests.** `test_gate.py:129-139` covers only the happy path. `test_adversarial.py:177-196` mocks `_create_adversarial_agent` to always succeed. There is no test that patches `behavioral.run` to raise and asserts `verify()` returns fail-open MEDIUM. Nothing guards against a future refactor that accidentally breaks the try/except wrapper.

**Action (~30 min):** add 3 small tests:
1. `test_gate.py`: patch `behavioral.run` to raise → `verify()` returns `overall_passed=True` with a `gate_error` finding at MEDIUM.
2. `test_adversarial.py`: patch `_create_adversarial_agent` to raise TimeoutError → `probe()` returns partial report with `adversarial_timeout` finding.
3. `test_behavioral.py`: add an integration test with an MCP tool that includes `if __name__ == "__main__": raise RuntimeError("should not fire")`. Verify the `__name__ = "not_main"` neutralization works end-to-end (today only the string-position is unit-checked).

### Critical #4 — Compositional executor per-type fixture routing has no test

Cluster D, finding #8.

Phase 3.5c added a crucial behavioral distinction: MCP_TOOL steps receive fixture access via Docker workspace mount (`/tmp/mint_workspace_*/fixtures/`), while SKILL/AGENT steps receive fixtures via the `fixture_contents` dict injected into the prompt. Additionally, binary fixture handling has step-0-broad vs steps-1+-name-aware logic that prevents e.g. `.xlsx` paths from being blindly assigned to unrelated downstream parameters.

`test_composition.py` has 46 unit tests and 1037 LOC, but **none** of them isolate per-type fixture handling. The tests mock `execute_mcp_tool_in_chain` and `execute_skill_artifact` without asserting that the correct fixture-routing shape is passed through. A regression that gave MCP_TOOL the dict shape or SKILL the filesystem mount would not be caught.

**Action (~30 min):** add `test_composition_mcp_vs_skill_fixture_paths()` that patches both execution backends and introspects the `fixture_contents` argument each receives. Assert MCP_TOOL gets filesystem paths, SKILL/AGENT gets `{filename: content}` dict.

### Critical #5 — `schema_context` parameter (Phase 3.5c chain contracts) has no unit coverage

Cluster B, finding #5.

Phase 3.5c introduced `build_schema_context(gap, subtasks)` in all 3 generators (`src/orchestrator/generators/mcp_tool.py`, `skill.py`, `agent.py`). The function propagates chain data-flow contracts (upstream `output_schema`, downstream expected shape) into the generator prompt so each step knows what it receives and what it must emit.

No unit test in `test_mcp_tool_generator.py`, `test_skill_generator.py`, or `test_agent_generator.py` passes a non-empty `schema_context` to the generator. The only coverage is "the function is called" — the tests never verify that its output actually reaches the prompt. A regression that disconnects `build_schema_context` from the prompt template would not be caught.

**Action (~20 min):** add one test per generator: construct a gap + a subtask with an explicit `output_schema`, call the generator, capture the prompt sent to the LLM, assert the schema context is present in a stable form (key tokens, not exact substring).

---

## The 5 noteworthy but non-critical findings

These are real issues but don't block anything and can be deferred.

### Noteworthy #1 — `test_loading_node.py:111-139` skill/agent skip tests are WEAK

Cluster A, finding #7. `test_skill_artifacts_are_skipped` / `test_agent_artifacts_are_skipped` assert `loader.load_tool.assert_not_called()`, but the test state constructs **no gaps**. With `state.gaps == []`, status flips to COMPLETED and the load loop never runs — regardless of artifact type. The tests are passing for a reason unrelated to the skip logic they claim to test.

**Action (~10 min):** add gaps to the test state and re-assert that non-MCP artifacts still skip the load loop.

### Noteworthy #2 — Over-specified prompt substring assertions

Cluster B, findings #7, #8. `test_task_context_forwarded` in all 3 generator test files asserts that the exact `custom_context` string appears verbatim in the prompt. These tests will break on any prompt rewording even when the semantic intent is preserved. They protect against one regression (prompt disconnects) but couple the test to implementation detail (prompt template strings).

**Action (optional, ~15 min):** replace exact-substring checks with "prompt length increased" or "key tokens from context are present" assertions. Lower coupling, same protection.

### Noteworthy #3 — Tier 2 HIGH → MEDIUM clearance downgrade has no test

Cluster C, finding #6. `src/verification/gate.py:230-241` downgrades a Tier 1 HIGH severity finding to MEDIUM when Tier 2 is clean. This is meaningful behavior: it means a suspicious static pattern gets rehabilitated by clean behavioral testing. But no test exercises this downgrade path.

**Action (~10 min):** add a test that sets up Tier 1 HIGH (e.g., synthetic_data), Tier 2 empty findings, and asserts the final report has MEDIUM not HIGH for that check.

### Noteworthy #4 — `_generate_lead_with_subagents()` has no direct unit coverage

Cluster B, finding #9. Phase 3.5b lead-with-subagents routing is well-tested at the `agent_node` level (`test_single_agent_gap_uses_standalone_mode`, `test_multiple_agent_gaps_uses_lead_mode`, `test_lead_failure_falls_back`), but `_generate_lead_with_subagents()` itself is never called in isolation. Internal logic — `subagent_gap_queries` merging, frontmatter name rewrite, repair pass — is only exercised indirectly.

**Action (optional, ~30 min):** add a `TestGenerateLeadWithSubagents` class covering happy path, repair pass, and frontmatter rewriting.

### Noteworthy #5 — `test_registry.py` search tests over-specified to seed state

Cluster D, finding #11. `test_search_relevance` asserts `results[0][0].name == "csv-parser"` based on today's corrected seed descriptions. With the 2026-04-13 seed fix, this is currently correct, but it's brittle: any future registry-level change (re-attempting enrichment, seed name rotation, score threshold tweak) will break the test without a real regression.

**Action (optional, ~5 min):** lower specificity to `results[0][1] > 0.7` (score-based) rather than exact name match.

---

## Aggregate classification across all 4 clusters

| Classification | Count | Clusters contributing |
|---|---|---|
| **VITAL** (catches real regressions) | ~65 | A: 35, B: 22, C: 5, D: 3 |
| **OK** (protects minor but real property) | ~48 | A: 12, B: 28, C: 3, D: 5 |
| **WEAK** (passes for wrong reason / mocks at wrong layer) | ~18 | A: 3, B: 7, C: 1, D: 7 |
| **VESTIGIAL** (tests subsumed feature) | ~20 | A: 1 class (~20 tests), B: 0, C: 0, D: 0 |
| **OVER-SPECIFIED** (couples to implementation detail) | ~7 | A: 0, B: 4, C: 1, D: 2 |
| **DUPLICATE** (no additional coverage over sibling test) | 0 | none |

**Total non-trivial tests: ~158.** The suite skews strongly VITAL+OK (113/158, 71%). The WEAK+VESTIGIAL+OVER-SPECIFIED bucket is 45 tests (29%) — of those, only 18 are WEAK in a consequential way, and only 5 of those 18 are the critical-path findings listed above.

---

## What this audit confirms about the earlier import-level audit

The earlier audit (`docs/archive/test_suite_audit_2026_04_13.md`) concluded:
- No dead imports ✅ confirmed
- No stale shape assertions at the grep level ✅ confirmed
- No duplicative unit↔integration pairs ✅ confirmed
- One orphan module (`simulation.py`) requiring a thesis-level call ✅ confirmed
- One post-B1 augmentation opportunity (merge/split code retention) ✅ confirmed AND **expanded** — this audit found the same augmentation target plus the merge-candidate Jaccard test and the fail-open contract gap

**The earlier audit was not wrong — it just asked a shallower question.** Import-level checks catch the easy drift ("symbol no longer exists"). Behavioral checks catch the subtle drift ("symbol exists, test passes, invariant is unguarded").

---

## Recommendations by time horizon

### Before B1 starts (~15 min)

1. **Augment `test_merge_skills` and `test_split_skill`** to assert that the merged/split descriptor has at least one non-None code field. These tests will turn RED immediately (stubs don't populate code fields), and turn GREEN when B1 Gap 2 fix lands. That red→green transition is exactly the regression signal you want.

This is the **one** action that's strictly pre-B1 because the B1 fix itself is the behavior the augmented assertion verifies. Doing it after B1 would mean both the fix and the test go green at the same time, which tells you nothing.

### During B1 (~15 min)

2. **Add `test_merge_candidates_reject_low_schema_overlap`** once `_schema_jaccard()` exists in `src/optimizer/loop.py`. Two high-description-cosine capabilities with disjoint schemas → no merge pair formed.

### Before Phase 4 smoke tests (~80 min total)

3. **Fail-open contract tests (~30 min)** — 3 small tests in `test_gate.py`, `test_adversarial.py`, `test_behavioral.py` covering the never-raises paths.
4. **Composition per-type fixture routing test (~30 min)** — `test_composition_mcp_vs_skill_fixture_paths()`.
5. **`schema_context` generator tests (~20 min)** — one per generator, covering Phase 3.5c chain contract propagation.

### Optional hygiene (any time, ~40 min)

6. Tighten `test_loading_node.py:111-139` to add gaps to state (Noteworthy #1).
7. Loosen over-specified prompt assertions (Noteworthy #2).
8. Add Tier 2 HIGH clearance downgrade test (Noteworthy #3).
9. Loosen `test_registry.py:test_search_relevance` to score-based (Noteworthy #5).

### Post-eval (when thesis Chapter 6 is drafted)

10. Decide `simulation.py`: if Chapter 6 cites `run_simulation()` figures, keep both the module and its tests; if not, delete both in one atomic commit (~15 min).
11. Decide `_generate_lead_with_subagents` direct unit test (Noteworthy #4): optional hygiene, not time-critical.

---

## Total time and effort

| Bucket | Tasks | Effort | When |
|---|---|---|---|
| **Pre-B1 critical** | 1 | ~15 min | Before touching `operations.py` |
| **With B1** | 1 | ~15 min | Once `_schema_jaccard` exists |
| **Pre-Phase-4 critical** | 3 | ~80 min | After B1, before smoke tests |
| **Optional hygiene** | 4 | ~40 min | Any time |
| **Post-eval** | 2 | ~30 min | After eval runs |
| **Total** | **11** | **~3 hours** | — |

---

## Still no deletions

To be absolutely clear: **this audit recommends deleting zero tests.** Every test file, including `test_optimizer_simulation.py`, protects some property — even if (in the simulation case) the property is "the research code still computes what it used to." The 45 tests classified as WEAK/VESTIGIAL/OVER-SPECIFIED should be **augmented, rewritten, or relabeled** — not deleted. Deletion is cheap to undo only until a future session forgets why the test existed.

The single genuine orphan (`test_optimizer_simulation.py` / `src/optimizer/simulation.py`) is a **thesis-level decision**, not a mechanical cleanup. Defer to post-eval when you know whether Chapter 6 cites the simulation harness.

---

## Recommendation for `docs/pre_eval_readiness.md` Section E

Section E currently describes a "full cleanup pass" with 6 subtasks (E1-E6). Based on this audit, Section E should be **scoped down and restructured** into:

- **E1 (pre-B1):** augment `test_merge_skills` / `test_split_skill` with code-field assertions. ~15 min.
- **E2 (with B1):** add `test_merge_candidates_reject_low_schema_overlap` once `_schema_jaccard` exists. ~15 min.
- **E3 (pre-Phase-4):** fail-open contract tests + composition fixture routing test + schema_context generator tests. ~80 min.
- **E4 (post-eval):** simulation.py decision. ~15 min.
- **E5 (optional hygiene):** prompt assertion loosening, loading-node skip test tightening, Tier 2 clearance test, registry search specificity. ~40 min.

The scripts audit (former E4/E5) is already complete — done earlier today.

**Net result:** Section E shrinks from "1-2 hours of deletion pass" to "~2 hours of targeted augmentation," with a clearer sequencing tied to B1 milestones. Want me to update Section E now?
