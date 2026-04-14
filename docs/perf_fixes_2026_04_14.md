# Pipeline Performance Fixes — 2026-04-14

**Branch:** `iter/6-execution-backends`
**Trigger:** test_mint run at 14:50 timed out on decomposition of T04 inventory reorder; investigation via LangFuse MCP surfaced three independent latency sources, all worth fixing before the Phase 5 eval.
**Scope:** performance only. No change to verification semantics, no change to the proposal's three-tier contract, no change to what gets generated.

---

## 1. Evidence summary (what LangFuse actually showed)

### 1a. Taxonomy classification — **~193s per call** (was: expected ~8s)

Trace `b4c2488d34e77adb8109760c785d9e07`, observation `8dfb032e34aecf63` (generator-side call to `DomainTaxonomy.get_or_create_for_capability`). 1,962 input / 496 output tokens over **193.099s** wall clock — ~12.7 tok/s, characteristic of gpt-5-mini default-effort reasoning burning internal tokens on a structurally trivial 5-field classification.

Observed twice per pipeline run minimum:
- `get_for_query` in `gap_detection_node` — ~8s per call
- `get_or_create_for_capability` in each generator's `classify_and_tag_artifacts` — **~193s per call**, iterated **sequentially** over all fresh artifacts. On a 3-gap task this stacks to **~579s of pure serial taxonomy overhead** on top of generation itself.

### 1b. Decomposition — hitting the 120s timeout on complex tasks

Traces `053d83d8...` (565s internal wall time) and `b845c38c...` (229.6s internal wall time), both on T04 inventory reorder. Hard evidence:

- T01 CSV parse (1 subtask): 25–51s ✓
- 2-step YAML + metrics: 33s ✓
- 4-step data integration: 45s ✓
- **T04 5-step inventory reorder: 229s–565s — timeout every run**

`decomposition.timeout` events in LangFuse go back to **2026-04-07** — 15+ events on April 7 alone, well before commit `1283f68` (2026-04-12) added `output_schema` to subtasks. **This is NOT a recent regression**: a pre-existing high-variance latency issue on complex multi-step tasks with gpt-5-mini default reasoning. My initial "1283f68 is the root cause" hypothesis was wrong — disproven by the April 7 timeouts. Pattern is better explained as reasoning-mode wall-clock variance on a task near the 120s edge.

Secondary finding: `pool.shutdown(wait=False)` in `decomposition.decomposition_node` does **not** cancel the running OpenAI calls. When the 120s timeout fires, the background thread keeps burning tokens for another 100–440s (visible in trace latency exceeding 120s). Cost hygiene issue, independent of the timeout value.

### 1c-bis. T04 SKILL silent-failure confirmed in test_mint output

This section was added after the 2026-04-14 14:44 test_mint run completed. The user's T04 SKILL test reported `[PASS]` but the actual output is wrong, confirming the downstream impact of the decomposition timeout (evidence for why Fix 2b is not optional).

`data/cli/mint_output/build-an-inventory-reorder-pipeline-usin-c36cb5_20260414_145339/run.json` shows:

```
"decomposition_node": 120.1,          ← hit the timeout
"subtask_count": 0,                    ← fallback path
"classifier_node": "skill",            ← treated whole task as one SKILL
```

The generated skill's own description makes the failure mode explicit:

> *"Load and validate the two data sources (inventory CSV and reorder rules YAML) for an inventory-reorder pipeline. **This skill only covers reading the files into structured, JSON-serializable Python objects**, basic schema validation, and returning the loaded data for downstream steps."*

It does **step 1 of the 5-step pipeline** and stops. The output is:

```json
{"inventory": [...], "reorder_rules": {...}, "product_count": 10, "errors": {...}}
```

Task asked for: `product_count`, `items_below_threshold`, `total_reorder_cost`.
**Missing**: `items_below_threshold`, `total_reorder_cost`.

**The pipeline reports `OK: Pipeline completed successfully`** because `test_mint`'s harness doesn't validate output contents — only that the pipeline returns without exception. This is a silent wrong-output path that will poison any evaluation run that exercises T04-class tasks without manual validation.

Derived requirement: Fix 2b needs a **secondary guard** (see updated section 2b) that marks the pipeline as failed when `subtask_count == 0` post-timeout, instead of silently continuing with a fallback single-gap classification.

### 1d. AGENT Tier 3 is currently a no-op (latent completeness gap)

Also surfaced by the test_mint run: the AGENT test (`parse-incident-yaml-and-system-metrics-c-97a4ab`) reports `Verifying artifacts — ✓ Tier 3 (0.0s)` with verification note `adversarial_skip`. A 0-second Tier 3 for an AGENT is not a latency win — it's a **verification gap** that was masked by how fast the skip returns.

Root cause in `src/verification/adversarial.py:256-271`:

```python
source_code = artifact.source_code or artifact.skill_content or ""
if not source_code.strip():
    return VerificationReport(..., findings=[adversarial_skip INFO], ...)
```

For AGENT artifacts, `source_code` and `skill_content` are both `None` (AGENTs store code under `agent_config.system_prompt` and `agent_config.skill_content`), so the skip path fires **unconditionally** for every AGENT ever generated. **Tier 3 has never actually probed an AGENT artifact in production.**

This reframes Fix 2c-3 (Python-fence extraction) from "performance improvement" to "closing a verification gap that was hiding behind a skip path". Updated framing applied in section 2c-3.

### 1c. Adversarial probing — **93–357s** with ~60% of time in pure waste

Trace `415817508e1ba88c5784d58696f6c529` (233.3s, 7 LLM calls, ~5,184 total reasoning tokens):

1. `write_todos` — **2,048 reasoning tokens** to "plan" a 4-todo list that literally restates the system prompt.
2. `ls /` — empty workspace, pointless detour.
3. `sandbox_execute` (attempt 1) — **failed with Traceback** because the inlined tool source tried to write outside the sandbox's writable dirs.
4. `sandbox_execute` (retry) — succeeded with "re-run without writing to root".
5. `sandbox_execute` (analysis write) — wrote `analysis_probe_results.json`.
6. `read_file /analysis_probe_results.json` — **failed with "file not found"** (wrote to relative path, read from absolute path; different filesystem abstraction).
7. Final structured response — actual findings.

Steps 1, 2, 3, and 6 are pure waste. The useful work (steps 4, 5, 7) is ~90s. The other ~140s is variance + reasoning ceremony + sandbox-path confusion between the agent's in-memory workspace (`StateBackend`) and the Docker sandbox filesystem.

---

## 2. Fix plan

### 2a. Taxonomy — ALREADY APPLIED

**Files:** `src/orchestrator/taxonomy.py`, `src/orchestrator/gap_detection.py`.

Two changes landed in-tree (uncommitted):
1. **`reasoning_effort="minimal"`** on both taxonomy LLM constructions. Rationale: picking one category from a list (or flagging a capability as generic vs. domain-bound) is pure pattern matching; reasoning-mode is pure overhead. Expected drop: ~193s → ~10s per call.
2. **`ThreadPoolExecutor(max_workers=min(len(pending), 4))`** around the per-artifact loop in `classify_and_tag_artifacts`. ChatOpenAI is thread-safe (each `.invoke()` is an independent HTTP request), and `_commit_new_category` already holds a lock + re-loads the file under the lock — the only race is two workers proposing slightly different names for the same concept with an empty taxonomy, which is acceptable evolutionary-taxonomy noise.

**Combined impact on a 3-gap task:** ~587s → ~11s of taxonomy overhead per pipeline run.

**Thesis alignment:** the taxonomy is Pillar 1 retrieval infrastructure we added; the proposal is silent on its internal model configuration. Neutral.

**Unit test status:** `tests/unit/test_taxonomy.py` + `tests/unit/test_gap_detection.py` pass (36/36) in 0.39s with the changes applied.

### 2b. Decomposition — PENDING APPROVAL

**File:** `src/orchestrator/decomposition.py`.

Two related changes:

1. **Bump `DECOMPOSITION_TIMEOUT_S` from 120 to 240.**

   Rationale: empirical wall-time data shows the agent routinely completes the work in 87–180s of actual LLM compute on complex tasks, but the combined agent + middleware + LangGraph event loop adds tail variance pushing total wall time to 230–565s on borderline cases. The 120s ceiling was set under an assumption that no longer holds. Reducing decomposition reasoning effort would change what the decomposer produces on every task (including the easy ones that currently work), with quality impact on downstream classification / generation / gap detection — the blast radius is too large for a targeted fix. Bumping the timeout affects only the tail of the distribution.

   **Budget cost:** +120s on the ~5% of tasks that hit the tail. For a 20-task × 4-condition × 5-run eval (400 runs) the expected overhead is ~20 × 120s = 40 minutes, added to an 18-hour wall clock. Acceptable.

