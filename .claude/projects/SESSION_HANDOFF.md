# Session Handoff — Phase 3 implementation + bug fixes + CLI improvements

**Written:** 2026-04-11 (post-Phase-3 implementation, pre-commit)
**Purpose:** brief the next Claude Code session on exactly where we are.

---

## You are picking up after Phase 3 was implemented but NOT YET COMMITTED

The project is **MINT** (Model-driven Instantiation of Novel Tools), a master's thesis on just-in-time capability synthesis. We are in **Iteration 6: Evaluation & Analysis**. Phase 3 (compositional executor) is code-complete with 968 unit tests passing, but the changes are uncommitted and need review before commit.

**Source-of-truth docs, in priority order:**

1. **`docs/pre_eval_execution_plan.md`** — ordered sequencing of all pre-eval work. **Phases 0, 1, 2 are complete. Phase 3 is code-complete but uncommitted.** Phase 3.5 and 4-7 remain.
2. **`docs/compositional_synthesis_plan.md`** — implementation plan for the compositional refactor. **Stages 1-4 are code-complete** (Stages 1-2 committed, Stages 3-4 uncommitted). Stage 4.5 (DeepAgents subagents) remains.
3. **`docs/session_primer_phase3.md`** — **NEW, written this session** — detailed primer on everything done in this session with findings and known issues.
4. **`docs/context_engineering_mapping.md`** — Write/Select/Compress/Isolate × MCP_TOOL/SKILL/AGENT implementation mapping. The thesis's context-engineering reference.
5. **`docs/todo_drift_and_missing.md`** — focused list of drift/missing items.
6. **`docs/proposal_vs_implementation.md`** — full audit (needs updating for Phase 3).
7. **`CLAUDE.md`** — general project context. **Updated for Phase 3.**
8. **`docs/thesis_proposal.md`** — **the contract**. Do not modify.

---

## Exact state right now

- **Branch:** `iter/6-execution-backends`
- **Latest committed:** `2361cd0` docs: archive three completed/superseded plans
- **Uncommitted changes:** ~35 changes across 15+ files implementing Phase 3 + bug fixes + CLI improvements
- **Unit tests:** 968 passing (939 baseline + 29 new composition tests)
- **Working tree:** DIRTY — all Phase 3 changes are staged/unstaged, not committed

### Key new/modified files (uncommitted)

**New files:**
- `src/orchestrator/composition.py` — CompositionalExecutor + build_resolution_map()
- `tests/unit/test_composition.py` — 29 unit tests
- `tests/integration/test_composition.py` — Docker integration tests

**Modified files (significant changes):**
- `src/verification/sandbox_tool.py` — new `sandbox_execute_in_workspace()` function
- `src/orchestrator/classifier.py` — `ClassifiedGap.query` fix (ROOT CAUSE bug fix)
- `src/orchestrator/gap_detection.py` — `MatchItem.query` populated from gap detection
- `src/orchestrator/verification_node.py` — skip re-verification of already-passed artifacts
- `src/verification/gate.py` — Tier 2 clearance for Tier 1 HIGHs (per proposal)
- `src/verification/behavioral.py` — `pip_packages` priority + upfront dependency check
- `src/schemas/task.py` — `MatchItem.query` field added
- `tests/evaluation/execution.py` — `execute_mcp_tool_in_chain()` + upstream priority
- `tests/evaluation/harness.py` — `_extract_result_compositional()` + metrics_store threading
- `src/cli/runner.py` — compositional chain execution, spinner, display, output subdirs, timestamps
- `src/cli/display.py` — chain summary, verification notes, dedup, severity labels

---

## What was done in this session (2026-04-10/11)

### Phase 3 core: CompositionalExecutor

Built `src/orchestrator/composition.py`:
- DAG construction via `graphlib.TopologicalSorter` from `SubTask.depends_on`
- Workspace at `/tmp/mint_workspace_<id>/` with `fixtures/` + `step_N_output.json`
- Per-type dispatch: MCP_TOOL (Docker workspace mount), SKILL/AGENT (fixture_contents injection — HP4 solution)
- Metrics recording per step via `InvocationRecord`
- LangFuse `chain-executor` trace with per-step child spans (VERIFIED WORKING in LangFuse)
- Progress callbacks (`on_step_start`, `on_step_end`) for CLI spinner

