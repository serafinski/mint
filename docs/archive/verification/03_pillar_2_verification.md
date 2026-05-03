# Pillar 2 — Verification Layer — Implementation Alignment Audit

**Scope:** `docs/thesis_proposal.md` §Pillar 2 lines 101–152 (Problem, three-tier approach, Verification as a Research Contribution).
**Audited directories:** `sandbox/`, `scripts/`, `src/`, `tools/`.
**Date:** 2026-04-18.
**Method:** File-by-file rundown of every source file in the four listed directories, mapped against the four failure modes the proposal names (hardcoded responses, silent fallbacks, mock contamination, graceful-looking degradation) and the three-tier gate (Tier 1 static analysis, Tier 2 behavioral testing, Tier 3 adversarial probing). Every item that extends beyond the proposal wording is flagged as IMPROVEMENT or DIVERGENCE and cross-linked to its master-plan disclosure.

---

## 1. What the proposal's Pillar 2 actually says

### Problem
LLM agents generating code optimize for *"making things work"* in the immediate context rather than producing correct, production-grade artifacts. Four common failure modes:

| Mode | What the proposal says |
|---|---|
| **F1. Hardcoded responses** | Tool returns plausible-looking data without actually querying any external source. |
| **F2. Silent fallbacks** | Tool wraps API calls in broad exception handlers that return synthetic data when the real call fails. |
| **F3. Mock contamination** | Test fixtures leak into production paths; tests pass while real usage fails silently. |
| **F4. Graceful-looking degradation** | Outputs of the right shape (correct column names, plausible value ranges) but with fabricated content. |

### Approach — three tiers, gated
Every generated capability **must pass all three tiers** before entering the production skill registry.

| Tier | Spec (verbatim-ish) |
|---|---|
| **Tier 1 — Static Analysis** | Zero-dependency scanner running regex + AST pattern detection. Table of categories: hardcoded data (Critical), silent exception handling (High), mock artifacts (Critical), synthetic data indicators (Medium), missing external calls (High). Severity-ranked report. *"Any Critical finding blocks deployment; High findings require Tier 2 clearance."* |
| **Tier 2 — Behavioral Testing** | Verifier agent generates test inputs: nonexistent-entity tests, consistency tests, boundary tests, latency analysis. Distinguishes real tools from mock-contaminated ones by behavior. |
| **Tier 3 — Adversarial Probing** | Red-team agent: crafts inputs to trigger LLM shortcut patterns (hidden demo mode), compares against known ground truth, tests network-disconnect behavior. |

### Verification as a Research Contribution
*"No published work on self-evolving agents (EvoMAC, EvoAgentX, STELLA, AgentEvolver, ACE) includes a systematic verification layer for generated capabilities."* The pipeline grounded in empirically observed contamination patterns is a novel contribution.

---

## 2. Verdict at a glance