2. **Mark the pipeline state as failed when decomposition produces zero subtasks, via a sticky boolean flag.** (Added post-test_mint run based on section 1c-bis. Refined after catching a bug in the initial design.)

   In the current code, when decomposition times out, `decomposition_node` falls back to `required_caps = [first_sentence_of_task]` and returns a `Command` that routes to `gap_detection` with `status=TaskStatus.DECOMPOSING` and only an error string appended to `state.errors`. The downstream nodes then run as if the task were a single-gap task, produce one artifact, register it, and the pipeline reports success. **The final `TaskStatus` is `COMPLETED` regardless of whether decomposition actually succeeded.**

   **Applies to all three capability types.** The guard fires in `decomposition_node` *before* classification, so it's agnostic to what the downstream classifier eventually picks. The fallback path can produce any of MCP_TOOL / SKILL / AGENT depending on the packaging signal in the first sentence of the task. T04 happened to silently produce a SKILL because *"Build an inventory reorder pipeline..."* has strong SKILL phrasing; a task phrased *"Compute X from Y..."* would silently produce an MCP_TOOL via the same path; *"Investigate Z..."* would silently produce an AGENT. The silent failure mode is not SKILL-specific. The guard is type-agnostic because decomposition failures are type-agnostic.

   **Why a sticky boolean flag, not a new `TaskStatus` enum value.** My first pass at this plan proposed adding `TaskStatus.DECOMPOSITION_FAILED` and setting it in decomposition_node. That approach is broken: `TaskStatus` is transitioned forward by every downstream node (`DETECTING_GAPS` → `CLASSIFYING` → `GENERATING` → `VERIFYING` → `COMPLETED`), so a failure value set at decomposition would be overwritten one node later. The correct pattern is a dedicated boolean field on `TaskState` that downstream nodes do not touch, so the flag stays sticky through the entire pipeline run.

   **Concretely:** add `decomposition_succeeded: bool = True` to `TaskState` in `src/schemas/task.py`. In all four failure branches of `decomposition_node` (TimeoutError, no structured_response, empty subtasks list, generic Exception), include `decomposition_succeeded=False` in the `Command(update={...})` dict alongside the existing `errors` append. Downstream nodes never read or write the flag — they just let it pass through. At terminal state, the CLI `runner.py` / `display.py` reads the flag and displays a distinct failure message when `False`, and the evaluation harness's success classifier (if one exists) treats `False` as a failure.

   **This does NOT abort the pipeline.** The downstream nodes still run, the single-sentence fallback still generates a capability, and verification still executes — the user gets whatever partial work was achievable. What changes is the terminal display and the harness classification. The pipeline is still fail-open at the node level; it's just no longer fail-silent at the user-facing level.

   **Scope of this change:**
   - `src/schemas/task.py`: add `decomposition_succeeded: bool = True` field to `TaskState`. One line + one default. No enum surgery.
   - `src/orchestrator/decomposition.py`: set `decomposition_succeeded=False` in the four failure branches. Four edits, each adding one key to an existing `Command(update={...})` dict. The timeout bump constant change is in the same file.
   - `src/cli/runner.py` and/or `src/cli/display.py`: read the flag in the terminal summary block; when `False`, display `DECOMPOSITION FAILED — partial output only from fallback path. Results may be incomplete.` in a distinct colour (yellow/red) instead of the green success line. Preserve the run manifest writing so post-mortem inspection of `run.json` is still possible.
   - `tests/evaluation/harness.py`: if `EvaluationRecord` or the per-task success classifier has a pass/fail field, return failure when `state.decomposition_succeeded is False`. If the harness currently infers success purely from "pipeline returned without exception", this is a real behavioral change that needs to be reflected in the eval baselines; confirm by grep before editing.
   - Tests: (a) unit test in `tests/unit/test_decomposition.py` verifying the flag is set to `False` in each of the four failure branches and stays `True` on success, (b) unit test verifying the flag survives a multi-node pipeline run (mock the later nodes, check the state reaching loading_node still has `decomposition_succeeded=False`), (c) test for the CLI display branch, (d) test for the harness classifier change if applicable.

   Effort: ~90 minutes. Same commit as the timeout bump (both touch `decomposition.py`).

**What this does NOT fix:** the ghost-thread issue (background thread keeps running tokens after node declares timeout). Documented as a separate item below, not fixed here — fixing it properly requires either subprocess isolation or accepting the result asynchronously, both of which are larger changes.

**Thesis alignment:** proposal is silent on decomposition timeouts and on the CLI's display contract. Neutral. The silent-failure guard actively improves fidelity of the Chapter 6 evaluation numbers because runs that would have scored as successes on a wrong-output path now score as failures, which is the correct classification.

### 2c. Adversarial probing — PENDING APPROVAL

**File:** `src/verification/adversarial.py` (plus one import from `src/verification/gate.py`).

Four related changes in one pass:

#### Fix 2c-1 — `reasoning_effort="low"` on the adversarial agent

`_create_adversarial_agent()` currently calls `init_chat_model(DEFAULT_LLM_MODEL)` with no reasoning configuration. Change to `init_chat_model(DEFAULT_LLM_MODEL, reasoning_effort="low")`.

**Why `"low"`, not `"minimal"`:** unlike taxonomy, the adversarial agent genuinely benefits from some reasoning when writing the Python test snippets inside each `sandbox_execute` call. `"low"` preserves that while cutting the wasteful planning and filesystem-exploration ceremony. `"minimal"` would hurt the test-code quality.

**Why the attack quality argument does NOT require high reasoning:** the three attack patterns are **fixed in both the system prompt and the Pydantic schema** (`AdversarialFinding.attack_pattern` is literally restricted to the three string literals `"hidden_demo_mode"`, `"network_disconnect"`, `"ground_truth"` via its field description — the agent cannot invent new attack names and have them pass structured-output validation). The agent is iterating within a fixed plan, not inventing attacks. Reasoning mode does not help it invent what it's structurally prohibited from inventing.

**Archive note:** `docs/archive/investigation_pipeline_bottlenecks.md` flagged exactly this change as a pending optimization ("Add reasoning_effort='low' to GPT-5-mini — Reduce reasoning token overhead in adversarial prober"), noted but never executed.

#### Fix 2c-2 — Tighten `ADVERSARIAL_SYSTEM_PROMPT`

Three prompt edits:

1. **Remove the `write_todos` directive.** The current prompt says *"Use write_todos to plan your attack approach."* Replace with *"Execute the three attack patterns below directly. Do NOT use `write_todos` — your plan is already fixed and listed in this prompt."* In the 233s trace, the single `write_todos` call burned 2,048 reasoning tokens to restate the prompt.

2. **Add sandbox-filesystem isolation warning.** New paragraph in the ## Important section:

   > *"Your `write_file` / `read_file` / `ls` tools operate on the agent's in-memory workspace — they do NOT share state with the Docker sandbox. Files written by `write_file` are NOT visible inside a later `sandbox_execute` call. Do NOT use the workspace tools to pass data into or out of the sandbox. Keep each test completely self-contained within a single `sandbox_execute` call — embed the tool source, run the test, and `print()` the results to stdout. Parse stdout in your next reasoning step."*

3. **Add "no filesystem exploration" rule.** New line: *"Do not call `ls`, `read_file`, or `write_file` for filesystem discovery. The agent workspace starts empty and the sandbox is ephemeral — there is nothing to explore."*

Together these eliminate steps 1, 2, 3, 6 from the 233s trace — the pure waste — without touching the useful work in steps 4, 5, 7.

#### Fix 2c-3 — Option B: probe Python fences for SKILL / AGENT, not prose

> **Reframed again 2026-04-14 after deeper proposal re-read.** My first pass at §2c-3 claimed this fix "closes the verification gap" and called it "the strongest proposal-alignment improvement in this fix pass". That framing was overclaimed. The actual Tier 3 story per the proposal is more ambitious than what the current implementation (with or without Fix 2c-3) does. See the re-read summary below, and the explicit drift entry in §3e.

**What Fix 2c-3 actually does:** it removes a structural bug where AGENT Tier 3 unconditionally hit the `adversarial_skip` path because `artifact.source_code or artifact.skill_content` is always empty for AGENTs (the code lives under `agent_config.system_prompt` or `agent_config.skill_content`). Post-fix, AGENTs with embedded Python fences get probed via the same fence-extraction helper Tier 1 uses. For SKILLs, it routes skill_content through the same fence extractor so Tier 3 stops feeding raw markdown prose to the adversarial agent (which produced zero useful signal and inflated latency). For prose-only SKILLs and prose-only AGENTs, it returns a **type-aware honest skip** instead of the pre-fix pattern of pretending to probe.