Wired into harness (`_extract_result_compositional`) and CLI (`_execute_compositional_chain`). Branching condition: `len(subtasks) > 1 and len(artifacts) + len(matches) > 1`.

### Bug fixes discovered during live testing

1. **`sandbox_execute` tool schema** — `workspace_path: Path` broke OpenAI's schema validation (`"format": "path"` rejected). Fixed: separated into `sandbox_execute()` (tool-facing, clean schema) and `sandbox_execute_in_workspace()` (programmatic).

2. **`ClassifiedGap.query` overwrite** (ROOT CAUSE) — classifier set `query=capability_name` (LLM-generated name with hash) instead of `query=gap.query` (original decomposer name). Broke the entire resolution chain: subtask → gap → artifact linkage was severed. Fixed in both normal and fallback paths.

3. **`MatchItem.query` missing** — registry matches had no record of the original query string. Resolution map couldn't link `required_capability` → match. Fixed: added `query` field to `MatchItem`, populated in `gap_detection_node`.

4. **Workspace permissions** — `tempfile.mkdtemp()` creates `0o700` (owner-only). Docker sandbox user couldn't read mounted workspace. Fixed: dirs `0o755`, files `0o644`.

5. **Behavioral test `pip_packages`** — behavioral tests used `resolve_extra_packages` (LLM-based, sometimes wrong: `yaml` instead of `pyyaml`) instead of `artifact.pip_packages` (generator-declared, correct). Fixed: `pip_packages` used first.

6. **Dependency install failures were `MEDIUM`** — tools that can't install their own packages passed verification. Fixed: upfront dependency check at start of behavioral `run()`, reports `HIGH` (blocking) on install failure.

### Verification improvements

7. **Tier 2 clearance** — per proposal "HIGH findings require Tier 2 clearance": Tier 1 HIGH findings are downgraded to MEDIUM if Tier 2 is clean (behavioral testing confirms the code works despite the code smell).

8. **Skip re-verification** — on retry, already-passed artifacts skip all three verification tiers. Saves ~$0.06 and ~360s per retry cycle.

9. **Verification display dedup** — `verification_results` accumulates across retries. Display now deduplicates by latest report per `capability_name`, shows correct counts.

### CLI/display improvements

10. **Chain step spinner** — live `✓`/`✗` per step with capability name, type, source (generated/registry), elapsed time.
11. **No duplicate chain summary** — spinner shows steps during execution; no redundant post-run repeat.
12. **Verification notes** — severity labels (`Critical:`, `Warning:`, `Note:`), per-capability attribution, deduplication with count, max 3 per capability, reference to verification.json.
13. **Output subdirectories** — `mint_output/<cap>_<ts>/output.json`, `verification.json`, `run.json` with labelled save lines.
14. **Footer type summary** — `379.5s | 2 MCP_TOOL, 2 SKILL | 1 attempt(s)` for chains.
15. **Local timestamps** — filenames use `datetime.now()` not `datetime.now(UTC)`.
16. **Streaming display order** — non-generator nodes (classifier, verification) print ✓ immediately, not deferred.
17. **Retry visibility** — verification failures show `✗ N failed, M passed (Tier 3)`.
18. **Verification spinner** — shows artifact count: `⠸ Verifying 4 artifact(s)... (180s)`.

### LangFuse observability

19. **`chain-executor` traces** — parent trace with per-step child spans. Shows capability name, type, source, latency, success, output keys. Uses `Langfuse().start_as_current_span()` pattern. **VERIFIED WORKING** — traces appear in LangFuse with correct session/tags.

### Memory items noted for Phase 3.5a

20. **Sandbox import check** — Tier 1.5 dry-run import in Docker for all artifact types. Noted in `memory/project_sandbox_import_check.md`.

---

## test_mint results (latest run, this session)

Results vary due to LLM non-determinism. Best run was 4/7, latest was 2/7:

| Test | Best result | Failure class |
|---|---|---|
| T01 (CSV) | PASS | — |
| T04 (YAML) | PASS (exact match) | Sometimes SKILL execution returns empty |
| T09 (XML) | FAIL | Registry pollution from earlier tests OR `@mcp.tool()` decorator crash |
| T11 (Excel) | FAIL | Over-parameterized tools / binary fixture handling |
| T14 (TSV) | PASS | Sometimes over-parameterized tools |
| T16 (AGENT) | PASS (correct root cause) | Sometimes agent timeout / registry pollution |

**Failure categories** (all Phase 3.5a scope, not executor bugs):
1. **Over-parameterized tools** — generators produce tools with config params (`quantity_field`, `encoding`) instead of hardcoding from task description
2. **Monolith tools** — each tool solves the entire task instead of its subtask. Confirmed via `mint inspect` — step 1 ("read file") returns `{row_count, column_names}` which is the final answer format
3. **Registry pollution** — test_mint shares a registry across tests; capabilities from earlier tests match later tests' gaps incorrectly
4. **`@mcp.tool()` decorator crash** — FastMCP schema validation fails for some tools at import time

---

## Decisions made during this session

### Confirmed: compositional executor IS proposal-faithful
The decompose → detect gaps → classify → generate → execute-all pipeline only makes sense if all generated capabilities are used. The proposal says capabilities are "connected to the orchestrator for immediate use." The compositional executor is the mechanism. **Do not propose going back to single-artifact execution.**

### Confirmed: DeepAgents subagents needed for context engineering
The compositional executor handles MCP_TOOL/SKILL chains. AGENT chains need the DeepAgents lead-with-subagents pattern (Phase 3.5b) for the Isolate primitive. These are separate mechanisms.

### Confirmed: workspace JSON passing is proposal-aligned
The proposal says "Agents within the pipeline communicate via strictly typed JSON schemas representing the 'world state' at each handoff point." The `step_N_output.json` files are exactly this.

---

## What to do next

1. **Review and commit Phase 3** — all changes are uncommitted. Draft commit message, stage specific files, let Tomasz commit.

2. **Phase 3.5a** — generator prompt discipline (the most impactful fix for test_mint pass rate):
   - Generator prompts must produce step-specific tools, not monoliths
   - Token budgets (MCP_TOOL ~1500 tokens, SKILL.md ~500 lines)
   - Sandbox import check (Tier 1.5) for all three artifact types
   - Over-parameterized tool prevention

3. **Phase 3.5b** — DeepAgents subagents for AGENT execution (context engineering Isolate primitive)

4. **Phase 3.5c** — decomposer rule 10 tightening (prevent over-decomposition)

---

## Things to avoid

All items from the previous handoff still apply. Additionally:

1. **Do not re-implement single-artifact execution.** The compositional executor is proposal-faithful and working. The failures are in generator quality, not executor infrastructure.
2. **Do not duplicate existing tracing patterns.** Use `Langfuse().start_as_current_span()` for custom traces. `CallbackHandler` for LangChain/LangGraph automatic tracing.
3. **The `ClassifiedGap.query` must preserve the original `gap.query`.** This was the root cause bug. Do not set it to the classifier's LLM-generated `capability_name`.
4. **Binary fixtures (.xlsx) exist in the workspace.** The chain executor handles them correctly (copies via `shutil.copy2`). The issue is downstream tools trying to read them as text.
5. **Verification `verification_results` accumulates across retries.** Any display or analysis code must deduplicate by `capability_name` to get accurate counts.

---

## Key operational facts (updated)

- **Unit tests:** 968 passing (939 baseline + 29 new)
- **LLM model:** `openai:gpt-5-mini` everywhere
- **LangFuse chain traces:** `chain-executor` traces appear with per-step spans. VERIFIED WORKING.
- **Output directory:** `data/mint_output/<cap_name>_<ts>/` (subdirectory per run, with `output.json`, `verification.json`, `run.json`)
- **Timestamps:** local time for filenames/logs, UTC for data storage
- **Workspace:** `/tmp/mint_workspace_<id>/` with `fixtures/` (0o755) + `step_N_output.json` (0o644)
- **sandbox_execute vs sandbox_execute_in_workspace:** tool-facing (clean schema) vs programmatic (workspace mount). Never expose `workspace_path` in LLM tool schemas.