| Proposal claim | Realized? | Primary implementation surface |
|---|---|---|
| Tier 1 — static analysis (AST + regex) with five categories + severities | ✅ ALIGNED + IMPROVED | `src/verification/static_scanner.py` + layered fence extractor in `gate.py` |
| Tier 2 — behavioral testing (nonexistent entity / consistency / boundary / latency) | ✅ ALIGNED + IMPROVED | `src/verification/behavioral.py` (6 test categories, includes proposal's 4 + timeout + output-variation + parameterless-smoke) |
| Tier 3 — adversarial probing (hidden-demo / ground-truth / network-disconnect) | ✅ ALIGNED | `src/verification/adversarial.py` (DeepAgent with all three attack patterns fixed in prompt + schema) |
| *"Every generated capability must pass all three tiers"* | ✅ ALIGNED (was drifting; fixed §12 finding #14) | `src/orchestrator/verification_node.py` — 3× retry + sticky flag + fail-closed with CLI exit 4 |
| *"Critical blocks deployment; High requires Tier 2 clearance"* | ✅ ALIGNED | `gate.py` — fail-fast tier skipping; Tier 2 clearance downgrades Tier 1 HIGH → MEDIUM when Tier 2 passes |
| F1 Hardcoded responses | ✅ ALIGNED | AST literal-list/dict return check + 1-level indirection |
| F2 Silent fallbacks | ✅ ALIGNED | AST `except:`/`except Exception:` silent-return detection |
| F3 Mock contamination | ✅ ALIGNED | Regex mock indicators + AST import check (`unittest.mock`, `faker`) |
| F4 Graceful-looking degradation | ✅ ALIGNED | Tier 2 output-variation + parameterless-smoke + consistency; Tier 3 hidden-demo-mode |
| Research contribution framing (novel vs. EvoMAC/EvoAgentX/STELLA/AgentEvolver/ACE) | ✅ ALIGNED | The gate itself is the novelty; `test_verification.py` is the demonstration harness |
| Tier 0 structural pre-check for SKILLs (`quick_validate.py`) | ➕ IMPROVEMENT (not in proposal) | `tools/quick_validate.py` + `static_scanner._run_skill_precheck` |
| Tier 1.5 sandbox import probe (Docker dry-run + fence `compile()`) | ➕ DIVERGENCE (disclosed as cost optimization) | `src/verification/import_check.py` — Chapter 4.5 disclosure required |
| Tier 1 applied to SKILL/AGENT prose via layered fence extractor | ➕ IMPROVEMENT | `gate.py::_extract_python_fences` — the proposal's "generated code" scope implicitly covered MCP_TOOL; SKILL/AGENT extension avoids false positives on prose while scrutinizing embedded fences |
| Per-operation gate application in Pillar 3 (split/merge variants) | ➕ IMPROVEMENT | `src/optimizer/operations.py::verify_and_register_or_rollback` — Pillar 2 × Pillar 3 coupling not in proposal |
| Coverage verifier (Step 7) — parent-failure replay against split variants | ➕ IMPROVEMENT (not a 4th tier, orthogonal axis) | `src/verification/coverage.py` (MCP_TOOL-only scope — disclosed) |

**Bottom line.** All three proposal-named tiers are realized with stronger semantics than the spec demands. Two **deliberate divergences** are disclosed (Tier 0 structural check + Tier 1.5 import probe), both documented as cost-optimization / ecosystem-specific pre-checks rather than replacements for the three-tier methodology. Three **improvements** strengthen the gate beyond the proposal: (1) Tier 1 AST scanning applied to embedded Python fences in SKILL/AGENT prose, (2) the Pillar 2 × Pillar 3 coupling that forces split/merge variants through the gate, and (3) fail-closed retry semantics with sticky flags and dedicated CLI exit codes (§12 finding #14) — **applied to the retry loop; two narrow fail-open branches on unexpected internal exceptions remain by design and are disclosed in §7.** No **silent drift** in the retry loop or tier mechanics was identified in this audit.

---

## 3. File-by-file rundown

Legend: **ALIGNED** = directly realizes a proposal claim. **IMPROVEMENT** = stronger mechanism than the proposal sketch. **DIVERGENCE** = diverges from the literal proposal wording (disclosed). **NEUTRAL** = infrastructure, does not directly bear on Pillar 2 claims.

### 3.1 `sandbox/` (1 file)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `sandbox/Dockerfile` | Minimal base image (`mcp` + `pydantic` only) with `uv` for dynamic package install. Runs as non-root `sandbox` user. Used by Tier 1.5, Tier 2, Tier 3, and Pillar 3 coverage replay. | Tier 2/3 isolation — **F2/F4** detection requires network-disconnected + dep-isolated execution to reveal fake data vs. real calls. | ALIGNED — supports `--network none` enforcement that powers Tier 3's network-disconnect attack. |

**Verdict.** The single Dockerfile is the isolation substrate for every tier that executes code. Proposal's network-disconnect attack (Tier 3) and behavioral probing (Tier 2) both depend on this image existing.

### 3.2 `scripts/` (21 files)

Scripts are evaluation / diagnostic / test harnesses. Only one directly bears on Pillar 2 correctness; the others are NEUTRAL for the purposes of this audit (they exercise the gate but are not the gate).

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `scripts/test_verification.py` | Integration test of the three-tier gate: runs known-good MCP_TOOL (fibonacci), known-bad MCP_TOOL (hardcoded dict return), known-good/bad SKILL, known-good AGENT. Asserts tier-reached and pass/fail semantics. | All three tiers; used to validate Tier 1.5 retrofit (Step 12.5, commit `3260cb0`). | ALIGNED — reference harness. |
| `scripts/test_auto_optimizer.py` | Pillar 3 wiring test; uses `patch("src.verification.gate.verify")` to simulate gate rejection in split/merge paths. | Gate × Pillar 3 coupling. | NEUTRAL for Pillar 2. |
| `scripts/test_split_merge.py` | Integration tests for split/merge across all three types — each variant routed through the gate via `verify_and_register_or_rollback`. | Pillar 2 × Pillar 3 coupling. | NEUTRAL for Pillar 2. |
| `scripts/test_feedback_split.py` | Tests that wrong-output feedback drives Pillar 3 split → gate → registration. | Pillar 3; gate only as downstream dependency. | NEUTRAL for Pillar 2. |
| `scripts/test_mint.py`, `test_pipeline.py`, `test_prune.py`, `test_execution_backends.py`, `test_cli_settings.py`, `test_registry_matching.py` | Various smoke / integration tests that invoke the full pipeline. | Exercise the gate as a side-effect. | NEUTRAL — they use Pillar 2 without redefining it. |
| `scripts/run_pilot.py`, `run_longitudinal.py`, `run_longitudinal_shift.py` | Evaluation harnesses. `run_pilot.py` includes the `no_verification` baseline (gate bypassed by `verification_enabled=False`) that directly answers the research-contribution claim ("does the verification layer actually help?"). | Research-contribution claim (quantifying the verification layer's effect). | ALIGNED — the `no_verification` baseline in the 4-condition matrix is the empirical counterpart to the proposal's "novel contribution" framing. |
| `scripts/analyze_results.py`, `analyze_longitudinal.py`, `analyze_context_utilization.py` | Post-run analyzers. `analyze_results.py` reports mock-contamination rate + end-to-end success rate across the 4 baselines. | Research-contribution claim. | ALIGNED — instruments the claim. |
| `scripts/step10_merge_candidates.py`, `backfill_token_costs.py`, `migrate_business_domain_labels.py`, `validate_contract_extractor.py` | One-off maintenance / research scripts. | Unrelated to Pillar 2. | NEUTRAL. |

**Verdict.** `test_verification.py` is the canonical demonstration that the gate realizes the proposal's three-tier behavior end-to-end. The evaluation harnesses contain the empirical counterpart to the "novel contribution" claim via the `no_verification` baseline.

### 3.3 `src/verification/` (7 files) — the core

This is the directory that *is* Pillar 2. Every file here is directly in scope.

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `static_scanner.py` | Tier 1 AST + regex scanner. Detects: **hardcoded_data** (Critical, AST literal list/dict return with 1-level variable indirection), **silent_exception** (High, AST bare/broad except with silent return or pass), **mock_artifact** (Critical, regex on `mock\|fake\|dummy\|placeholder\|DEMO\|TEST_DATA`), **mock_import** (Critical, AST `unittest.mock`/`faker`), **synthetic_data** (High, `TODO`/`FIXME`/`placeholder` comments + `lorem ipsum` string), **missing_external_calls** (Medium/High depending on `allow_network`), **determinism_artifact** (High, `random.seed(<literal>)`). Also runs Tier 0 `quick_validate.py` for SKILLs. | Tier 1 entire spec (table of 5 categories). F1 (hardcoded_data), F2 (silent_exception), F3 (mock_artifact + mock_import). | ALIGNED + IMPROVED. **Deviation from proposal severities:** proposal lists synthetic-data indicators as Medium; implementation rates TODO/FIXME comments and lorem ipsum as HIGH and `random.seed(<literal>)` as HIGH. The implementation is strictly stricter than the proposal demands, consistent with fail-closed intent. `random.seed` specifically catches a "fake-determinism" sub-pattern of F4 that the proposal names but does not enumerate as its own severity. |
| `import_check.py` | Tier 1.5 sandbox import probe. MCP_TOOL: Docker `python -c "import tool"` with declared `pip_packages`, catches `wrong_pip_package` / `import_error`. SKILL/AGENT: in-process `compile()` on each embedded ```python fence; AGENT also structurally validates `agent_config.system_prompt`. | None — not in proposal's three-tier structure. | DIVERGENCE (disclosed). Cost-optimization only. The failure modes it catches (wrong PyPI names, runtime import errors, fence `compile()` errors that `ast.parse` masks) are **operational-correctness issues NOT in the proposal's F1–F4 taxonomy.** Without Tier 1.5, Tier 2 still catches them via `ModuleNotFoundError` — just at ~20-40× the token cost. **Chapter 4.5 must disclose Tier 1.5 as a fourth operational layer, not a fourth methodological tier.** Per master plan Step 12.5 and `MEMORY.md::project_sandbox_import_check`. |
| `behavioral.py` | Tier 2 behavioral testing. Six test categories: `_test_nonexistent_entity` (empty/bogus inputs, checks for silent swallowing), `_test_consistency` (same input twice, result + error must match), `_test_boundary` (zero / negative / large; distinguishes pip-install failures from code crashes), `_test_timeout` (>5s threshold), `_test_latency` (statistical: very fast AND very uniform = suspicious), `_test_output_variation` (different inputs must yield different outputs — F4 detection), `_test_parameterless_smoke` (non-trivial output from a no-arg tool = canned-data signal). Pre-flight dependency check so pip failures report once instead of once per category. | Tier 2 entire spec. Proposal names 4 categories (nonexistent / consistency / boundary / latency); implementation has 6. F4 covered by output-variation + parameterless-smoke. | ALIGNED + IMPROVED. The extras (`timeout`, `output_variation`, `parameterless_smoke`) each close a specific gap: `parameterless_smoke` catches F1 for no-arg tools the other categories cannot probe; `output_variation` is the most direct F4 detector; `timeout` is a boundary-test sub-case with its own severity. |
| `adversarial.py` | Tier 3 DeepAgent red-team. Three attack patterns fixed in the system prompt **and** in the `AdversarialFinding.attack_pattern` response-format enum (so the LLM cannot invent new names and have them pass structured-output validation): `hidden_demo_mode`, `network_disconnect`, `ground_truth`. `reasoning_effort="low"` on the model to drop ~2k tokens/call of redundant planning. Per-probe wrapper unions the artifact's declared `pip_packages` into every `sandbox_execute` call. 480 s node timeout; 50-step recursion limit. **Fail-closed on timeout** (§12/Step 14.11) — returns HIGH-severity `adversarial_timeout` so the `verification_node` retry loop fires. Fail-open on non-timeout error (MEDIUM). | Tier 3 entire spec. Hidden-demo-mode ↔ F3+F4 shortcut patterns. Ground-truth ↔ proposal's "comparison against known ground truth". Network-disconnect ↔ proposal's "network connectivity simulated as unavailable" test. | ALIGNED. The fail-closed timeout is an §12 finding #14-aligned correction: pre-fix, timeouts were silent `overall_passed=True`, which violated *"Every generated capability must pass all three tiers"*. Type-aware probe-source extraction (`_extract_probe_source`) dispatches MCP_TOOL → `source_code`, SKILL/AGENT → concatenated fenced Python blocks — the same surface Tier 1's layered scanner already targets, closing the AGENT-Tier-3-always-skips latent bug (`perf_fixes_2026_04_14.md §2c-3`). |
| `gate.py` | Orchestrates all four operational tiers with fail-fast ordering: Tier 0 (SKILL structural) → Tier 1 (AST + regex, with layered fence extraction for SKILL/AGENT) → Tier 1.5 (import probe) → Tier 2 (behavioral) → Tier 3 (adversarial). Tier-skip rules: Critical/Tier 0 → skip all; Critical/Tier 1 → skip 1.5/2/3; Critical/Tier 1.5 → skip 2/3; Critical or High/Tier 2 → skip 3. **Tier 2 clearance downgrade:** if Tier 1 flagged HIGH but Tier 2 is clean, the Tier 1 HIGHs are downgraded to MEDIUM (aligns directly with proposal's *"High findings require Tier 2 clearance"*). Fail-open on gate *internal* error (never raises — returns a MEDIUM `gate_error` finding with `overall_passed=True`). | All of Tier 1 + Tier 2 + Tier 3; the *"must pass all three tiers"* contract; the Critical-blocks / High-requires-Tier-2-clearance rule. | ALIGNED. The Tier 2-clearance downgrade is the only place in the codebase where the proposal's exact severity-escalation semantics are mechanized. Fence-extraction (`_extract_python_fences`) is the key mechanism for applying Tier 1's MCP_TOOL-grade AST scan to SKILL/AGENT prose without false positives on words like "mock" or "placeholder". |
| `sandbox_tool.py` | Docker execution primitive. Two-phase: (1) `uv pip install` with network, (2) run code with `--network none` (or `bridge` if `allow_network=True`). `extract_imports` + LLM-based `import → PyPI` resolution with session cache + identity-mapping fallback. `sandbox_execute_in_workspace` adds a read-write `/workspace` mount for compositional chain execution. Package-name validator rejects suspicious strings. | Tier 2/3 infrastructure — F2 detection depends on `--network none` being enforced. | ALIGNED. This is the mechanism that makes the network-disconnect attack (Tier 3) observable. |
| `coverage.py` | Coverage verifier for split operation (Step 7). Replays parent's recorded failure cases against each variant; applies failure-mode-fix semantics (error case: variant handles iff replay doesn't raise; wrong-output case: variant handles iff replay runs AND produces output ≠ parent's recorded `actual_output`). **MCP_TOOL-only scope** — SKILL/AGENT return `status=unverifiable_type` per Pillar 1 × Pillar 3 tension (deterministic-executor assumption only holds for MCP_TOOL). | Not a tier. An **additive strengthening** at the Pillar 2 × Pillar 3 boundary. | IMPROVEMENT. Not in the proposal's Pillar 2 spec. Earlier master-plan prose mis-labelled this as "Tier 4"; the module docstring now explicitly rejects that framing — it is an orthogonal cross-check, not a fourth tier. |

**Verdict.** Every proposal-named tier is realized. Two additive pre-checks (Tier 0, Tier 1.5) and one additive post-check (coverage) extend the gate beyond the proposal's three-tier spec; all three are disclosed in master-plan prose as non-proposal additions rather than silent drift.

### 3.4 `src/orchestrator/` — verification wiring (15 files)

Only files that bear on Pillar 2 are listed. The rest (Pillar 1 surface) are covered in `02_pillar_1_jit_pipeline.md`.

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `verification_node.py` | Pillar 2 retry orchestration. Runs `gate_fn` in parallel (thread pool, 4 workers) over every artifact, up to 3 attempts. **Fail-closed on exhaustion** (§12 finding #14): failed artifacts are DROPPED from `generated_artifacts`, sticky `verification_succeeded=False` is set, pipeline proceeds to terminal state so the CLI can emit exit code 4. Skip-already-passed optimization: artifacts with existing passing reports skip re-verification on retry. On gate exception: treat as `overall_passed=False` with HIGH-severity `gate_error` (fail-safe, not fail-open). | *"Every generated capability must pass all three tiers before entering the production skill registry"* — the operational contract. | ALIGNED + IMPROVED. Pre-§12-finding-#14, the node was fail-open (unverified artifacts kept + executed with warnings) — a latent drift from the proposal. The 2026-04-16 fix brought it to proposal-strict semantics with a uniform retry + sticky-flag + dedicated exit code pattern shared with Pillar 1.1 decomposition (exit 2) and Pillar 1.3 classification (exit 5). |
| `pipeline.py` | Wires `verification_node` between generators and `loading_node`. `verification_enabled=False` substitutes the stub gate for the `no_verification` evaluation baseline (RQ instrumentation). | Gate placement in the 9-node graph. | ALIGNED. |
| `tool_loader.py`, `loading_node.py`, `composition.py`, `decomposition.py`, `classifier.py`, `gap_detection.py`, `generators/*.py`, `prompts/*.py`, `taxonomy.py`, `tracing.py`, `schema_extraction.py`, `output_contract_resolver.py` | Pillar 1 surface or peripheral. | — | NEUTRAL for Pillar 2. |

**Verdict.** The retry orchestration is the single most important piece of Pillar 2 operational semantics. The §12 finding #14 fix (2026-04-16) is the load-bearing correction that brought the codebase into proposal-strict alignment.

### 3.5 `src/optimizer/` (4 files) — Pillar 2 × Pillar 3 coupling

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `operations.py` | `verify_and_register_or_rollback()` — shared helper that every split-variant and merge-variant must route through. Runs the three-tier gate on the new artifact; registers only on pass; leaves parent(s) active on fail. Atomic: parent-deactivation happens only after a `(True, None)` return. | *"Only verified capabilities enter production"* — extends this contract to Pillar 3's evolved capabilities. | IMPROVEMENT. Not in proposal Pillar 2 text, but a necessary corollary. Pre-2026-04-15 code created metadata-only descriptors that bypassed the gate with a self-justifying comment; that violated the proposal and was corrected in Steps 5+6. |
| `utility.py`, `loop.py`, `simulation.py` | Pillar 3 utility scoring and loop mechanics. | — | NEUTRAL for Pillar 2. |

### 3.6 `src/schemas/` (6 files)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `verification.py` | Pydantic models: `Severity` (StrEnum: CRITICAL/HIGH/MEDIUM/LOW/INFO), `VerificationFinding` (tier ∈ [0,3]), `VerificationReport`. `tier ge=0 le=3` forbids half-tiers — this is why Tier 1.5 findings carry `tier=1` (with `tier_reached=1` when 1.5 rejects). | Typed output of every tier; used by CLI sidecar `<cap>_<ts>.verification.json`. | ALIGNED. Five severities vs. proposal's three (Critical/High/Medium) — the extras (LOW, INFO) are used by skip / pass reports to keep the never-raises contract clean. |
| `capability.py`, `task.py`, `metrics.py`, `evaluation.py`, `optimizer_config.py` | Peripheral schemas. | — | NEUTRAL. |

### 3.7 `src/registry/` (3 files)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `store.py`, `metrics.py`, `evaluation_store.py` | Post-gate surface: only capabilities that passed verification are registered here. | Enforces *"production skill registry"* downstream of the gate. | NEUTRAL for Pillar 2 (the gate is upstream; the registry is the beneficiary). |

### 3.8 `src/cli/` (4 files)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `runner.py` | Detects sticky `verification_succeeded=False` at terminal state and emits CLI exit code **4** with `[VERIFICATION FAILED — no usable capability produced]` and the Pillar 2 contract quoted to the user. Writes `data/mint_output/<cap>_<ts>.verification.json` as a post-hoc artifact. Also implements exits 2/3/5 for sibling sticky flags (decomposition / output contract / classification). | Honours *"Only verified capabilities enter production"* at the user-visible boundary. | ALIGNED + IMPROVED — dedicated exit code per stage per §12 finding #14/#15/#16. |
| `app.py`, `display.py`, `settings.py` | UX / plumbing. | — | NEUTRAL. |

### 3.9 `src/mcp_tools/` (1 file)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `fibonacci.py` | Reference hand-built MCP tool used by `scripts/test_verification.py` as the canonical known-good artifact (expected to pass all three tiers). | Tier-reached=3 sanity check for the gate. | NEUTRAL (test fixture). |

### 3.10 `tools/` (2 files)

| File | Role | Maps to | Alignment |
|---|---|---|---|
| `quick_validate.py` | Tier 0 structural pre-check for SKILLs: YAML frontmatter present, hyphen-case naming (max 64 chars), description ≤ 1024 chars, no angle brackets, body doesn't contain literal `lorem ipsum` / `TODO: implement` / `# placeholder return` markers, body ≤ 500 lines (warning only). Invoked via `validate_skill()` by `static_scanner._run_skill_precheck`. | None — not in proposal's three-tier spec. Structural pre-check. | DIVERGENCE (disclosed, ecosystem-specific). The Agent Skills specification (agentskills.io) mandates the frontmatter structure; without Tier 0 a malformed SKILL.md would reach Tier 1 regex and Tier 2 execution, both of which are ill-suited to YAML-shape errors. Like Tier 1.5, this is infrastructure scaffolding rather than a novel methodology tier. |
| `init_skill.py` | Scaffolding command that emits a boilerplate SKILL.md + `scripts/`, `references/`, `assets/` directory layout from a skill name. Not invoked by the gate itself. | Developer-facing skill authoring. | NEUTRAL for Pillar 2. |

---

## 4. Full findings table

Findings scored against the four proposal claims (F1/F2/F3/F4) and the three tiers. Column **Status**: `✅` aligned; `✅+` improved; `➕` additive feature; `⚠️` drift noted; `ℹ️` disclosed divergence.

| # | Area | Proposal wording | Implementation | Status | Where | Note |
|---|---|---|---|---|---|---|
| 1 | Tier 1 / F1 | Hardcoded data, *Critical* | AST literal list/dict ≥3 items returned from function body, +1-level variable indirection | ✅ | `static_scanner._check_hardcoded_return` | Indirection is implementation-specific; not named in proposal. |
| 2 | Tier 1 / F2 | Silent exception handling, *High* | AST bare `except:` / `except Exception:` with silent-return or pass; logging/reraise clears the flag | ✅ | `static_scanner._check_silent_except` | |
| 3 | Tier 1 / F3 | Mock artifacts, *Critical* | Regex on mock/demo/placeholder names + AST import check on `unittest.mock`, `mock`, `faker` | ✅ | `static_scanner._check_mock_artifacts`, `_check_mock_imports` | `sample` intentionally excluded to avoid `random.sample()` false positives. |
| 4 | Tier 1 / F3 | Synthetic data indicators, *Medium* | TODO/FIXME/placeholder comments (HIGH), lorem ipsum literal (HIGH), `random.seed(<literal>)` (HIGH) | ✅+ | `static_scanner._check_synthetic_comments`, `_check_lorem_ipsum`, `_check_random_seed` | Implementation is stricter than proposal (HIGH vs. MEDIUM). Intentional fail-closed bias. |
| 5 | Tier 1 / F2 | Missing external calls, *High* | Function names matching `fetch/query/call/get/retrieve/search` with no `requests/httpx/urllib/...` import. Severity Medium when `allow_network=False`, High when True. | ✅ | `static_scanner._check_missing_external_calls` | Severity is network-context-dependent — stricter when the tool *could* reach network and isn't. |
| 6 | Tier 1 / scope | *"Generated code"* | MCP_TOOL gets full AST+regex; SKILL/AGENT prose gets regex-only at the top level, but embedded ```python fences are extracted and fed back through the MCP_TOOL-grade AST scan. | ✅+ | `gate._extract_python_fences` + `_static_scan` | Closes T04 false-positive reproducer (prose words like "mock" / "placeholder" no longer false-fire on SKILL/AGENT descriptions, but embedded code is still scrutinised). |
| 7 | Gate rule | *"Critical blocks deployment"* | Tier-skip on first Critical; exit with `overall_passed=False`. | ✅ | `gate._verify_inner` + `static_scanner.should_proceed_to_tier2` | |
| 8 | Gate rule | *"High findings require Tier 2 clearance"* | After Tier 2 clean, Tier 1 HIGH findings are downgraded to MEDIUM so they don't block at the final gate. | ✅ | `gate._verify_inner` (`t1_had_high` branch) | Exact mechanization of the proposal's severity-escalation semantics. |
| 9 | Tier 2 | Nonexistent entity tests | Empty string / zero inputs; sandbox-succeeds-but-unparseable or None/`""` return with no error → MEDIUM finding. | ✅ | `behavioral._test_nonexistent_entity` | |
| 10 | Tier 2 | Consistency tests | Same input twice; different result or error → HIGH. | ✅ | `behavioral._test_consistency` | |
| 11 | Tier 2 | Boundary tests | Zero/empty, negative, large inputs; crash + dep-failure distinguished. | ✅ | `behavioral._test_boundary` | |
| 12 | Tier 2 | Latency analysis | Two-condition gate: mean < 1ms AND coefficient-of-variation < 0.10 across ≥2 distinct inputs → MEDIUM. | ✅ | `behavioral._test_latency` | Two-condition gate keeps FP rate near zero. |
| 13 | Tier 2 / F4 extras | *(not in proposal)* | `_test_output_variation` — identical output for very different inputs → MEDIUM (most direct F4 detector). | ✅+ | `behavioral._test_output_variation` | Implementation-specific F4 surface. |
| 14 | Tier 2 / F1 extras | *(not in proposal)* | `_test_parameterless_smoke` — no-arg tool returning non-trivial output is canned-data-suspicious → MEDIUM. | ✅+ | `behavioral._test_parameterless_smoke` | Closes the gap that schema-based tests leave for no-arg tools. |
| 15 | Tier 2 / misc | *(not in proposal)* | `_test_timeout` — >5s wall-clock → MEDIUM. | ✅+ | `behavioral._test_timeout` | |
| 16 | Tier 3 | Hidden demo/sample mode | Attack pattern #1 in system prompt + enum in `AdversarialFinding.attack_pattern`. | ✅ | `adversarial.ADVERSARIAL_SYSTEM_PROMPT` | |
| 17 | Tier 3 | Ground-truth comparison | Attack pattern #3; applies to deterministically-verifiable tools (fibonacci, hashing, unit conv). | ✅ | `adversarial.ADVERSARIAL_SYSTEM_PROMPT` | |
| 18 | Tier 3 | Network-disconnect test | Attack pattern #2; sandbox already `--network none`, so any "successful" remote-fetch output is ipso facto suspicious. | ✅ | `adversarial.ADVERSARIAL_SYSTEM_PROMPT` + `sandbox_tool` | |
| 19 | Tier 3 / scope | *"Generated code"* | Pre-2026-04-14, AGENT Tier 3 always hit the empty-source skip (since AGENT has no `source_code`). Now `_extract_probe_source` extracts ```python fences from `agent_config.system_prompt` + embedded `skill_content`, matching Tier 1's layered extractor. | ✅+ | `adversarial._extract_probe_source` | Recharacterized 2026-04-16: the prior master-plan wording ("white-box inspection") was inaccurate — this is a capability-type coverage gap, not a methodology substitution. |
| 20 | Tier 3 / timeout | *"Must pass all three tiers"* | Fail-closed on timeout: HIGH-severity `adversarial_timeout` + verification_node retry. Pre-Step-14.11 behavior was fail-open (silent PASS), violating the contract. | ✅ | `adversarial._timeout_report` | §12 Step 14.11 correction. |
| 21 | Tier 3 / deps | *(not in proposal)* | Probe-scoped `sandbox_execute` wrapper unions artifact's `pip_packages` into every call — pandas/numpy-dependent artifacts no longer crash their first probe turn with ModuleNotFoundError. | ✅+ | `adversarial._make_probe_sandbox_execute` | Step 14.9 fix. |
| 22 | Must-pass contract | *"Every generated capability must pass all three tiers"* | 3× retry; sticky `verification_succeeded`; failed artifacts dropped from `generated_artifacts` on exhaustion; CLI exit code 4. | ✅ | `verification_node.verification_node` + `cli/runner.py` | §12 finding #14. Pre-fix was fail-open + silent. |
| 23 | Research contribution | *"Novel vs. EvoMAC/EvoAgentX/STELLA/AgentEvolver/ACE"* | Empirical counterpart: `no_verification` baseline in `BaselineCondition`; `scripts/run_pilot.py` runs it; `analyze_results.py` reports mock-contamination-rate. | ✅ | `tests/evaluation/tasks.py`, `scripts/run_pilot.py`, `scripts/analyze_results.py` | 4-condition matrix directly instruments the claim. |
| 24 | *(additive)* | Tier 0 — SKILL structural pre-check | `tools/quick_validate.py` invoked by `static_scanner._run_skill_precheck`; rejects malformed frontmatter / invalid names / lorem ipsum body before AST/regex. | ℹ️ | `tools/quick_validate.py` | Ecosystem-specific pre-check (Agent Skills spec); not in proposal. |
| 25 | *(additive)* | Tier 1.5 — sandbox import probe | Docker dry-run import for MCP_TOOL + in-process `compile()` on embedded fences for SKILL/AGENT. Catches wrong PyPI names + runtime import errors + unterminated strings/mismatched brackets that `ast.parse` masks. | ℹ️ | `src/verification/import_check.py` | **Chapter 4.5 disclosure required** per master plan Step 12.5 — cost-optimization only, NOT a fourth methodological tier. Failure modes are operational-correctness issues NOT in the F1–F4 taxonomy. |
| 26 | *(additive)* | Coverage verifier for split variants | Replays parent's recorded failure cases against variants; MCP_TOOL-only scope (SKILL/AGENT return `status=unverifiable_type`) per Pillar 1 × Pillar 3 tension. | ➕ | `src/verification/coverage.py` | Not a fourth tier — orthogonal axis. Module docstring explicitly rejects the "Tier 4" framing earlier master-plan prose had used. |
| 27 | *(additive)* | Pillar 2 × Pillar 3 coupling | Split + merge variants route through `verify_and_register_or_rollback`; atomic rollback on gate rejection. | ➕ | `src/optimizer/operations.py` | Extends *"must pass all three tiers"* from Pillar 1's generated capabilities to Pillar 3's evolved capabilities. |
| 28 | *(additive)* | Retry-loop cost optimization | Already-passed artifacts skip re-verification on retry (Tier 2/3 token-budget saving). | ➕ | `verification_node.verification_node` (`already_passed` branch) | |
| 29 | *(additive)* | CLI post-hoc artifact | `data/mint_output/<cap>_<ts>.verification.json` written after every `mint run`. | ➕ | `src/cli/runner.py` | Makes the sidecar inspectable without re-running the pipeline. |

---

## 5. Deliberate divergences — summary

The implementation diverges from the proposal's literal wording in exactly two places. Both are disclosed, neither is silent drift.

| Divergence | What the proposal says | What the implementation does | Why | Disclosure |
|---|---|---|---|---|
| **Tier 0 structural pre-check** | Not mentioned. | Run `quick_validate.py` on SKILL artifacts before Tier 1. | Agent Skills spec (agentskills.io) mandates frontmatter structure; malformed YAML would confuse Tier 1/2 without giving useful signal. | `static_scanner._run_skill_precheck` comment; present-tense in `01_bootstrapping_problem.md`. |
| **Tier 1.5 sandbox import probe** | Not mentioned. | Docker `python -c "import tool"` for MCP_TOOL + in-process `compile()` for SKILL/AGENT fences, between Tier 1 and Tier 2. | Cost optimization. Catches `wrong_pip_package` / `import_error` / `fence_compile_error` at ~5-10s Docker cost, avoiding ~20-40× higher Tier 2/3 token cost for the same failure modes. **NOT a new methodology tier** — the failure modes are operational-correctness issues NOT in the F1–F4 taxonomy. | `docs/MASTER_PLAN_2026_04_13.md` Step 12.5 DONE block; `MEMORY.md::project_sandbox_import_check`. **Chapter 4.5 must disclose this framing.** |

Additive pieces that extend the proposal without diverging from it: coverage verifier (`src/verification/coverage.py`), Pillar 2 × Pillar 3 coupling (`src/optimizer/operations.py::verify_and_register_or_rollback`), and retry-loop already-passed skip optimization. None of these contradict the proposal; all strengthen the *"must pass all three tiers"* contract.

---

## 6. Research-contribution framing

The proposal claims Pillar 2 is *"a novel contribution"* not present in EvoMAC, EvoAgentX, STELLA, AgentEvolver, or ACE. The empirical instrumentation of this claim:

| Claim component | Instrumentation |
|---|---|
| *"Systematic verification layer for generated capabilities"* | `src/verification/gate.py` + the four-layer operational gate (Tier 0/1/1.5/2/3) — this is the artifact of the claim. |
| *"Grounded in empirically observed contamination patterns"* | Static-scanner category names map 1:1 to F1–F4 plus observed sub-patterns (`determinism_artifact` = fake-determinism sub-case of F4). Categories are enumerated in the `static_scanner.py` module docstring. |
| *"Does the verification layer actually help?"* (implicit) | `BaselineCondition.NO_VERIFICATION` in the 4-condition evaluation matrix. `scripts/run_pilot.py` runs it; `scripts/analyze_results.py::analyze` reports mock-contamination-rate + end-to-end success-rate by condition. |
| *"Not just functional correctness (STELLA's baseline)"* | Tier 3 adversarial probing — the three attack patterns (`hidden_demo_mode`, `network_disconnect`, `ground_truth`) are the operational distinction from STELLA's functional-correctness tests. |

---

## 7. Loose ends / honest gaps

| Gap | Severity | Notes |
|---|---|---|
| Tier 3 fail-open on non-timeout errors | Low | When the adversarial agent itself crashes (provider error, SDK bug), `adversarial.probe` returns MEDIUM `adversarial_error` + `overall_passed=True` (`src/verification/adversarial.py::_error_report`). Deliberately out of scope for Step 14.11 — provider errors are often transient and flipping them to fail-closed would trigger regenerations for reasons the LLM can't fix. Codified by `tests/unit/test_adversarial.py::test_non_timeout_exception_still_fail_open`. Revisit in Chapter 7 if Phase 5 logs show a pattern. |
| Gate-internal exception fail-open | Low | The outer `verify()` wrapper catches every unexpected exception raised during tier dispatch (e.g., scanner OSError, corrupt AST cache) and returns MEDIUM `gate_error` + `overall_passed=True` (`src/verification/gate.py::verify`). This is about *gate-code bugs*, not verification outcomes — a crashing static scanner should not block the entire pipeline. Codified by `tests/unit/test_gate.py::test_verify_never_raises_on_unexpected_exception`. Same trade-off rationale as the Tier 3 row above: transient / engineering-level failures are separated from adversarial or behavioral conclusions. |
| Coverage verifier scope restricted to MCP_TOOL | Disclosed | SKILL/AGENT split variants rely on the three-tier gate per variant without collective coverage verification. Justified by the Pillar 1 × Pillar 3 tension (deterministic-executor assumption). Chapter 7 future work. |
| No formal per-failure-mode attribution in Tier 2 findings | Low | Tier 2 categories are behaviorally-named (`consistency`, `output_variation`) rather than F-code-named. A future retrofit could tag each finding with the F1–F4 mode(s) it most likely indicates, making the cross-tier coverage matrix explicit. Not blocking Chapter 4. |
| Tier 1 regex for MCP_TOOL only | By design | Prose-based SKILL/AGENT content is regex-skipped at the top level to avoid T04 false positives (words like "mock" in legitimate instructions). Fenced Python code gets the full scan via `_extract_python_fences`. This is the correct design; the "gap" is only a gap if you expect regex to run on prose, which the T04 reproducer showed is wrong. |

---

## 8. Citations

- `docs/thesis_proposal.md` §Pillar 2 lines 101–152 — source of the claims audited.
- `docs/MASTER_PLAN_2026_04_13.md` §12 findings #14 (verification fail-closed), #15 (decomposition fail-closed), #16 (classification fail-closed), and Step 12.5 (Tier 1.5 disclosure framing).
- `docs/MASTER_PLAN_2026_04_13.md` Step 14.11 — Tier 3 timeout fail-closed.
- `docs/archive/perf_fixes_2026_04_14.md` §2c-3 — Tier 3 AGENT coverage recharacterization.
- `CLAUDE.md` §Verification Gate (Tiered Cost Optimization), §Three-layer strict-gate pattern.
- `MEMORY.md::project_sandbox_import_check` — Tier 1.5 closure note.
- `MEMORY.md::Tier 3 coverage-gap drift` — 2026-04-16 recharacterization.
- Prior audits in the same series: `docs/verification/01_bootstrapping_problem.md`, `docs/verification/02_pillar_1_jit_pipeline.md`.