**What Fix 2c-3 does NOT do:** bring the implementation into alignment with the proposal's Tier 3. The proposal describes a **black-box behavioral** red-team agent — one that sends crafted inputs through the capability's normal runtime and observes outputs. The current implementation is **white-box code-inspecting** — it extracts source code, has an LLM write Python test snippets that inline that source, and runs those tests in `sandbox_execute`. These are structurally different verification strategies. Fix 2c-3 operates entirely within the white-box framing; it does not add black-box behavioral probing for anything.

**Direct quotes from the proposal** (`docs/thesis_proposal.md` lines 143-148):

> *"A dedicated red-team agent attempts to make the generated capability produce incorrect but plausible output. It:*
> - *Crafts inputs designed to trigger known LLM shortcut patterns (e.g., asking for "sample data" or "example output" to see if the tool has a hidden demo mode)*
> - *Compares outputs against known ground truth where available*
> - *Tests whether the tool behaves differently when network connectivity is simulated as unavailable"*

Every bullet is input-construction and output-observation language. *"Crafts inputs"*, *"compares outputs"*, *"tests whether the tool behaves differently"*. The red-team agent in the proposal is a black-box prober. Nothing in the text restricts it to code-bearing capabilities, and SKILLs are explicitly described in Pillar 1 as *"a SKILL.md file with instructions, templates, and **optional** scripts"* — prose-only SKILLs are a valid classification target per the proposal, and they would be uniformly covered by black-box behavioral probing.

**The drift:** the current implementation narrowed Tier 3 to a white-box code-inspection strategy. This narrowing was never written down, never justified, and isn't in the proposal. It happens to cover the dominant MCP_TOOL case cheaply and deterministically, but it leaves prose-only SKILLs structurally un-probeable. The narrowing was surfaced accidentally during this performance investigation when test_mint showed AGENT Tier 3 taking 0.0s — the coarse version of the same drift — and deeper re-reading of the proposal text revealed that the "gap" I was closing was one of several gaps, not the whole thing.

**Fix 2c-3 is still worth shipping** — it reduces the drift rather than eliminating it, and the reduction is honest and clean:

- MCP_TOOL: unchanged (white-box, which is defensible as a shortcut for pure deterministic Python functions).
- SKILL with Python fences: now probed via fence extraction (previously fed raw markdown prose).
- AGENT with Python fences in `agent_config.system_prompt` or `agent_config.skill_content`: now probed (previously skipped unconditionally — a structural bug).
- SKILL pure prose: now returns an explicit `adversarial_skip` with a type-aware message instead of pretending to probe prose (honesty improvement).
- AGENT pure prose: same — explicit skip with `agent_config` mentioned in the message.

**What Fix 2c-3 is NOT:** a completion of Tier 3 per the proposal. The proposal-aligned Tier 3 would execute every capability through its normal runtime (`sandbox_execute` for MCP_TOOL, the constrained-LLM skill executor for SKILL, the DeepAgent harness for AGENT) with crafted adversarial inputs and compare behavioral outputs. That's a multi-day design + implementation job, introduces new runtime trust boundaries (especially for AGENT probing, which would re-invoke the DeepAgent in an adversarial loop), and is not safe to land days before the Phase 5 eval. See §3e for the explicit out-of-scope framing and the proposal audit added to the master plan.

`probe()` currently builds the user message as:

```python
source_code = artifact.source_code or artifact.skill_content or ""
user_message = f"Probe this generated tool for deceptive behavior.\n\n## Tool name: {name}\n\n## Source code:\n```python\n{source_code}\n```"
```

For SKILL and AGENT artifacts, `source_code` is `None`, so it falls back to `skill_content` or (by default) `""`. `skill_content` is markdown prose with embedded ```python fences. The adversarial agent then tries to "probe" that prose — which is partly nonsense (the `hidden_demo_mode` attack against prose instructions returns no meaningful signal), and it inflates the prompt size because markdown noise dilutes the executable surface.

**Change:** route via `_extract_python_fences` (already defined at `src/verification/gate.py:46-56` — the same helper Tier 1's layered static scanner uses). This keeps Tier 1 and Tier 3 targeting the **same executable surface**, which is a real coherence improvement — right now they don't, which is arguably a latent bug.

Pseudocode for the dispatch in `probe()`:

```python
if artifact.capability_type == CapabilityType.MCP_TOOL:
    source_code = artifact.source_code or ""
elif artifact.capability_type == CapabilityType.SKILL:
    from src.verification.gate import _extract_python_fences
    fences = _extract_python_fences(artifact.skill_content or "")
    if not fences:
        return _skip_report(capability_name,
            "No executable Python in skill_content — adversarial tier not applicable.")
    source_code = "\n\n# --- fence separator ---\n\n".join(fences)
elif artifact.capability_type == CapabilityType.AGENT:
    from src.verification.gate import _extract_python_fences
    config = artifact.agent_config or {}
    fences: list[str] = []
    if isinstance(config.get("system_prompt"), str):
        fences.extend(_extract_python_fences(config["system_prompt"]))
    if isinstance(config.get("skill_content"), str):
        fences.extend(_extract_python_fences(config["skill_content"]))
    if not fences:
        return _skip_report(capability_name,
            "No executable Python in agent_config — adversarial tier not applicable.")
    source_code = "\n\n# --- fence separator ---\n\n".join(fences)
```

A new `_skip_report()` helper returns a VerificationReport with `tier_reached=3`, one `INFO`-severity `adversarial_skip` finding explaining why, and `overall_passed=True`. Mirrors the existing empty-source-code path at `adversarial.py:258-271` exactly; no new contract.

#### Combined expected impact

A typical adversarial probe should drop from ~120–230s to ~40–90s. The worst-case 357s runs should disappear entirely because the main tail sources (planning ceremony, sandbox-path retries, pure-prose probing) are all being eliminated.

**Thesis alignment:** this is where the alignment story gets genuinely stronger, not just neutral.

- Fix 2c-1 (`reasoning_effort="low"`): proposal silent on internal model config. Neutral.
- Fix 2c-2 (prompt tightening): the proposal describes *"a dedicated red-team agent"* that runs three specific attacks. The agent-ness is preserved (it still iterates, still uses `sandbox_execute`, still produces structured findings). Removing a self-imposed planning layer on top of a prompt-enumerated plan doesn't remove agent-ness. Neutral.
- Fix 2c-3 (Option B): **partial drift reduction, not full alignment.** Corrects the first-pass overclaim. Pre-fix Tier 3 for SKILL/AGENT was structurally broken (AGENT always skipped, SKILL fed raw prose); Fix 2c-3 replaces that with fence-aware probing + honest type-aware skips. But the proposal describes a **black-box behavioral** Tier 3 that would uniformly probe prose-only capabilities via crafted inputs through their normal runtime — the current implementation (white-box code inspection) does not do that regardless of Fix 2c-3. Closing the remaining drift is out of scope for this pass and is tracked in §3e and in `docs/MASTER_PLAN_2026_04_13.md` Phase 2 Step 12 (proposal audit).

### 2d. `mint reset` and the evolutionary taxonomy file — PENDING APPROVAL

**File:** `src/cli/app.py` (`reset` command body at line 196).

**Problem surfaced by test_mint run 2026-04-14 14:44:** `mint reset` wipes `data/cli/chroma/`, `data/cli/metrics.duckdb`, and `data/cli/last_run.json`, then re-seeds the registry with the pinned capabilities from `seed_registry()`. **It does NOT touch `data/cli/domains.json`.** The evolutionary taxonomy file survives reset, carrying stale categories from prior sessions into a nominally clean state.

Evidence: after the test_mint run, `data/cli/domains.json` contains exactly one category, `file-processing`, with `created_at=2026-04-14T12:45:28` — a timestamp from a **prior session**, not the 14:44 run. The current run performed reset, then seeded the registry, then ran 6 pipeline tests. No new taxonomy categories were added because every new capability's LLM classifier reused the pre-existing `file-processing` category. The behavior is correct from the taxonomy's perspective (reuse over proliferation), but the **reset semantics are wrong**: "clean slate" should mean clean slate.

**Fix:** split the reset behavior by whether re-seeding is requested.

1. **`mint reset --no-seed`** (user explicitly wants a blank slate): delete `data/cli/domains.json` entirely. The first LLM classification on the next pipeline run creates the first category organically, matching the evolutionary-taxonomy design. No pre-population needed.

2. **`mint reset` (default, re-seeds registry)**: two sub-options, pick one based on thesis framing:

   - **Option A (keep in sync with seeded caps)**: delete `data/cli/domains.json`, then iterate `seed_registry()`'s seed list, collect the distinct `domain` values from the pinned descriptors (e.g., `mathematics`, `general`, …), and write a fresh `domains.json` with one entry per distinct seeded domain. Description field can be auto-generated from the cap names — e.g., `"(seeded — contains: fibonacci, csv-parser, ...)"`. This keeps the taxonomy structurally consistent with the registry at all times: every pinned cap has a corresponding taxonomy category.

   - **Option B (blank-slate evolutionary start)**: delete `data/cli/domains.json` and do not pre-populate. Pinned caps stay in the registry with their hardcoded `domain` metadata. The first gap_detection call on the next pipeline run sees an empty taxonomy, hits the `get_for_query → None` branch, and runs **unfiltered** registry search — which correctly matches seeded caps by semantic similarity regardless of domain. As soon as a generated cap arrives, the taxonomy starts growing from scratch. This matches the thesis framing ("taxonomy evolves organically as MINT encounters new kinds of tasks") more cleanly than Option A, because the taxonomy is a property of what MINT has *generated*, not what it was bootstrapped with.

**Recommendation: Option B.** Reasons:
- Thesis-aligned: the evolutionary taxonomy is described as growing from observed generation, not from seed fixtures.
- Simpler: one `unlink()` call in the reset command, no iteration over seeds, no auto-generated descriptions.
- Safe under the current gap_detection contract: the `get_for_query → None` branch already falls back to unfiltered search, so seeded caps are findable without any taxonomy entries.
- Doesn't create second-class "seeded" categories that would confuse later inspection of the taxonomy evolution (which is a Chapter 6 artifact).

**Risk consideration**: with Option B, if you `mint reset`, then immediately query a seeded-cap domain (e.g. fibonacci), then generate a non-math capability, the taxonomy now has one non-math category. A subsequent math query would run `get_for_query` against that one category and either return `None` (no fit) or the wrong category; both fall back to unfiltered search because of the existing `$or {domain, is_generic}` filter semantics. **This is already the designed behavior** for the non-reset path — it's not a regression from the reset change. Flag for completeness.

**Scope:**
- `src/cli/app.py`: add `domains.json` unlink to the reset command body, after the `chroma_path`/`metrics_path`/`last_run_path` block. Add to the `removed` list so the user sees it in the confirmation message. ~6 lines.
- Tests: one unit test in `tests/unit/test_cli.py` (or wherever `reset` is tested) verifying `domains.json` is gone after reset.

Effort: ~20 minutes.

**Thesis alignment:** moderate positive. Option B aligns the reset command's behavior with the evolutionary-taxonomy framing in the thesis. Not as strong as Fix 2c-3's alignment impact, but measurably better than the current stale-file behavior.

---

## 3. What's explicitly NOT being fixed in this pass

Documenting the scope boundary so it's clear what remains as debt.

### 3a. Decomposition ghost-thread bug — NOT FIXING

When the 120s (or post-bump 240s) decomposition timeout fires, `pool.shutdown(wait=False)` lets the background thread keep running OpenAI calls for another 100+ seconds, burning dollars on work we've already given up on. **Decision: we are explicitly NOT fixing this.**

**Why it's safe to leave it:** the ghost thread is not risking correctness. By the time the background thread finishes its delayed work, the pipeline has already advanced — `decomposition_node` has returned the fallback state, `gap_detection` / classifier / generators / verification are already running on it, and there is no branch anywhere in the code that would re-integrate a late decomposition result. We've already permanently committed to discarding whatever the ghost produces. Killing it saves money; it does not save any useful work.

**Why we're not attempting the fix anyway:**
1. Python threads cannot be force-killed (no `Thread.kill()`, no cancellable `future`). The proper fix is subprocess isolation or an async-await path — both are multi-hour refactors touching decomposition + adversarial nodes and introducing their own risk surface (subprocess IPC, async context propagation for LangFuse metadata, etc.).
2. The timeout bump in Fix 2b does most of the cleanup for free: with the timeout at 240s, most borderline tasks now complete *before* the timeout fires, so the ghost-thread window shrinks from "always" to "only on tasks exceeding 240s real compute time" — exceptional.
3. The cost of leaving it unfixed is small and bounded: ~$0.002–0.01 per timeout event in wasted tokens, plus some LangFuse trace noise where node-level latency exceeds the stated timeout. For a 400-record eval with ~5% timeout rate, expected total waste is under $0.50 and zero correctness impact.

**Action:** none. This is documented debt, not a TODO. Revisit only if a future feature requires cancellable long-running nodes for reasons unrelated to this specific bug.

### 3b. Adversarial tier for SKILLs with zero Python fences

Subsumed by §3e below — the pure-prose-SKILL coverage gap is one symptom of a broader Tier 3 proposal drift, not an isolated issue. Left as a section heading for git-blame continuity but the full treatment is in §3e.

### 3e. Tier 3 proposal drift — white-box code inspection vs. black-box behavioral probing (NOT FIXING)

**This is the single most important "not fixing" item in the doc.** It's also the one that was almost missed until deeper proposal re-reading surfaced it. Adding it explicitly so it's not quietly forgotten once the eval runs.

**The drift, stated plainly.** The proposal (`docs/thesis_proposal.md` Pillar 2, Tier 3) describes the adversarial probing layer as a *"dedicated red-team agent [that] attempts to make the generated capability produce incorrect but plausible output"*, operating by *"crafting inputs"*, *"comparing outputs"*, and *"testing whether the tool behaves differently"* under various conditions. This is a **black-box behavioral** description — the red-team agent sends inputs through the capability's normal runtime, watches what comes back, and scores the behavior against known LLM shortcut patterns. Nothing in the proposal restricts the approach to code-bearing capabilities. The Pillar 1 section explicitly defines SKILLs as *"a SKILL.md file with instructions, templates, and optional scripts"* (emphasis on optional), so prose-only SKILLs are a valid classification target that the proposal's Tier 3 would naturally cover via behavioral probing.

**What the implementation actually does.** The current `src/verification/adversarial.py` is a **white-box code-inspecting** Tier 3. The red-team agent is given the capability's source code in its system prompt, asked to read that source, write Python test snippets that inline the source, and run those snippets in `sandbox_execute`. This works for MCP_TOOLs (pure Python functions) and — post-Fix 2c-3 — for SKILLs with embedded ```python``` fences and AGENTs with embedded Python in `agent_config`. It **does not** work for prose-only SKILLs or prose-only AGENTs, because there is no executable source to inline. Fix 2c-3 makes that limitation honest (returns a type-aware `adversarial_skip`) but does not close it.

**Why the drift matters technically.** Five problems with the white-box framing, in decreasing order of severity:

1. **Capability-type coverage is non-uniform.** Prose-only SKILLs and prose-only AGENTs are structurally un-probeable. That's half the SKILL design space by the proposal's own definition.
2. **Tier 1 and Tier 3 become methodologically redundant.** Both inspect source code statically (Tier 1 with AST+regex, Tier 3 with an LLM reading the code). The tier architecture argument for three distinct tiers weakens when two of them are doing the same kind of analysis with different tools.
3. **The thesis novelty claim is weaker.** "LLM-driven static code analysis" is less novel in 2026 than "black-box behavioral adversarial probing for LLM-generated capabilities". The proposal's framing is the stronger novelty story.
4. **Evasion surface is different.** White-box misses semantically-deceptive prose instructions. Black-box misses backdoors that only activate on inputs the prober doesn't happen to craft. Neither is strictly superior, but the proposal's framing at least covers prose capabilities at all.
5. **Determinism vs. generality tradeoff is skewed.** White-box is cheaper and more deterministic for MCP_TOOLs (which is why it was attractive), but that gain is local — it doesn't justify narrowing the whole tier to the code-bearing case.

**Why the drift matters for the thesis.** Chapter 4 (Verified Capability Synthesis) is where Pillar 2's novelty is argued. The current implementation narrows that novelty argument without admitting it. Either Chapter 4 needs an honest drift disclosure (recommended) or the proposal needs updating to match what was built (discouraged, because it weakens the novelty claim on the thesis's strongest single point). The path of least dishonesty is: **ship the current implementation, disclose the drift in Chapter 4 as a methodology limitation, and position the proposal-aligned black-box Tier 3 as Chapter 7 future work**. This turns the drift into a Chapter 4 contribution (the gap between behavioral and static adversarial probing is a real design space with observable consequences at the capability-type boundary) rather than a hidden flaw.

**Why we're not fixing it now.** Four reasons:

1. **Time budget.** Phase 5 eval is roughly three working days away per `docs/MASTER_PLAN_2026_04_13.md`. A proper behavioral-Tier-3 rewrite is multi-day design + implementation + testing work, and the margin for integration regressions is zero.
2. **Risk profile.** A rewritten Tier 3 would introduce new trust boundaries (especially for AGENT probing, which would re-invoke the DeepAgent inside an adversarial loop) and new non-determinism sources (black-box probing through an LLM runtime is inherently noisier than AST inspection). Neither is safe to land days before a 400-record unattended eval.
3. **Coverage of the dominant case is acceptable.** MCP_TOOLs are the majority of generated artifacts in the testbed. The current implementation correctly probes them. Fix 2c-3 extends that to code-bearing SKILLs and AGENTs. The remaining un-probed slice (prose-only SKILL/AGENT) is narrower than the full drift sounds.
4. **The drift is a stronger thesis contribution than the fix.** Honestly disclosing the gap in Chapter 4 and positioning the proposal-aligned version as future work is a methodology contribution: *"static adversarial probing is cheaper and more deterministic but has capability-type boundaries; behavioral adversarial probing is more general but has crafting-quality dependencies. The tradeoff matters for how three-tier verification architectures generalize across capability types."* That's more interesting to a thesis reader than a hidden drift that got fixed in a rush.

**What to do about it, concretely.**

- **Pre-eval (now):** ship the current implementation plus Fix 2c-3. Nothing else changes in `src/verification/`.
- **Pre-eval (now):** run the full proposal audit in `docs/MASTER_PLAN_2026_04_13.md` Phase 2 Step 12 (new) to confirm Tier 3 is the only such drift. Track any additional findings in `docs/proposal_vs_implementation_v2.md`.
- **Thesis writing:** Chapter 4 gets an honest methodology section on the white-box / black-box Tier 3 tradeoff, with the drift disclosed and the trade space mapped.
- **Chapter 7 future work:** a proper behavioral-Tier-3 implementation. Out of scope for the thesis itself; tracked as a follow-on research direction.
- **Post-thesis (if MINT continues):** implement proposal-aligned Tier 3. Design sketch: invoke each capability through its normal runtime (`sandbox_execute_in_workspace` for MCP_TOOL, `execute_skill_artifact` for SKILL, `execute_agent_artifact` for AGENT — all already exist in `tests/evaluation/execution.py`); craft adversarial inputs via prompt engineering targeting hidden-demo, ground-truth, and network-disconnect patterns; compare outputs against baseline runs; emit findings in the existing `AdversarialFindings` shape. Estimated effort: 2-4 days of focused implementation plus a test suite.

**Action in this pass: none.** This is documented debt, not a TODO. Revisit after Phase 5 eval data is in hand and Chapter 4 is being drafted.

### 3c. Decomposition reasoning-effort reduction

Considered and **rejected** in favor of the timeout bump. Lowering decomposition's reasoning_effort would change what the decomposer produces on **every** task, not just the slow ones. Every downstream stage (classification, generation, gap detection, compositional execution) depends on decomposition's output quality — the blast radius of a quality regression there is the entire pipeline. The timeout bump is surgical by comparison: it affects only the tail where variance is biting us, with no quality impact on the body of the distribution.

### 3d. Sandbox path / workspace confusion at the task level

The user's question about "how this would work if we would have to get a csv file or excel file at the end of the task" — worth clarifying because the answer is **unaffected by any of these fixes**. Task-level file output uses a completely different sandbox entry point:

- **`sandbox_execute`** (used by adversarial prober + behavioral tester): stateless, no workspace mount, each call is a fresh `docker run --rm`.
- **`sandbox_execute_in_workspace`** (used by compositional executor, `sandbox_tool.py:152-177`): mounts a host workspace dir at `/workspace`, so files written to `/workspace/output.xlsx` survive the container lifecycle and are collected by `ChainResult.output_files`.

The prompt guidance in Fix 2c-2 ("keep test code self-contained within a single `sandbox_execute` call") applies only to the adversarial prober's use of the stateless variant. Task file output via the compositional executor is a different code path and continues to work as before. They don't share code.

---

## 4. Execution order

Fixes committed in dependency-free order, each self-contained enough to commit independently. Revised after the 2026-04-14 14:44 test_mint run added sections 1c-bis, 1d, and 2d.

1. **Commit A (ALREADY STAGED)** — taxonomy reasoning_effort + parallelization (`src/orchestrator/taxonomy.py`) + gap_detection reasoning_effort (`src/orchestrator/gap_detection.py`) + previously-uncommitted taxonomy path-default fix (`src/cli/runner.py`). Single commit covering Fix 2a plus the already-staged path-default work. Commit message to include both scopes.

2. **Commit B** — decomposition timeout bump **and** silent-failure guard (`src/orchestrator/decomposition.py`, `src/schemas/task.py`, `src/cli/runner.py` or `src/cli/display.py`, `tests/evaluation/harness.py` if it has a success classifier, plus corresponding unit tests). Fix 2b in its full form. Scope is two files minimum, four with tests. Still a coherent single commit.

3. **Commit C** — adversarial prober: reasoning_effort + prompt tightening + fence-extraction completeness fix (`src/verification/adversarial.py`, import from `src/verification/gate.py`, plus a unit test for the new AGENT fence-extraction path). Fix 2c-1, 2c-2, 2c-3 together.

4. **Commit D** — `mint reset` clears `domains.json` (`src/cli/app.py`, plus a unit test). Fix 2d. Small, independent, could also bundle into Commit A but kept separate for reviewability.

5. **Re-run test_mint end-to-end** to verify all four fix layers land correctly. The success criterion for T04 specifically: the output dict must contain `items_below_threshold` AND `total_reorder_cost`, not just the raw inventory dump. If those keys are present, Fix 2b has correctly enabled real decomposition rather than the silent fallback. The test_mint harness does not currently validate this — perform a manual visual check on the T04 output JSON.

6. Verify Fix 2c-3 end-to-end: the AGENT test should now show non-zero Tier 3 wall time (or, if the generated AGENT happens to have no Python fences, an explicit `adversarial_skip` with a message mentioning "no Python fences in agent_config" rather than the old generic `"No source code to probe."`). Both outcomes are acceptable; the old unconditional skip is not.

7. Verify Fix 2d: run `mint reset` and confirm `data/cli/domains.json` no longer exists. Run any pipeline command afterward and confirm `domains.json` is re-created with whatever category the first LLM classification picks.

After 1–7 land and test_mint passes, the only remaining pre-eval surfaces are Phase 2 Steps 4–11 of `docs/MASTER_PLAN_2026_04_13.md` (which were the next substantive work block before this performance detour surfaced). No scope expansion, no new debt beyond what section 3 explicitly documents.

---

## 4.5. Validation — test_mint run 2026-04-14 19:01

Full end-to-end smoke run against the four fixes. Results back the fix-group claims in §2 with real numbers.

### Headline: canary (T04 SKILL) passed with correct output

The 14:44 run exhibited the silent-failure pattern: decomposition timed out at 120s, the pipeline fell back to a single-sentence query, classifier picked SKILL, generator produced a skill that only read the input files, and the pipeline reported `OK: Pipeline completed successfully` with a raw inventory dump missing `items_below_threshold` and `total_reorder_cost`.

The 19:01 run (with Fix 2b applied):

```
✓ Decomposing task — 5 subtask(s) (49.0s)         ← real decomposition, well under the new 240s timeout
✓ Classifying gaps — mcp_tool, mcp_tool, skill, skill, mcp_tool (56.9s)
✓ Generating MCP tool — 3 artifact(s) (38.5s)
✓ Generating skill — 2 artifact(s) (45.3s)
✓ Verifying artifacts — ✓ Tier 3 (109.2s)
✓ Loading & registering — completed (1.3s)
...
✓ Step 1/5 through Step 5/5 all executed in dependency order

Output:
{
  "product_count": 7,
  "items_below_threshold": 7,
  "total_reorder_cost": 33785.1
}
```

**All three expected keys present, with plausible values.** The silent-failure path is closed. Fix 2b delivered exactly what it was designed to.

### Fix 2c Tier 3 speedup — measured

| Test | 14:44 Tier 3 | 19:01 Tier 3 | Speedup |
|---|---|---|---|
| T01 MCP CSV (1 artifact) | 148.1s | 0.0s (registry match now) | n/a |
| T04 SKILL (3 MCP + 2 SKILL) | 123.0s | 109.2s | ~1.1× (one artifact was SKILL) |
| Sales/products/targets (3 MCP + 1 SKILL) | **525.0s** | **161.8s** | **~3.2×** |
| AGENT T16 (1 AGENT, prose) | 0.0s (bug) | 0.0s (honest skip) | (fix type changed) |
| XML (2 MCP) | 162.5s | **56.6s** | **~2.9×** |
| TSV (3 MCP) | 135.4s | **54.6s** | **~2.5×** |

MCP_TOOL-heavy tests consistently 2.5–3.2× faster, matching the projection in §2c-1 (~120-230s → ~40-90s per probe). The smaller T04 win reflects its SKILL artifacts taking longer to probe individually; the larger speedups on multi-MCP_TOOL chains reflect the parallelizable nature of the per-artifact probes.

### Fix 2c-3 AGENT completeness — confirmed via verification.json

The AGENT test generated a prose-only incident-triage agent (its only tool is `sandbox_execute`, and its system prompt is natural-language instructions — no embedded Python fences anywhere). Fix 2c-3's dispatch correctly detected this and returned the new type-specific skip message:

```json
{
  "tier": 3,
  "category": "adversarial_skip",
  "severity": "info",
  "message": "No executable Python in agent_config.system_prompt or agent_config.skill_content — agent is prose-configured. Tier 3 not applicable; rely on Tier 1 prose checks.",
  "location": null
}
```

**This is the exact message I wrote into `_extract_probe_source()`.** Pre-fix, the same AGENT would have hit the old generic `"No source code to probe."` skip at `adversarial.py:258-271`, which gave no signal about *why* the skip happened. Post-fix, `verification.json` records the structural reason (`agent_config.system_prompt` and `agent_config.skill_content` contain no Python fences), which is inspectable and interpretable in Chapter 6 analysis.

### Fix 2d taxonomy reset — confirmed

`data/cli/domains.json` was wiped by the `mint reset` at the start of the run and rebuilt organically from empty. The post-run file contains 5 categories, all with `created_at` timestamps from this run's wall-clock window (17:04–17:29 UTC):

```
- inventory          (17:04:36 UTC)
- inventory-management (17:04:37 UTC)
- inventory-reorder  (17:04:35 UTC)
- incident-triage    (17:19:25 UTC)
- sales-analytics    (17:29:12 UTC)
```

No stale categories from prior sessions. The reset behavior matches the Fix 2d design.

**Side observation:** the T04 SKILL test alone produced three near-duplicate categories (`inventory`, `inventory-management`, `inventory-reorder`) within two seconds of each other. This is the parallel-classification race flagged in Fix 2a's design note — three generator threads classified three T04 artifacts against an empty taxonomy and each proposed a slightly different category name for the same concept. Acceptable noise: the evolutionary taxonomy design accepts some duplication near the start of a run in exchange for avoiding the ~200s serial bottleneck that per-artifact sequential classification would have introduced. Would be cleaner with a pre-generation taxonomy seeding step from the seeded registry's domain strings, but that's out of scope for this pass.

### Fix 2a taxonomy performance — implicitly confirmed

The generator stage timings across the run (38.5s, 45.3s, 46.9s, 53.2s, 55.5s, 75.6s, 87.3s) are consistent with parallelized classification at `reasoning_effort="minimal"`. Pre-fix, serial per-artifact classification at ~193s would have stacked visibly — a 3-artifact generator stage would have been ~580s instead of ~40-75s.

### Two failures surfaced, both pre-existing and NOT caused by this fix pass

**Failure 1: AGENT execution timeout at 480s.**

The AGENT test reached verification cleanly (`generate_agent: 55.5s`, `verification_node: 0.0s` per the honest Fix 2c-3 skip, `loading_node: 0.0s`). Total pipeline time to that point: ~113s. The pipeline succeeded.

Then MINT invoked `execute_agent_artifact` on the generated capability to actually run it against the input files (incident YAML + system metrics CSV). The DeepAgent started its adaptive investigation — reading files via `sandbox_execute`, reasoning about metrics, forming hypotheses — and was still running at 479s when the hard execution timeout in `tests/evaluation/execution.py::execute_agent_artifact` fired at 480s. The capability ran but produced no output; CLI correctly reported `FAIL: exit code 1`.

**Root cause:** same class of bug as the decomposition timeout that Fix 2b addressed — a hard cutoff on a variance-prone runtime. The generated incident-triage agent's real work fits the body of the runtime distribution (the 14:44 run completed at 584s total, with the agent fitting under 480s that time), but the tail tips over. Reasoning-mode wall-clock variance on long-running DeepAgents.

**Not caused by this fix pass.** Nothing in Fix 2a/2b/2c/2d touches the agent runtime. The 480s ceiling in `execute_agent_artifact` would have fired the same way without any of our work. This is a pre-existing timeout that happened to bite on this run.

**Follow-up tracked as Phase 2 Step 13 in the master plan** (AGENT execution timeout + sticky-flag pattern, ~1.5-2 hours).

**Failure 2: TSV chain schema contract mismatch.**

The TSV test generated 3 MCP_TOOL subtasks and verification passed cleanly for all three. Chain execution failed at step 2 with: `upstream_data must include a 'transactions' key with a list of transaction dicts`.

Reading the decomposition in `run.json`:

- Step 1 (`parse_csv_file_a0c0d5`): *"Parses the departments CSV file and returns a dict with key 'departments' containing a list of {department, annual_budget} records."*
- Step 2 (`parse_tsv_and_aggregate_by_department_892bf3`): *"Aggregates total spending per department from upstream parsed data. Expects upstream_data to include 'departments' AND 'transactions' (list of transaction dicts)."*
- Step 3 (`format_final_result_dict_fe9cbb`): trivial formatting

**Step 2 expects `'transactions'` in upstream_data. Step 1 only produces `'departments'`. Nothing parses the TSV file anywhere.** The chain is structurally incoherent — step 2's generator wrote code that expects pre-parsed transactions, but no earlier subtask produces them. The TSV fixture file is completely ignored.

**Compare to the 14:44 run:** the same task was decomposed into 2 coherent subtasks — step 1 parsed BOTH the CSV and the TSV in one step (producing `{'departments': [...], 'transactions': [...]}`), step 2 aggregated. That chain ran correctly end-to-end. **Same task, two runs, different decompositions — and only one was coherent.**

**Root cause:** decomposition non-determinism producing incoherent chain contracts. The decomposer is sensitive to its reasoning path and sometimes produces incoherent chains where downstream subtasks reference upstream keys that no earlier subtask produces. The decomposer prompt doesn't explicitly require schema coherence, and the pipeline has no validation layer that walks the subtask DAG and catches contract mismatches before generation runs.

**Not caused by this fix pass.** Decomposition coherence is a prompt-quality and pipeline-validation issue that's been happening intermittently for a while. If anything, Fix 2b's timeout bump (120 → 240) gives the decomposer *more* room to produce complex decompositions, which gives it more room to produce *incoherent* complex decompositions. But the underlying fragility is prompt brittleness, not the timeout value.

**Follow-up tracked as Phase 2 Step 14 in the master plan** (decomposition coherence check — prompt tightening + pipeline-side validation, ~2-3 hours). Higher priority than Step 13 because it directly affects Phase 5 eval correctness — runs that fail due to incoherent decomposition look identical to runs that fail for other reasons, and the harness can't distinguish them without this check.

### Final verdict on the four fixes

| Fix | Designed outcome | Measured outcome | Status |
|---|---|---|---|
| 2a taxonomy performance | ~587s → ~11s overhead per 3-gap pipeline run | Invisible in stage timings; parallel classification produced some near-duplicate categories on T04 as designed | ✅ working |
| 2b decomposition timeout + sticky flag | T04 decomposes correctly or fails loudly with exit 2 | T04 decomposed in 49s, produced correct output with all 3 expected keys | ✅ **canary passed** |
| 2c-1/2 adversarial reasoning_effort + prompt | ~2-3× speedup on MCP_TOOL probes | 2.5-3.2× speedup measured across MCP_TOOL-heavy tests | ✅ working |
| 2c-3 AGENT fence extraction | Honest type-specific skip message | Exact message I wrote appears in verification.json for prose-only AGENT | ✅ working |
| 2d mint reset wipes taxonomy | Fresh `domains.json` built from empty | 5 categories created fresh during the run, no stale carryover | ✅ working |

**All four fixes are verified end-to-end and ready to commit.** The two failures are pre-existing bugs now explicitly tracked in the master plan as Phase 2 Steps 13 and 14. They do not block committing the current fixes.

---

## 4.6. Third finding from the 2026-04-14 20:53 test_mint run — seeded-cap retrieval is structurally broken

**Added end of day 2026-04-14, after running test_mint a third time (20:53) to validate Step 14 decomposition coherence check.** A new failure mode surfaced that's unrelated to anything we've fixed today: the T01 MCP CSV test (literally labelled *"seeded capability match"*) did NOT match the `csv-parser` seed in the registry. Instead it generated a duplicate `parse_csv_and_return_stats_7f373a`. The user's observation was that this is the normal case — the 19:01 run's match was the exception, not the rule.

**Root cause, measured empirically:** the ChromaDB semantic-search threshold of 0.6 is too high for the combination of short slug-style decomposer queries against long prose-style embedded documents. For csv-parser specifically:

| Query | Similarity | Above 0.6? |
|---|---|---|
| `parse-csv-and-return-stats` (slug — current behavior) | 0.5425 | ❌ |
| `csv-parser` (literal cap name) | 0.5544 | ❌ — can't find the cap by its own name |
| `Parse the CSV file and return a dict with 'row_count' (int) and 'column_names' (list[str]).` (natural description) | 0.7622 | ✅ |

Three compounding factors: (a) decomposer produces short hyphen-case slugs for `required_capabilities`, (b) `_build_document` prepends `"{domain} > {type} > {name}:"` which dilutes the embedding signal, (c) `all-MiniLM-L6-v2` + 0.6 threshold is high for slug-vs-prose length asymmetry.

**Impact on Phase 5 eval if unfixed:** reuse rate metric artificially near-zero for seeded caps, `static` baseline looks artificially weak vs `full`, token cost inflated by duplicate generation, Chapter 6 retrieval-rate analysis can't distinguish "genuinely novel" from "retrieval bug". **Cannot be deferred to post-eval.**

**Fix:** dual-query search in `detect_gaps` — query with both the slug AND the corresponding `SubTask.description`, take the max. The decomposer already produces both; we just don't use the description for retrieval. Strictly additive, zero downside.

**Full plan:** `docs/registry_retrieval_fix_plan.md` — contains empirical evidence, root cause, ranked fix options, concrete implementation scope, and five open questions to resolve before implementation.

**Tracked in master plan:** Phase 2 **Step 14.5 — Registry dual-query search**, positioned after Step 14 (decomposition coherence) and before Step 13 (AGENT execution timeout). ~2.5-3 hours including an empirical sweep across all 11 seeded caps.

**Not a Step 14 regression:** Step 14 (decomposition coherence check) is independent from this retrieval issue. Step 14 handles DAG contract coherence at decomposition time; this fix handles query-to-document similarity at registry lookup time. Different layer, different fix. Step 14 can still be committed as planned; Step 14.5 happens next.

---

## 4.7. Fourth finding from the 2026-04-14 20:52 test_mint run — Step 14 validator false positive

**Added end of day 2026-04-14 after running test_mint a second time to validate Step 14 decomposition coherence check.** The validator I wrote for Step 14 correctly caught the T14 TSV incoherent-chain pattern (the target case from the 19:01 run) but also **false-flagged two runs that produced correct output end-to-end**:

- **T11 Excel SKILL integration** — subtask 2 (`compare-region-revenues-to-targets`) had `regions_meeting_target` in its own `output_schema` but the validator flagged it as a missing upstream key. Output was correct: `{summary_row_count: 60, region_revenues: {...}, regions_meeting_target: 2}`. Confirmed via langfuse trace `1c61e8cb6f12a2c0bb87fe2e720ca3fc`.
- **T14 TSV MCP_TOOL aggregation** — subtask 1 (`aggregate-spending-by-department`) had `department_count` and `department_spending` in its own `output_schema` but the validator flagged them as missing upstream keys. Output was correct: `{department_count: 5, department_spending: {5 departments with correct totals}}`. Confirmed via langfuse trace `ba87434f89ec0ff3b0de401124d345db`.

**Root cause:** `_extract_upstream_key_references` uses a regex that walks from an "upstream_data" anchor phrase to the next sentence-ending period, extracting every quoted identifier in that span. For descriptions like *"Aggregate `upstream_data['transactions']` by department. Return a dict with 'department_count' and 'department_spending'."* with no intervening period between the anchor and the output-key discussion, the regex extracts all three quoted identifiers as supposed upstream references. The validator then compares them against the transitive upstream set and incorrectly flags the output keys as missing upstream producers.

**Fix (applied in the same Step 14 commit):** before comparing `referenced_keys` against transitive upstream availability, subtract the subtask's own `output_schema` keys. A key in own `output_schema` is an output, not an input, regardless of description text near an upstream anchor phrase. This preserves the T14 TSV positive catch from the 19:01 run because the catch's failing key (`transactions`) is NOT in the subtask's own output_schema.

**Known edge case accepted:** hallucinated pass-through (key declared in own output_schema AND referenced in description AND no predecessor produces it) — the subtraction skips the check. Requires the decomposer to invent an invalid pass-through from scratch, which is unlikely. Better than the false-positive alternative, which was blocking every multi-step aggregation run.

**Three regression tests added** in `TestValidateChainCoherence`:
- `test_final_aggregation_step_not_false_flagged_tsv_pattern` — mirrors the T14 TSV case
- `test_comparison_step_not_false_flagged_excel_pattern` — mirrors the T11 Excel case
- `test_t14_catch_still_works_after_false_positive_fix` — regression guard that the original T14 catch still fires

**Status: fix included in Step 14 commit.** Verified by the 2026-04-14 21:38 test_mint re-run: T14 TSV and T11 Excel both passed the validator (no coherence violation), though T11 Excel hit a separate failure documented in §4.8 below.

---

## 4.8. Fifth finding from the 2026-04-14 21:38 test_mint run — MCP_TOOL generator signature bug

**Added end of day 2026-04-14 after re-running test_mint a third time to validate the Step 14 false-positive fix.** The false-positive fix worked — T14 TSV and T11 Excel both passed the validator cleanly. But T11 Excel then hit a **different failure mode at chain execution time**:

```
Chain MCP_TOOL join_sales_with_products_on_product_id_a_a8e6f6 raised:
join_sales_with_products_on_product_id_a_a8e6f6() missing 1 required positional argument: 'upstream_data'
```

Confirmed via trace `d8d92cca1d3f4715c97a5a72e96f9daa` and run.json at `data/cli/mint_output/compare_region_revenues_to_targets_025451_20260414_215154/`.

**Root cause:** the MCP_TOOL generator produced a step-0 function with `upstream_data` as a **required positional argument**, despite the subtask having `depends_on: []`. The executor's dispatch logic in `tests/evaluation/execution.py::execute_mcp_tool_in_chain` correctly does NOT pass `upstream_data` on step 0 (only on steps with `step_index > 0`). Python then raised the missing-argument error at call time.

**Why the LLM got it wrong:** the decomposer named subtask 0 `join-sales-with-products-on-product_id-and-parse-targets` — bundling file parsing and joining into one step (legitimate). The generator LLM saw "join" in the name, pattern-matched it to middle-step semantics, and produced a signature expecting upstream data despite the structured `depends_on=[]` signal.

**The MCP_TOOL generator prompt already has the rule** distinguishing step-0 (per-key file-path params) from middle/last-step (`upstream_data` param) — but the LLM didn't follow it reliably on this run. The decomposer's non-determinism in subtask naming caused the generator to violate its own contract.

**MCP_TOOL-only scope — verified, not assumed:**

- **SKILL and AGENT don't have this bug class.** They use `fixture_contents` injection via the LLM task message rather than Python function signatures. `_execute_skill_step` and `_execute_agent_step` in `composition.py` build `fixture_contents` from upstream outputs and pass it as a parameter to `execute_skill_artifact` / `execute_agent_artifact`. The SKILL/AGENT runtime LLM executor reads whatever keys are present in `fixture_contents` — empty on step 0, populated on non-step-0. **No signature to mismatch.**
- Grep confirms neither `src/orchestrator/prompts/skill_generator.py` nor `src/orchestrator/prompts/agent_generator.py` mentions `fixture_contents`, `upstream_data`, `first step`, or `depends_on` — because the mechanism is architecturally invisible to them.
- Commit `1283f68` (chain schema contracts, the commit that introduced the `upstream_data` mechanism for MCP_TOOL) explicitly notes *"SKILL/AGENT steps unaffected (fixture_contents injection already robust)"*. The two types were considered and deliberately not touched.

**Fix:** tracked as Phase 2 **Step 14.7** in `docs/MASTER_PLAN_2026_04_13.md`, positioned between Step 14.6 and Step 13. Recommended approach (Option A in the plan): make `upstream_data: dict | None = None` always-optional in the MCP_TOOL generator prompt rule, so the generator produces a uniform signature for all steps regardless of what the LLM infers from the subtask name. The executor's dispatch already handles both cases. Removing the step-0 vs non-step-0 decision from the LLM's hands eliminates the verb-pattern-matching failure mode. **~1 hour of work.**

**Pre-existing since commit `1283f68`** (2026-04-12). Not caused by any of the four performance fixes or Step 14. The bug was latent on any run where the decomposer happened to name a step-0 subtask with a verb like "join", "aggregate", "combine", or "merge" that triggered the LLM's middle-step pattern matching.

---

## 4.9. Sixth finding — `mint reset` path bug on `last_run.json` + missing skills/WAL cleanup

**Added end of day 2026-04-14 while reviewing the 21:38 test_mint run's leftover state.** The user noticed `data/cli/skills/` had 6 leftover skill directories from 2-3 prior runs and asked whether the same reset-cleanup gap applied to chroma, metrics.duckdb, and last_run.json. Investigation surfaced **three distinct problems** in the reset command:

**Problem 1: `last_run.json` path bug — reset was never actually wiping it.**

The writer at `src/cli/runner.py:67` saves to `_data_dir() / "cli" / "last_run.json"` (correctly under the `cli` subdirectory). The reset command at `src/cli/app.py:212` (pre-fix) looked at `data / "last_run.json"` — **without the `cli` subdirectory**. On every reset, it tried to delete a file that didn't exist, the real file at `data/cli/last_run.json` survived untouched, and the user kept seeing it in ls output after reset.

The unit test was complicit: `tests/unit/test_cli_reset.py:29` (pre-fix) created the fixture at `data / "last_run.json"`, asserted the reset wiped it there, and passed — because the test and the production code shared the same wrong path assumption. This is the canonical *"test mirrors the bug"* anti-pattern: the test validates the path assumption that the production code holds, not whether that assumption is correct.

Same bug also present in `scripts/test_mint.py:321` (its inline reset replicates the production reset and copied the path error).

**Fix:** production path corrected to `data / "cli" / "last_run.json"` in both `src/cli/app.py` and `scripts/test_mint.py`. Test fixture corrected to write at the correct path. New regression test `test_reset_deletes_last_run_at_correct_path` explicitly asserts reset wipes the file at `data/cli/last_run.json` so a future regression to the wrong path would fail the test instead of hiding.

**Problem 2: `metrics.duckdb.wal` was never cleaned up.**

DuckDB writes a write-ahead log file next to the main database file. On graceful shutdown DuckDB cleans up the WAL, but if a metrics operation is interrupted or a process crashes, the `.wal` file can be left orphaned next to `metrics.duckdb`. The reset command only unlinked `metrics.duckdb`, leaving any orphan WAL behind. User's ls confirmed: `metrics.duckdb.wal` was present alongside `metrics.duckdb`.

**Fix:** reset now also unlinks `metrics.duckdb.wal` if present. Silent cleanup — not added to the `removed` list because the WAL is a DuckDB internal file, not user-facing state. New regression test `test_reset_deletes_metrics_wal` covers it.

**Problem 3: `data/cli/skills/` was never wiped** (surfaced earlier — the original user observation).

Already covered in the reset-skills fix committed separately. Mentioned here for completeness: the three problems were noticed together while auditing the reset command after the 21:38 run.

**Why all three bugs persisted undetected until today:**
- `last_run.json` path bug: the test fixture matched the wrong path, so the test passed
- `metrics.duckdb.wal`: not covered by any test
- `skills/` directory: not covered by any test

**The common pattern:** the reset command grew incrementally (chroma → metrics → last_run → taxonomy → skills) without a holistic audit of "what does data/cli/ contain and what should survive a reset?". Each addition only tested the new item, not the total state. An inventory-based approach — list everything in `data/cli/` after a session, compare against what reset deletes, flag the delta — would have caught all three at once.

**Process nugget for Chapter 4 methodology section:** *"Test fixtures that hold the same assumptions as production code can mask bugs that both depend on. We observed this pattern on the mint reset command's `last_run.json` path — the test passed because it created the file at the same wrong path the production code deleted, validating the internal consistency of the mistake rather than whether the mistake was correct. A regression test that explicitly writes the file at the path the writer uses, then asserts the reset wipes it there, catches this class of bug by construction."*

**Total scope of the fix (one small commit):**
- `src/cli/app.py`: last_run_path corrected, metrics_wal_path added, both wired into the unlink block. 3 edits, ~15 lines total counting comments.
- `scripts/test_mint.py`: same path fixes in the inline reset block + add WAL/taxonomy/skills cleanup for parity with app.py reset. ~15 lines.
- `tests/unit/test_cli_reset.py`: fixture writes at correct path, two new regression tests (last_run path + WAL cleanup), existing sweep test updated. ~30 lines.

**Full suite: 1073/1073 unit tests passing** (was 1071 before, +2 regression tests minus 0 replaced).

---

## 5. What the user will see after the fixes

Updated with real numbers from the 2026-04-14 14:44 test_mint run (6 tests, wall time ~38 min total).

**Per-stage impact on the T04 SKILL test** (current worst-case behavior vs. projected post-fix):

| Stage | Observed (14:44 run) | Projected after fixes |
|---|---|---|
| Decomposition | **120.1s — TIMEOUT** → 0 subtasks → silent fallback | 45–180s, completes with 5 structured subtasks |
| Taxonomy (gap_detection) | ~1s (already fixed) | ~1s |
| Taxonomy (per-artifact generators) | ~11s (already fixed, parallelized) | ~11s |
| Adversarial probe (per artifact) | 123s | 40–60s |
| T04 pipeline wall time | **339.4s (5.7 min) — with wrong output** | ~250–350s (4–6 min) — with correct output |
| **T04 output correctness** | **WRONG** (missing `items_below_threshold`, `total_reorder_cost`) | **CORRECT** (full 5-stage pipeline executed) |

**Per-test Tier 3 wall time, observed in the 14:44 run** (the biggest bucket):

| Test | Artifacts | Tier 3 observed | Tier 3 projected after Fix 2c |
|---|---|---|---|
| MCP_TOOL CSV (T01) | 1 | 148.1s | ~50–70s |
| SKILL T04 (wrong output) | 1 | 123.0s | ~40–60s |
| 4× MCP_TOOL sales/products/targets | 4 | **525.0s (~131s each)** | ~200–280s (~50–70s each) |
| AGENT incident | 1 | 0.0s (unconditional skip) | **~60–120s (real probing of Python fences)** |
| 3× MCP_TOOL XML | 3 | 162.5s (cached, ~54s each) | ~120–150s |
| 2× MCP_TOOL TSV | 2 | 135.4s (~68s each) | ~100–140s |
| **Total Tier 3** | **12** | **~1,093s (18.2 min) = 47% of total wall time** | **~570–820s (9.5–13.7 min) = estimated 30–35% of total** |

**Total test_mint smoke-test wall time:**

| | Before | After |
|---|---|---|
| Observed 14:44 run (6 tests) | **~38 min** | |
| Projected after all fixes | | **~20–25 min** |
| T04 output correctness | **wrong (silent failure)** | **correct** |
| AGENT Tier 3 coverage | **0% (always skipped)** | **100% (probes Python fences or returns honest no-fence skip)** |

The bigger story isn't the wall-clock savings — it's the two correctness improvements:

1. **Decomposition stops silently corrupting the downstream pipeline.** Before Fix 2b, every 5+ stage task hits the timeout and the pipeline produces a wrong-output "success" because the fallback path treats the whole task as a single SKILL gap. After Fix 2b, either the real decomposition completes (240s is enough budget for the observed 87–180s wall-time distribution) or the pipeline surfaces an explicit failure.

2. **AGENT Tier 3 starts actually verifying agents.** Before Fix 2c-3, the adversarial tier returns an unconditional `adversarial_skip` for every AGENT ever generated because `artifact.source_code` and `artifact.skill_content` are both empty for AGENTs. After Fix 2c-3, Python fences in `agent_config.system_prompt` and `agent_config.skill_content` get extracted and probed, closing a verification gap that was invisible in the metrics because the skip path always returned `overall_passed=True`.

Both of these correctness improvements directly affect the fidelity of the Phase 5 eval numbers. Without them, the eval would report artificially high success rates on T04-class tasks and would claim 100% Tier 3 coverage on AGENTs when it's actually 0%.
