# Security / Robustness Audit — 2026-04-19

**Scope:** Eighteen findings surfaced during a focused audit of `sandbox/`, `scripts/`, `src/`, and `tools/`. Findings #1-#4 logged 2026-04-19; #5-#6 added 2026-04-20 (first extension); #7-#8 added 2026-04-20 (second extension); #9-#11 added 2026-04-20 (third extension); #12-#14 added 2026-04-20 (fourth extension, detail in `security_audit_2026_04_19_ext4.md`); #15-#18 added 2026-04-20 (fifth extension, detail in `security_audit_2026_04_19_ext5.md`). Issue #5's original framing was superseded by #8 — see the note inside the #5 section.
**Status:** All eighteen reproduced and verified against the current `iter/6-execution-backends` tip. **All eighteen fixed 2026-04-20** across the two-session sweep.
**Priority:** P0s were #12, #13, #15, #17, #18 — all eval-blocking (distorted `chain_success` reporting, escaped filesystem writes, false-positive split coverage, T19/T20 drop, corrupted per-cycle utility snapshots). Alongside #6, #7, #8, #9, #10, #11 they shipped before any longitudinal run. #16 was a P1 silent-failure (truncation → invalid JSON); #1, #2, #3, #4, #5, #14 were Tranche 2 (ship-when-convenient) and also shipped in the same sweep.

---

## TL;DR

| # | Title | Severity | Real? | Recommended disposition |
|---|---|---|---|---|
| 1 | Host import executes generated MCP code before any sandbox | High | Yes — verified | ✅ **FIXED 2026-04-20.** `src/orchestrator/loading_node.py` — removed the `loader.load_tool()` + `loader.execute_tool()` calls; production flow now only reads `source_code` into the registry via `register_tool`. `ToolLoader.load_tool` / `execute_tool` remain in `src/orchestrator/tool_loader.py` for unit-test inspection with explicit "not for production" docstring warnings. `CLAUDE.md` "MCP Tool Loading Strategy" paragraph rewritten to match reality (Docker sandbox is the only execution path). Regression guard at `tests/unit/test_loading_node.py::test_does_not_execute_module_top_level_on_host` — real `ToolLoader` + a tool whose top-level writes `pwned` marker file; marker must NOT exist after `loading_node` returns. |
| 2 | Path traversal in `tools/init_skill.py` via unvalidated `skill_name` | Medium | Yes — reproduced locally | ✅ **FIXED 2026-04-20.** `_validate_skill_name` regex (`^[a-z][a-z0-9-]{0,63}$`, matches `quick_validate.py`) + `Path.is_relative_to` containment check. Both layers run before any filesystem write. CLI `main()` surfaces the policy failure with the requirements block. Regression guard at `tests/unit/test_init_skill.py` (20 tests: valid/invalid-name parametrize + traversal + symlink-escape). |
| 3 | `ToolLoader` temp-dir accumulation in long-running drivers | Low | Partial — `atexit` handles exit, but in-run growth is real | ✅ **DOCUMENTED 2026-04-20.** `src/orchestrator/tool_loader.py::ToolLoader` class docstring now spells out the `atexit`-only cleanup contract: temp dirs accumulate for the lifetime of the loader, reclaimed only at process exit; production loaders are process-lifetime-bounded (CLI: one task per invocation; eval harness: one loader per run, bounded by task count). Long-running drivers (daemon mode, future Ch 7 hot-reload) must call `cleanup()` explicitly. Accept + document disposition per audit recommendation; revisit with per-task ownership when daemon mode is added. |
| 4 | `os.system` + missing `try/finally` in `mint retry --edit` | Low/Medium | Yes — verified | ✅ **FIXED 2026-04-20.** `src/cli/app.py::retry` — replaced `os.system` with `subprocess.run(shlex.split(editor) + [tmp_path], check=False)` (shell=False, neutralises metacharacters in `$EDITOR`); wrapped the editor + read block in `try/finally` with `Path.unlink(missing_ok=True)` so the tmp file is removed on every branch including SystemExit(1). Regression guard at `tests/unit/test_cli_retry_edit.py` (6 tests: shell=False, shlex-split flags, metacharacter inertness, cleanup on happy path / editor raise / empty-prompt SystemExit). |
| 5 | `ToolLoader` picks coroutines by `dir()` alphabetical order, not source order | Medium (see note) | Yes — verified; **framing superseded by #8** | ✅ **FIXED 2026-04-20 (as the #1 bundle).** The two offending `dir()` loops live inside `ToolLoader.load_tool` / `load_tool`'s runner script — both are unreachable from production after finding #1 removed the `loading_node` host-import call sites. The methods remain for unit-test inspection with explicit "not for production" docstring warnings. Generated code's entrypoint is now identified via `_find_mcp_tool_function` in all production paths (finding #8). |
| 6 | Package validator regex rejects valid PEP 508 requirement forms; silent fallback masks the cause | Medium | Yes — verified by direct regex replay | ✅ **FIXED 2026-04-20.** `src/verification/sandbox_tool.py::_sandbox_execute_with_install` replaces the narrow regex with `packaging.requirements.Requirement` PEP 508 parsing. All-rejected case: loud-fail dict carrying `pip_requirement_rejected` (no more silent no-install fallback). Partial case: warning log + install on the good subset. `src/verification/import_check.py::_check_mcp_tool` reads `pip_requirement_rejected` and emits a dedicated CRITICAL finding category (not `import_error` / `wrong_pip_package`). Regression guard at `tests/unit/test_sandbox_tool.py` (17 tests: 11 valid-PEP-508 forms + 4 malformed-loud-fail + partial-rejection + import_check finding-category mapping). |
| 7 | `--json` path drops `allow_network` for SKILL/AGENT execution | High | Yes — verified | ✅ **FIXED 2026-04-20.** `src/cli/runner.py` SKILL/AGENT branch factored into a single `_invoke_skill_or_agent` closure — no kwarg can drift between the spinner and JSON paths. Regression guard at `tests/unit/test_cli_allow_network.py::TestAllowNetworkPropagatesAcrossJsonModes` (parametrized across `json_output=True/False`). |
| 8 | Every "which async function is the tool?" call site uses an ordering heuristic; none reads the `@mcp.tool` decorator | High | Yes — reporter's repro (`helper_format_rows` + `summarize_sales`) reproduced verbatim | ✅ **FIXED 2026-04-20.** New `src/verification/behavioral.py::_find_mcp_tool_function` AST-matches the `@mcp.tool` / `@mcp.tool()` decorator on top-level public async defs (handles bare + call-form, ignores nested + private). `_extract_function_name` rewritten as a fallback chain: decorator-first → single-public-async with WARNING → None on ambiguity. Existing 7 call sites (`cli/runner.py`, `coverage.py`, `behavioral.py` self, `harness.py`, `execution.py`, `scripts/test_pipeline.py`) inherit the fix unchanged. Regression guard at `tests/unit/test_behavioral.py::TestFindMcpToolFunction` (10 tests: helper-first + entrypoint-first + alphabetical + bare decorator + unrelated decorators ignored + ambiguity → None + nested skip + private-decorated skip) + `TestExtractFunctionNameFallbackChain` (4 tests: decorator precedence + legacy fallback warning + ambiguity cases). |
| 9 | Compositional chain crashes on registry-matched AGENT via `agent_config` dict/str mismatch | High | Yes — Pydantic `ValidationError` reproduced | ✅ **FIXED 2026-04-20.** New shared `src/schemas/task.py::decode_agent_config_metadata` helper used by all three sites: `src/orchestrator/composition.py::_match_to_artifact` (previously passed the raw metadata string straight to `GeneratedArtifact` and crashed), `src/cli/runner.py::_try_registry_match` (previously had its own inline decoder), and `src/registry/store.py::add_seeded_capability` snapshot construction (previously had a different inline decoder with a `{"system_prompt": str}` fallback — store.py retains that fallback because the tokenization snapshot benefits from a best-effort value even on parse failure). Zero policy drift across the three sites. Regression guard at `tests/unit/test_schemas.py::TestDecodeAgentConfigMetadata` (7 tests: None / empty / dict / valid JSON / malformed / JSON-non-dict / unexpected type) + `tests/unit/test_composition.py::TestMatchToArtifactAgentConfigNormalization` (3 tests: JSON string decoded / malformed → None / missing → None). |
| 10 | CLI returns exit 0 on execution failure via registry-match path | High | Yes — verified by code-path reading | ✅ **FIXED 2026-04-20.** `src/cli/runner.py::execute_task` — when `artifact is not None` but `output` is falsy, check whether the artifact has an executable payload (`source_code` / `skill_content` / `agent_config`). With payload → exit **6** (new code, "execution failure after successful pipeline"); without payload → exit 0 (genuine seeded-descriptor case). `CLAUDE.md` three-layer strict-gate table extended with the new code. Regression guard at `tests/unit/test_cli_exit_codes.py::TestCliExitCodeDiscrimination` (4 tests: happy path → 0 / payload-failed → 6 / no-payload → 0 / no-artifact-no-matches → 1). |
| 11 | Non-dict MCP outputs crash output-saving; falsey outputs silently coerced to `{}` at three sites | Medium-High (thesis integrity) | Yes — both halves reproduced | ✅ **FIXED 2026-04-20.** New shared `src/cli/output_coerce.py` with two helpers: `extract_tool_result(parsed)` (sentinel-based key-presence check — preserves falsy values; returns `{}` only when the ``result`` key is absent entirely) + `as_dict_for_save(output)` (wraps non-dict outputs as `{"_result": output}` so the per-key scan doesn't crash). Applied at all three executor-return sites (`src/cli/runner.py::_execute_mcp_tool`, `tests/evaluation/execution.py::execute_mcp_tool_in_chain`, `tests/evaluation/harness.py`) + `_save_output`'s per-key scan + the save-dispatch truthy check (`if output:` → `if output is not None:`). Regression guard at `tests/unit/test_output_coerce.py` (25 tests: parametrized falsy values preserved / truthy values preserved / missing key returns `{}` / `None` vs missing-key distinction / dict pass-through / non-dict wrapping). |
| 12 | Compositional DAG is fail-open — invalid `depends_on` indices passed through, cycle fallback silently linearizes, phantom steps report success | **P0** | Yes — direct graphlib replay: `[99]` → IndexError, `[-1]` → phantom last-subtask execution reporting chain_success=True, cycle → linear fallback reporting success | ✅ **FIXED 2026-04-20 (three-layer strict-gate).** **Layer (A) — decomposition validator:** new `src/orchestrator/decomposition.py::_validate_depends_on_indices` checks out-of-range, self-loop, and cycle BEFORE the coherence validator; violations flip `decomposition_succeeded=False`, emit a `decomposition.invalid_dag` LangFuse event, and trigger the 3x retry budget. Matches the §12 finding #15 strict-gate pattern (retry exhaustion → CLI exit 2). **Layer (B) — composition:** `build_execution_order` returns `None` on the same three violation classes (defence-in-depth; pre-sort bounds check runs before `graphlib.TopologicalSorter` because graphlib silently treats nonexistent predecessors as no-predecessor nodes — the root cause of the `[-1]` phantom-execution bug). **Layer (C) — run loop:** implicit — `run()` short-circuits to `ChainResult(chain_success=False, failed_step_reason="Invalid compositional DAG...")` when (B) returns `None`. Regression guards: `tests/unit/test_decomposition.py::TestValidateDependsOnIndices` (9 tests: valid DAG / empty / out-of-range / negative / self-loop / two-cycle / three-cycle / diamond valid / multiple violations) + `tests/unit/test_composition.py::TestBuildExecutionOrder` (10 tests: single/empty/linear/diamond/no-deps + 5 failure cases + end-to-end run() fails closed). |
| 13 | Output persistence writes outside the run directory via three unsanitized path joins | **P0** | Yes — `pathlib` repro: `cap_name='../escape'`, output key `'../escape-key'`, and `output_files` filename `'../escape.bin'` all escape `mint_output/<run>/` | ✅ **FIXED 2026-04-20.** New `src/cli/output_coerce.py` helpers: `_sanitise_component(raw, fallback)` (drops characters outside `[A-Za-z0-9._-]`, rejects pure-dots, falls back on empty) + `contained_join(base, *parts, fallback)` (sanitise-then-join-then-resolve-and-assert-containment via `Path.is_relative_to`). Applied at all three `_save_output` sites: `run_dir` uses `_sanitise_component(cap_name)`; per-key output files use `contained_join(run_dir, f"{key}{ext}")`; `output_files` chain writes use `contained_join(run_dir, fname)`. Regression guard at `tests/unit/test_output_coerce.py::TestSanitiseComponent` (6 tests: plain/dots-hyphens/separators-dropped/pure-dots/empty/metacharacters) + `TestContainedJoin` (6 tests: plain join / traversal contained / output-key escape / output_files escape / absolute-path-as-part / nested traversal). |
| 14 | `sandbox_execute_in_workspace` drops the workspace mount in its regex-reject fallback | P1 | Yes — fallback at sandbox_tool.py:354 calls `sandbox_execute` (no workspace param) when Issue #6's regex rejects all packages | ✅ **FIXED 2026-04-20 (as side-effect of #6).** The old `return sandbox_execute(code, timeout, allow_network=...)` fall-through that dropped `workspace_path` was replaced by the #6 loud-fail dict (`pip_requirement_rejected`). No more workspace-dropping code path exists in `src/verification/sandbox_tool.py`. Confirmation test at `tests/unit/test_sandbox_tool.py::TestSandboxExecuteInWorkspaceLoudFailsOnInvalidPackages` locks the invariant. |
| 15 | Coverage verifier marks semantically-identical outputs as "handled" due to dict key-order asymmetry | **P0** | Yes — direct replay: parent `sort_keys=True`, variant no sort_keys → same-content dicts compare unequal → handled=True | ✅ **FIXED 2026-04-20.** `src/verification/coverage.py::_build_replay_code` — added `sort_keys=True` to the variant-side `json.dumps(result, ...)` so both sides serialise identically. Defence-in-depth: new `_semantic_equal` helper at the wrong-output compare site parses JSON on both sides before `==` so key-order drift / int-vs-float / whitespace differences all compare equal. Regression guard at `tests/unit/test_coverage.py::TestWrongOutputCase_SemanticEquality` (4 tests: key-order rejected / int-vs-float equal / nested key-order / genuinely-different still handled). Pre-fix direction of bias was pro-optimizer (false-positive split coverage). |
| 16 | `serialize_for_invocation` truncation produces invalid JSON | P1 | Yes — `json.loads` on truncated string raises `JSONDecodeError`; replay treats the case as unreproducible | ✅ **FIXED 2026-04-20.** `src/registry/metrics.py::serialize_for_invocation` — oversize payloads now emit a valid-JSON sentinel `{"__truncated__": true, "__original_size_bytes__": N}` instead of the broken `f'{head}..."__TRUNCATED__"'` literal. `src/verification/coverage.py::_variant_handles_case` detects the sentinel AND tolerates legacy invalid-JSON rows — both report "unreproducible" without invoking the sandbox. Regression guard at `tests/unit/test_metrics.py::test_oversize_payload_truncates_to_valid_json_sentinel` + `tests/unit/test_coverage.py::TestTruncationSentinelHandling` (2 tests: sentinel / legacy). |
| 17 | `analyze_results.py` drops T19 and T20 from task-level analysis | **P0 for Ch 6** | Yes — `range(1, 19)` yields `T01..T18`; T19 + T20 silently excluded from every per-task table | ✅ **FIXED 2026-04-20.** `scripts/analyze_results.py` — replaced hard-coded `ALL_TASK_IDS = [f"T{i:02d}" for i in range(1, 19)]` with an import from the single source of truth: `[t.task_id for t in ALL_TASKS]`. Every downstream construct (`SYNTHESIS_TASK_IDS`, iteration loops, classification accuracy, Gini) auto-includes T19 (exploratory-AGENT) and T20 (policy-SKILL) — the two tasks Chapter 6's per-type analysis most depends on. Regression guard at `tests/unit/test_analyze_results.py::test_all_task_ids_match_testbed` + `test_synthesis_task_ids_include_t19_and_t20`. |
| 18 | Longitudinal analyzer keys utility/events by `task_id` only, corrupting per-cycle snapshots when tasks repeat | **P0** | Yes — replay: two cycles of T01 both read the merged/last-write-wins dict, so cycle 1 reports cycle 5's utility scores | ✅ **FIXED 2026-04-20.** `scripts/analyze_longitudinal.py::_load_cycle_data` — replaced `utility_by_task_id` / `events_by_task_id` dicts keyed by task_id alone with per-cycle lists bucketed by timestamp window (same pattern as the existing `cycle_increment` loop). Each cycle's snapshot now reads ONLY the utility/event rows that fell within its `task_boundaries` window. `src/registry/evaluation_store.py` — `record_cycle_utility_scores` and `record_cycle_events` gained an optional `occurred_at` parameter so tests can drive deterministic per-cycle bucketing. Regression guard at `tests/unit/test_analyze_longitudinal.py::TestPerCycleIsolationAcrossTaskRepetitions` (2 tests: utility isolation / event isolation — both insert T01 twice with distinct timestamps + distinct utility/event rows and assert cycles don't merge). Pre-fix direction of bias was pro-"graceful-adaptation" in Step 18.8 shift-detection verdicts. |

The common thread across #1, #2, #4, #5, #8, #9, #11-half-A, #12, and #13: each site bridges a type, correctness, or filesystem boundary (LLM-generated code, user-provided skill names, user-controlled `$EDITOR`, LLM-generated module layout, registry-serialized agent config, non-dict tool outputs, LLM-chosen dependency indices, LLM-chosen output key names) without validating or normalising at the boundary, relying instead on an invariant enforced somewhere else in the codebase. When the invariant doesn't hold — merge output, seeded AGENT row, scalar-valued tool result, hallucinated dependency index `[99]`, output key `"../pwn"` — the failure surfaces as a crash, as a silent semantic shift (phantom-step "success"), or as a filesystem side effect outside the intended directory, not at the site that violated the invariant.

#6, #7, #10, #11-half-B, #12 (cycle fallback + linear-order), and #14 are a related but distinct shape: **silent-degradation paths** where a narrow guard (regex; default kwarg; exit-code fallback; falsey coercion; cycle→linear fallback; package-reject→no-workspace fallback) demotes the caller's intent and continues execution without surfacing the demotion in the output. Each converts a "we couldn't do what you asked" into either a "this probably doesn't matter" or "we did something else that looks similar." They contaminate the failure-mode attribution the thesis's Pillar 2 argument depends on.

Fixing all fourteen brings the implementation in line with claims already made in `CLAUDE.md` and the thesis proposal about sandbox isolation, Pillar 2 failure attribution, Step 14.12's offline-by-default contract, and §12 finding #14/#15/#16's "no silent fallbacks anywhere" strict-gate commitment.

---

## Issue #1 — Host import executes generated MCP code (High)

### Summary

`ToolLoader.load_tool()` calls `spec.loader.exec_module(module)` in the current Python process. Any top-level statement in the generated source runs on the host, in-process, *after* the three-tier gate has signed off the artifact. The subsequent "smoke test" in `ToolLoader.execute_tool()` is a plain `subprocess.run([sys.executable, ...])` — **not** the Docker `sandbox_execute` backend. Only at actual invocation time (via `src/cli/runner.py::_execute_mcp_tool`) does Docker enter the picture.

This contradicts the CLAUDE.md architectural statement:

> Generated MCP tools are loaded via `importlib` dynamic import in a sandboxed subprocess (`src/orchestrator/tool_loader.py`).

That sentence is aspirational, not descriptive. `load_tool` runs on the host; `execute_tool` runs in a plain Python subprocess with the same uid, filesystem, network, and environment as the parent.

### Where

- `src/orchestrator/tool_loader.py:103` — `tempfile.mkdtemp(...)` + `write_text(artifact.source_code)`
- `src/orchestrator/tool_loader.py:122` — `spec.loader.exec_module(module)` (host import)
- `src/orchestrator/tool_loader.py:211` — `subprocess.run([sys.executable, ...])` (plain subprocess, not Docker)
- `src/orchestrator/loading_node.py:101` — `loader.load_tool(artifact)` (unconditional after verification)
- `src/orchestrator/loading_node.py:115` — `loader.execute_tool(loaded, inputs={})` (smoke test, plain subprocess)

### Reproduction

A generated tool whose source contains a top-level side effect — e.g. `Path("/tmp/pwned").write_text("fired at import")` outside any function body — will create the marker file during `loader.load_tool(artifact)` before the smoke test ever runs. The Tier 1 static scanner does not AST-reject arbitrary top-level statements; it looks for specific categories (mocks, hardcoded values, external imports like `requests`, placeholder comments). A top-level file write, `os.environ` mutation, socket bind, or recursive-fork bomb passes Tier 1 cleanly.

### Why the three-tier gate does not cover this

| Tier | What it runs | Where |
|---|---|---|
| 1 | AST + regex on source text | No exec |
| 1.5 | `python -c "import tool"` with `pip_packages` | **Docker** |
| 2 | Behavioral probing — calls the tool function | **Docker** via `sandbox_execute` |
| 3 | Adversarial DeepAgent — crafts inputs, invokes tool | **Docker** via `sandbox_execute` |

Tier 1 doesn't execute. Tiers 1.5/2/3 do execute, but all in Docker. **After** all of them pass, `loading_node` reaches for the host-import path for a redundant smoke test. The only Python process that ever runs generated code outside Docker is `loading_node` itself.

### Threat model

The immediate attacker is not a remote adversary — it is the LLM itself (or a prompt-injecting input that steers the generator). The blast radius is still real:

- LLM prompt injection via task text or fixture file contents could coax the generator into emitting top-level `subprocess`, `socket`, or file-I/O statements.
- A benign-but-buggy LLM output (e.g., "warming up" a cache at import time) can silently write to `/tmp`, mutate `sys.path`, or open sockets on the developer machine.
- Thesis claim-vs-code mismatch: Chapter 4 (verification) frames Pillar 2 as the trust boundary; if a capability can side-effect the host before it is registered, that boundary leaks.

### Remediation options

**A. Remove the host-import smoke test from `loading_node` entirely (recommended).**
Registration reads from `artifact.source_code` + AST-extracted schemas; it does not need a live module object. Runtime uses `sandbox_execute`, which imports the code inside Docker. The smoke test in `load_tool` is doubly redundant — Tier 1.5's Docker import probe already catches import-time failures, and Tier 2 behavioral testing already exercises the function with empty/edge inputs.

Concretely: `loading_node` drops the `loader.load_tool(...)` + `loader.execute_tool(loaded, inputs={})` block, keeps only `loader.register_tool(artifact, registry)`. `ToolLoader.load_tool` + `execute_tool` remain in the module for the unit tests that exercise them directly but are no longer called from production flows.

- **Pro:** eliminates the host-execution path entirely; aligns code with the CLAUDE.md sentence.
- **Pro:** drops a duplicate of Tier 1.5's job.
- **Pro:** the "host_loadable" telemetry count becomes moot — it is currently only a log line.
- **Con:** loses the "the module at least parses into a real Python object on this interpreter" check. Mitigated by Tier 1.5 running the same import in Docker with the declared `pip_packages`.

**B. Move the smoke test into Docker.**
Replace `loader.execute_tool(loaded, inputs={})` with a `sandbox_execute` call that executes the function with empty inputs. But this is exactly what Tier 2 already does — so Option A (remove) is strictly simpler.

**C. Tighten Tier 1 to reject non-trivial module top-level.**
Extend the static scanner to flag any module-level statement outside `{Import, ImportFrom, FunctionDef, AsyncFunctionDef, ClassDef, Assign-of-literal, Expr-of-docstring, If __name__ == "__main__"}`. This is defense-in-depth: even if a future code path reintroduces host import, Tier 1 would reject the payload first.

- **Pro:** tightens the conformance window to "FastMCP pattern only".
- **Con:** genuine legitimate top-level init (e.g., `mcp = FastMCP(...)` — which is already required by the FastMCP pattern) must be explicitly whitelisted. Needs careful AST classification to avoid false positives.
- **Con:** does not close the gap by itself if Option A is skipped — a regex-free statement (e.g., dotted-attribute mutation) can still slip through an AST whitelist unless the whitelist is very tight.

**D. Sandbox the host import with `RestrictedPython` / seccomp / resource limits.**
Overkill for the threat surface here, and fragile. Not recommended.

### Recommendation

Ship **A + C together**. Remove the host-import path from `loading_node` (A) so the CLAUDE.md claim becomes literally true, and add the Tier 1 module-top-level whitelist (C) as defense-in-depth and as a Chapter 4 robustness-narrative nugget. Test coverage: reuse the existing `test_tool_loader.py` suite against `load_tool` directly; add a single `test_loading_node.py::test_does_not_import_module_into_host_process` case that registers an artifact whose top-level writes a marker file and asserts the marker is absent after `loading_node` returns.

---

## Issue #2 — Path traversal in `tools/init_skill.py` (Medium)

### Summary

`skill_name` is joined into a base path with no validation. `Path(path).resolve() / skill_name` resolves only the base; `..` components in `skill_name` are not normalized until the caller (here `mkdir(parents=True)`) resolves them again at filesystem level. A `skill_name = "../escaped"` creates the directory one level above `--path`.

### Where

- `tools/init_skill.py:210` — `skill_dir = Path(path).resolve() / skill_name`
- `tools/init_skill.py:295` — `skill_name = sys.argv[1]` (CLI entry, no validation)

### Reproduction

```python
from tools.init_skill import init_skill
from pathlib import Path

base = Path("/tmp/audit_repro/base")
base.mkdir(parents=True, exist_ok=True)
init_skill("../escaped-skill", str(base))
# Creates /tmp/audit_repro/escaped-skill/ with SKILL.md, scripts/, references/, assets/
# /tmp/audit_repro/base/ stays empty.
```

Verified on tip of `iter/6-execution-backends`.

### Threat model

The tool is developer-facing and is not wired into the `mint` CLI or any LLM-driven generator. The production generator path (`src/orchestrator/generators/skill.py`) writes `SKILL.md` directly via `set_skills_output_dir()` and does not call `init_skill.py`. So the immediate exploit vector is "a human or an LLM-authored script invokes `tools/init_skill.py` with a crafted name."

Still worth fixing because:

- `quick_validate.py` (the sibling tool) *does* enforce hyphen-case + max 64 chars — `init_skill.py` should share the same policy so the two are consistent.
- The CLAUDE.md "DeepAgents Skill Ecosystem" section advertises those name constraints as invariants.
- If a future path (e.g., a `mint skill init` subcommand) ever exposes this to user input or LLM output, the escape becomes live.

### Remediation

Two layers, both cheap:

**Layer 1 — Regex whitelist matching the documented policy.**

```python
import re

_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

def init_skill(skill_name: str, path: str):
    if not _SKILL_NAME_RE.fullmatch(skill_name):
        raise ValueError(
            f"Invalid skill_name {skill_name!r}: must be hyphen-case "
            f"(lowercase letters, digits, hyphens; 1-64 chars; lead with letter)."
        )
    ...
```

**Layer 2 — Containment check after `resolve()`.**

```python
base = Path(path).resolve()
skill_dir = (base / skill_name).resolve()
if not skill_dir.is_relative_to(base):
    raise ValueError(
        f"Resolved skill dir {skill_dir} is not under --path {base}"
    )
```

`is_relative_to` needs Python 3.9+ (already a project baseline). Keep both layers: Layer 1 gives a clear user-facing error on the common case (typo, wrong case); Layer 2 defends against exotic inputs that slip the regex (e.g., a future policy change that accidentally allows `.`).

Also: the CLI `main()` at line 278 should surface the regex failure with the same "Skill name requirements" block it already prints on usage error, so the error message stays consistent.

### Recommendation

**Fix.** Both layers together, plus one unit test asserting `init_skill("../escaped", tmp_path)` raises `ValueError` *before* any directory is created.

---

## Issue #3 — `ToolLoader` temp-dir accumulation (Low)

### Summary

`ToolLoader.__init__` registers `atexit.register(self.cleanup)`. Every `load_tool` call appends to `self._temp_dirs`. During a long-running driver that reuses one loader across N tasks, `_temp_dirs` grows unboundedly until process exit — at which point `atexit` removes them all. Nothing leaks across process boundaries; the "leak" is in-run only.

### Where

- `src/orchestrator/tool_loader.py:79` — `atexit.register(self.cleanup)` (at `__init__`)
- `src/orchestrator/tool_loader.py:103` — `tempfile.mkdtemp(prefix="jit_mcp_")` + append to `_temp_dirs`
- `src/orchestrator/tool_loader.py:373` — `cleanup()` implementation (`shutil.rmtree` per dir)

Callers that reuse one loader across many tasks:

- `src/cli/runner.py:567` — one loader per `mint run` invocation; the process exits after one task, `atexit` fires. No accumulation in practice.
- `scripts/run_longitudinal.py:132` — one loader across N tasks (default 400). Accumulates N temp dirs during the run.
- `scripts/run_longitudinal_shift.py:251` — same shape as above.
- `tests/evaluation/harness.py:324` — one loader per run; N tasks per run.

### Why the original report overstates the severity

- `atexit` guarantees cleanup at process exit in every caller. Nothing survives the process.
- Each leaked directory is one `.py` file (the generated source) plus any still-un-unlinked runner scripts. The per-call runner script at `tool_loader.py:226` is already `unlink(missing_ok=True)`-ed in a `finally` block, so the residue is only the source `.py`.
- For a 400-task evaluation run, on-disk pressure is ~400 small files (~few KB each) in `/tmp/jit_mcp_*` — well under any practical disk-quota ceiling on the target hardware.
- `_temp_dirs` is a `list[Path]` — 400 entries in memory is trivial.

The only pathological shape is: (a) a long-running daemon that never exits, or (b) a driver that hits a local `/tmp` quota. Neither is in scope for the thesis eval.

### Remediation options

**A. Accept + document (recommended).**
Add a line to the class docstring making the `atexit` contract explicit: "Temp dirs accumulate for the lifetime of the loader and are removed via `atexit`. Long-running drivers that want bounded disk use must call `cleanup()` explicitly." No behavior change.

**B. Per-task loader in long drivers.**
Change `run_longitudinal.py` and `run_longitudinal_shift.py` to construct a fresh `ToolLoader()` per task and call `.cleanup()` at the end. Side effect: each `__init__` registers another `atexit` handler. `atexit`'s handler list grows unboundedly instead of `_temp_dirs` — same shape, different bucket. Would need `atexit.unregister(self.cleanup)` in `cleanup()` to avoid.

**C. Explicit per-task cleanup in the driver.**
Keep the single loader but call `loader.cleanup()` at end-of-task. This invalidates any `LoadedTool` instances still holding `temp_path` references — the object still looks live but the file beneath it is gone. Safe only if no `LoadedTool` outlives the task (currently the case, since `load_tool`'s output is consumed inside `loading_node` and discarded). Requires an ownership invariant documented in the docstring.

**D. Keep-last-K heuristic.**
On each `load_tool`, if `len(self._temp_dirs) > K`, rmtree the oldest and drop from the list. Simple but has the same liveness-invalidation risk as C.

### Recommendation

**Accept + document (A)** for the thesis timeline. The in-run growth is bounded, the disk impact is negligible at 400 tasks, and `atexit` closes the window at process exit. If a daemon mode is later added (Chapter 7 future work), revisit with Option C + an explicit ownership contract.

---

## Issue #4 — `os.system` + tmp leak in `mint retry --edit` (Low/Medium)

### Summary

`src/cli/app.py:780` shells out to `$EDITOR` via `os.system`, interpolating both the editor path and the temp file path into a single shell command string. Environment-variable shell injection is the risk; the temp file cleanup is not guarded by `try/finally`, so an exception between `os.system` and `os.unlink` leaks the file to `/tmp`.

### Where

```python
# src/cli/app.py:770-785
if edit:
    import os
    import tempfile

    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, prefix="mint_"
    ) as f:
        f.write(task_text)
        tmp_path = f.name
    os.system(f'{editor} "{tmp_path}"')
    task_text = Path(tmp_path).read_text().strip()
    os.unlink(tmp_path)
    if not task_text:
        err_console.print("[red]Empty prompt — aborting.[/red]")
        raise SystemExit(1)
```

### Threat model

- `$EDITOR` is the invoking user's own environment. Not a privilege-escalation vector — it is already inside the user's shell trust boundary.
- Real risks: (a) accidental quoting/escaping bugs when `$EDITOR` contains flags (e.g., `EDITOR='emacs -nw'`) — `os.system(f'emacs -nw "{path}"')` happens to work only because the shell re-parses; (b) fails static-analysis (`bandit B605`, `semgrep python.lang.security.audit.dangerous-system-call`); (c) tmp file leaks to `/tmp` whenever the editor subprocess errors or `read_text()` raises or the empty-prompt `SystemExit(1)` branch fires.
- `tmp_path` itself is safe input — it comes from `tempfile.NamedTemporaryFile`, which produces a controlled name. So the `"{tmp_path}"` interpolation is not the hole; `$EDITOR` is.

### Remediation

Replace with `subprocess.run` (shell=False) and wrap in `try/finally`:

```python
import shlex
import subprocess

editor = os.environ.get("EDITOR", "vi")
cmd = shlex.split(editor) + [tmp_path]

try:
    subprocess.run(cmd, check=False)
    task_text = Path(tmp_path).read_text().strip()
finally:
    Path(tmp_path).unlink(missing_ok=True)

if not task_text:
    err_console.print("[red]Empty prompt — aborting.[/red]")
    raise SystemExit(1)
```

Notes:

- `shlex.split(editor)` handles `EDITOR='emacs -nw'` correctly — it splits into `['emacs', '-nw']` and passes each as a separate argv entry. No shell re-parse.
- `shell=False` (the `subprocess.run` default with a list argument) means `EDITOR='vi; rm -rf ~'` becomes `argv[0] = 'vi;'` and exec fails harmlessly, instead of the shell running two commands.
- `check=False` preserves current behavior: editor exit codes don't abort retry.
- `finally` guarantees cleanup on every branch, including the `SystemExit(1)` empty-prompt case.
- `missing_ok=True` guards against the editor having deleted the file (rare but possible with certain editor plugins).

### Recommendation

**Fix.** Trivial diff, closes a bandit/semgrep finding, closes a real tmp-file leak in an uncommon-but-reachable branch. One unit test with `EDITOR=/bin/true` on `tmp_path` asserting cleanup ran even when the file was empty on exit.

---

## Issue #5 — `ToolLoader` picks coroutines by `dir()` alphabetical, not source order (Medium — framing superseded by #8)

> **Superseded-by note (2026-04-20, second extension):** the original framing below — "five of six call sites use source order; ToolLoader's two use alphabetical" — was based on the assumption that source order (`ast.walk`) is the correct answer. Issue #8 showed source order is also wrong whenever a helper is defined before the entrypoint in the file (reporter's repro reproduced verbatim). The correct fix is **not** to unify on `_extract_function_name`; it is to AST-match the `@mcp.tool` decorator. See Issue #8 for the revised bundle. The section below is preserved for context and for the architectural-coupling observation, which remains valid: ToolLoader's two sites disagree with the rest of the codebase by method, and that disagreement would hide a regression the day the single-async-def invariant breaks.

### Summary

Two sibling loops in `ToolLoader` select "the tool function" by iterating `dir(module)` and breaking on the first public async coroutine they find. `dir()` returns attributes sorted alphabetically, so a module with `async def apple_helper(...)` and `async def zebra_tool(...)` binds `tool_fn = apple_helper` — the helper, not the entrypoint. Every other call site in the codebase that answers the same question ("which async function is the tool?") uses `ast.walk`, which returns source order. This is a hidden architectural inconsistency whose impact today is limited only because one upstream validator happens to enforce the invariant the loader silently depends on.

### Where

- `src/orchestrator/tool_loader.py:131` — `for attr_name in dir(module): … break` (host import path)
- `src/orchestrator/tool_loader.py:185` — identical loop, re-emitted into the subprocess runner script

### Reproduction

```python
import ast

src = """
async def zebra_tool():
    return 1

async def apple_helper():
    return 2
"""
ns = {}
exec(compile(src, "<t>", "exec"), ns)

print("dir() alphabetical:", [a for a in sorted(ns) if not a.startswith("_")])
# → ['apple_helper', 'zebra_tool']    — ToolLoader picks apple_helper

tree = ast.parse(src)
for n in ast.walk(tree):
    if isinstance(n, ast.AsyncFunctionDef):
        print("ast.walk first async:", n.name); break
# → zebra_tool                        — every other call site picks zebra_tool
```

Confirmed on tip of `iter/6-execution-backends`. The disagreement fires for any module where the public async functions' names don't happen to be in source order alphabetically.

### Why this is latent today, not already firing

`src/orchestrator/generators/mcp_tool.py::_validate_code` (lines 156-167) counts public top-level async defs via `ast.iter_child_nodes` and rejects any module with `async_def_count != 1`. The repair pass re-validates. Private helpers (`_`-prefixed) don't count and are also skipped by the `dir()` loop's `startswith("_")` filter. With exactly one public async def, alphabetical and source order trivially agree — the bug cannot manifest.

So the generator path is safe **by contract between two files that don't reference each other**. The footgun is in the coupling, not the behavior.

### The architectural inconsistency (what the audit actually found)

Six call sites across the codebase ask "which async function in this source is the tool?":

| Call site | Method | Order |
|---|---|---|
| `src/orchestrator/tool_loader.py:131` (host import) | `dir(module)` | **alphabetical** |
| `src/orchestrator/tool_loader.py:185` (subprocess runner) | `dir(tool_module)` | **alphabetical** |
| `src/verification/behavioral.py::_extract_function_name` | `ast.walk` | source order |
| `src/cli/runner.py::_execute_mcp_tool` | `_extract_function_name` | source order |
| `tests/evaluation/execution.py::execute_mcp_tool_in_chain` | `_extract_function_name` | source order |
| `tests/evaluation/harness.py` eval path | `_extract_function_name` | source order |

Five of six use source order. The two that don't both live inside `ToolLoader`. The generator's `_validate_code` is the only thing keeping these two sites aligned with the other five.

### Threat model

Three paths could break the single-async-def invariant and light the bug up:

1. **Pillar 3 Merge.** `src/optimizer/operations.py::_merge_tools` hands `MergeToolsResult.combined_source_code` to `verify_and_register_or_rollback`. The gate's Tier 1 static scanner does not AST-count public async defs; nothing between the merge LLM and `registry.add` enforces single-public-async-def. If the merge LLM emits helper + entrypoint both public, the artifact lands in the registry with both defined. Today that artifact is invoked through `sandbox_execute` (source-order selection via `_extract_function_name`) — so the merged artifact runs correctly at CLI time. But if *any* future flow wires it through `ToolLoader.load_tool` (e.g., a daemon-mode hot reload, a hypothetical Pillar-3 smoke-test stage, or a debug script), the smoke test executes the wrong function.
2. **Pillar 3 Split.** Same shape: `SplitToolsResult` variants carry `source_code` that passes through the gate and lands in the registry. No per-variant single-async-def check.
3. **Regression of `_validate_code`.** A future refactor that loosens the generator's "exactly 1 public async def" to "at least 1" (a plausible change if a Chapter-7 feature allows public helpers) silently breaks the alphabetical-selection site without any compile-time signal.

For each, the failure mode is the same: the gate passes (it exercises the source-order-first function and that one works), then `ToolLoader` binds to a different function and executes it with empty inputs during smoke test. If the alphabetically-first function happens to be a destructive helper — deleting fixture files, POSTing to a logging endpoint, mutating shared state — the side effect fires. It is exactly the class of "the sandbox is tighter than you think, but the thing that runs first isn't sandboxed" bug that Issue #1 is also about.

### Coupling with Issue #1

Issue #1's recommended fix is to remove the host-import smoke test from `loading_node`, because Docker-based Tier 1.5 / Tier 2 already cover the ground. If that fix ships, both of `tool_loader.py:131` and `tool_loader.py:185` become dead code — `load_tool` and `execute_tool` have no production callers. Issue #5's fix is then subsumed: the two alphabetical sites are deleted with the path.

If Issue #1 is deferred or rejected, Issue #5 still wants a fix in place: unify function selection so `ToolLoader` matches the rest of the codebase.

### Remediation options

**A. Bundle with Issue #1 (recommended).**
Delete `ToolLoader.load_tool` + `ToolLoader.execute_tool` (or keep them for unit-test inspection only, clearly marked as not-for-production). `loading_node` stops calling them. Issue #5's two offending lines go away. Zero risk.

**B. Standalone fix — unify on `_extract_function_name`.**
If Issue #1 is deferred: replace both `dir()` loops with an import from `src.verification.behavioral`:

```python
from src.verification.behavioral import _extract_function_name

func_name = _extract_function_name(artifact.source_code)
if func_name is None:
    raise RuntimeError(...)
tool_fn = getattr(module, func_name)
```

And for the runner script at line 185, embed the resolved name directly into the runner template instead of re-doing the `dir()` walk at runtime:

```python
runner_code = textwrap.dedent(f"""\
    import asyncio, json, sys
    sys.path.insert(0, {str(loaded.temp_path.parent)!r})
    import {loaded.temp_path.stem} as tool_module
    fn = getattr(tool_module, {func_name!r})
    …
""")
```

Cost: ~20 LOC, one unit test with a deliberately adversarial module layout (e.g., the `zebra_tool` / `apple_helper` repro above).

**C. Harden the gate instead of fixing the loader.**
Port `_validate_code`'s `async_def_count != 1` check into the Tier 1 static scanner, so merge/split/future-flow artifacts can't slip multi-public-async-def modules past verification. This keeps the loader bug latent but eliminates every known path that could trigger it.

- **Pro:** closes all three threat paths at once.
- **Con:** may false-reject LLM outputs that are otherwise fine — e.g., a merge that legitimately merges two entrypoints by keeping both public. The generator's narrow "exactly 1" is a FastMCP-pattern constraint; merge/split are arguably not FastMCP code any more, just "valid Python with one callable we want to invoke."
- **Con:** doesn't fix the architectural inconsistency — `ToolLoader` remains the odd one out by method, even if inputs are now forced to make it agree.

**D. Do both B and C (belt-and-braces).**
Unify selection (B) so the loader behaves like the rest of the codebase; also port the gate check (C) as defense in depth for the "multiple public async defs" case where humans would disagree on which one is "the tool."

### Recommendation

**Updated 2026-04-20 (second extension).** Original recommendation (Option A or B depending on #1) was predicated on `_extract_function_name` being correct. It isn't — see Issue #8. The revised plan:

1. **Land #8 first** — replace `_extract_function_name` (and all seven call sites) with decorator-aware selection.
2. **Then either #1 bundle (removes ToolLoader host path, #5 sites evaporate) or #5 standalone Option B but delegating to the new `_find_mcp_tool_function` instead of `_extract_function_name`.**

The key test from the original recommendation still applies — a `test_tool_loader.py` case with `zebra_tool` + `apple_helper` asserting the correct selection — but the assertion is now "the `@mcp.tool`-decorated function is selected regardless of source or alphabetical order."

---

## Issue #6 — Package validator false-rejects valid PEP 508 forms; silent fallback hides the cause (Medium)

### Summary

`_sandbox_execute_with_install` filters the caller's `packages` list through a narrow regex, silently drops any non-matching entry, and on total-rejection falls back to `sandbox_execute` without installing anything. The regex rejects three common PEP 508 forms the LLM generator is allowed to produce (`pandas>=2.0,<3` compound versions, `pydantic[email]` extras, `requests ; python_version > "3.8"` environment markers). A filtered-out package is never installed; the sandbox then runs the code against the base image only; the code fails with `ModuleNotFoundError`; the verification report blames the tool's source code, not the dropped dep. Both Tier 1.5 (import probe) and Tier 2/3 (behavioral / adversarial) inherit the same failure attribution.

### Where

- `src/verification/sandbox_tool.py:347` — the validator regex
- `src/verification/sandbox_tool.py:348` — `packages = [p for p in packages if _VALID_PKG.match(p)]` (silent filter)
- `src/verification/sandbox_tool.py:349-354` — `if not packages: return sandbox_execute(code, ..., allow_network=allow_network)` (silent fallback)

The regex verbatim:

```python
_VALID_PKG = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?([<>=!~]+[A-Za-z0-9.*]+)?$")
```

### Reproduction

Direct regex replay on the current tip:

```
ACCEPT  'pandas'
ACCEPT  'pandas>=2.0'
REJECT  'pandas>=2.0,<3'        ← reporter's example #1 — PEP 508 compound version
REJECT  'pydantic[email]'        ← reporter's example #2 — PEP 508 extras
ACCEPT  'scikit-learn'
ACCEPT  'Flask-Login'
ACCEPT  'zope.interface'
ACCEPT  'package~=1.2'
REJECT  'requests ; python_version > "3.8"'   ← PEP 508 environment markers
REJECT  'pkg[a,b]>=1.0'                       ← PEP 508 extras + version
ACCEPT  'numpy==1.24.*'
```

All three rejected forms are documented PEP 508 (what `pip install` on the command line accepts verbatim).

### Two distinct failure modes

Line 348: `packages = [p for p in packages if _VALID_PKG.match(p)]`.

**Mode A — partial rejection.**
Caller passes `['pandas>=2.0,<3', 'numpy']`. Filter produces `['numpy']`. The `if not packages: …` guard at line 349 doesn't fire (we still have numpy). Install proceeds with numpy only. Code runs, tries `import pandas`, fails with `ModuleNotFoundError`. The verification report's `stderr` shows the import error; nothing anywhere announces "we dropped `pandas>=2.0,<3` because of regex."

**Mode B — total rejection.**
Caller passes `['pandas>=2.0,<3']` as the only package. Filter produces `[]`. Line 349-354 takes the fallback: `return sandbox_execute(code, timeout=timeout, allow_network=allow_network)`. Code executes against the base image (`mcp` + `pydantic` only) with no pandas. Same `ModuleNotFoundError`, same invisibility. The Step 14.12 comment correctly preserves `allow_network` through the fallback (so the branch isn't a covert offline-mode demotion) — but it *is* a covert no-install demotion.

### Impact on Pillar 2 failure attribution

The Pillar 2 gate's entire value is its ability to distinguish failure modes. Step 12.5 (Tier 1.5) and the `import_check_structural` / `wrong_pip_package` / `import_error` finding categories explicitly aim to separate "bad source" from "wrong PyPI name." The silent-drop-plus-fallback undermines that:

- Tier 1.5 import probe threads `pip_packages` into `sandbox_execute(extra_packages=...)` → same regex → may silently drop → `python -c "import tool"` fails with `ModuleNotFoundError` → Tier 1.5 reports `import_error`. The real cause (regex rejected a valid requirement string) is invisible. Chapter 4.5's failure-mode telemetry is contaminated.
- Tier 2 behavioral reports a runtime import failure as a "behavioral failure," blaming the generated code. LLM repair passes waste tokens rewriting source to avoid a dep that was never actually passed to pip.
- Tier 3 adversarial same.

This directly contradicts the reporter's summary — "can cause dependency install to be skipped and generate false verification failures." Verified.

### Why the regex was narrow in the first place

Line 343-344 comment: "Validate package names: only allow alphanumeric, hyphens, underscores, dots, and version specifiers. Reject anything that looks suspicious." The intent is defence against argv injection into the `uv pip install` call.

But the actual install call at line 363-373 uses `subprocess.run([...])` list-form — each package is its own argv entry, so the shell is never invoked and metacharacters can't escape. The layer that does touch a string is line 357: `pip_args = ", ".join(repr(p) for p in packages)` interpolated into `install_script`, which is then passed as `-c` to the python subprocess **inside Docker**. `repr()` already escapes quotes and backslashes; the worst an attacker-controlled string can do is be its own literal inside the Docker container (which is already network-isolated in Phase 2 and has no host filesystem access). The regex is belt-and-braces on top of protections that are already tight — the belt is just too small to fit.

### Remediation options

**A. Use PEP 508 parsing, not regex (recommended).**
The `packaging` library (transitive dep via `pip`/`setuptools`) provides `Requirement`:

```python
from packaging.requirements import Requirement, InvalidRequirement

good: list[str] = []
bad: list[tuple[str, str]] = []
for p in packages:
    try:
        Requirement(p)
        good.append(p)
    except InvalidRequirement as exc:
        bad.append((p, str(exc)))
packages = good
```

This accepts exactly the PEP 508 grammar — compound versions, extras, environment markers, URL requirements — and rejects genuinely malformed strings (`!@#$`, empty, whitespace-only). No false rejects on legitimate LLM output.

**B. Broaden the regex.**
Add `,`, `[`, `]`, `;`, spaces, quotes, `>`, `<` inside markers to the middle char class. This is re-implementing PEP 508 badly and is guaranteed to miss an edge case. Not recommended.

**C. Drop validation entirely.**
Subprocess list-form plus `repr()` interpolation already neutralize shell metacharacters. Simplest fix, but loses any logged signal on genuinely broken input (`uv pip install` surfaces the parse error in stderr instead). Acceptable but slightly worse for operator debugging than A.

**D. Loud-fail instead of silent fallback (must pair with A, B, or C).**
Independent of the parsing fix: when any package is dropped, emit a **`VerificationReport` finding** with category `pip_requirement_rejected` (tier=1 or a new tier-agnostic category) and severity MEDIUM. When *all* packages are dropped, return the report directly instead of falling through to a no-install `sandbox_execute`.

Concretely in the code path:

```python
if bad:
    logger.warning("Dropping malformed pip requirements: %s", bad)
    # Surface via a side-channel the caller can attach to the VerificationReport.
    # Simplest: set a module-level context var or return a dict.
if not packages:
    return {
        "stdout": "",
        "stderr": (
            "All pip requirements rejected as malformed — no-install fallback "
            f"would mask missing deps. Rejected: {bad}"
        ),
        "exit_code": -1,
        "success": False,
    }
```

The caller (import_check.py / behavioral.py / adversarial.py) then attributes the failure to `pip_requirement_rejected` in the VerificationReport instead of `import_error`. Chapter 4.5's failure-mode histogram stays accurate.

### Recommendation

Ship **A + D together.** A fixes the false-reject (replace the regex with `packaging.requirements.Requirement`); D fixes the silent-failure-attribution (surface drops as VerificationReport findings and refuse to fall through on total-rejection). Rough cost: A is ~15 LOC plus `packaging` as an explicit dependency (already transitive); D is ~10 LOC in `sandbox_tool.py` plus a pass-through field in the VerificationReport schema and ~5 LOC in each of the three Pillar-2 callers. One unit test per rejected form asserting (a) `Requirement` accepts it, (b) the VerificationReport on an all-rejected batch carries the new finding category instead of `import_error`.

Optional polish: regenerate the test fixtures in `scripts/test_verification.py` to include at least one tool whose `pip_packages` contains a compound version, so this regression stays caught.

---

## Issue #7 — `--json` path drops `allow_network` for SKILL/AGENT execution (High)

### Summary

`_execute_skill_or_agent` defaults `allow_network: bool = False`. `execute_task` calls it in two places, gated by `if not json_output:` / `else:`. The non-JSON branch passes `allow_network=allow_network` explicitly; the JSON branch omits the kwarg. Result: the same `mint run` invocation executes with different network policy depending on `--json` — and in the JSON case, the CLI's `--allow-network` flag is silently ignored.

### Where

- `src/cli/runner.py:907` — signature default: `allow_network: bool = False`
- `src/cli/runner.py:2014` — non-JSON branch (`if not json_output:`): `allow_network=allow_network` **is** passed
- `src/cli/runner.py:2030` — JSON branch (`else:`): `allow_network=allow_network` **is not** passed; default `False` fires

The two branches differ only in whether a Rich `console.status(...)` spinner wraps the call — a presentation concern, not a semantic one. The network kwarg got lost when the JSON-mode duplicate was added.

### Reproduction

```bash
# Tool that needs outbound network — e.g., anything in the registry that calls requests/httpx.
$ mint run --allow-network "fetch https://api.example.com/data and summarise"
# → executes with --network bridge, succeeds

$ mint run --json --allow-network "fetch https://api.example.com/data and summarise"
# → executes with --network none (default-false fires), fails with ConnectionError / getaddrinfo
```

Confirmed by code-path inspection against tip of `iter/6-execution-backends`. Triggers specifically for SKILL and AGENT artifacts — MCP_TOOL goes through `_execute_mcp_tool` at line 1980 which correctly threads `allow_network=allow_network` in both branches (single call site).

### Threat model / impact

- **Silent offline demotion.** Step 14.12's whole design was "offline-by-default with explicit `--allow-network` opt-in." The JSON branch breaks the opt-in.
- **JSON-consuming callers hit false failures.** CI, eval harnesses, watchdog scripts that parse `mint run --json` output see `ConnectionError` / timeouts that don't reproduce in interactive mode. Debugging this is hard because the CLI flag *was* set; the user has no reason to suspect the flag didn't take.
- **Eval-metric contamination.** If any eval path ever shells out to `mint run --json` (it doesn't today, but it's a plausible future harness shape), network-requiring tasks systematically fail and the failure reason is attributed to the artifact.
- **Not a security bug** — this doesn't grant network access that was refused. It removes access that was explicitly granted. The direction of the failure makes this a correctness/usability bug, not a privilege-escalation one. Severity is "High" because it breaks an advertised CLI contract and affects scriptable callers.

### Remediation

One-line fix at line 2030:

```python
else:
    _lf_meta = invoke_config.get("metadata", {})
    output = (
        _execute_skill_or_agent(
            artifact,
            task_prompt,
            file_paths,
            timeout_s=timeout,
            langfuse_tags=_lf_meta.get("langfuse_tags"),
            langfuse_session_id=_lf_meta.get("langfuse_session_id"),
            allow_network=allow_network,          # ← add this line
        )
        or None
    )
```

### Optional structural polish

The `if not json_output / else` pair duplicates the call site purely to attach a spinner. A cleaner shape is to factor out the call and wrap the spinner conditionally:

```python
def _call_skill_or_agent():
    return _execute_skill_or_agent(
        artifact, task_prompt, file_paths,
        timeout_s=timeout,
        langfuse_tags=_lf_meta.get("langfuse_tags"),
        langfuse_session_id=_lf_meta.get("langfuse_session_id"),
        allow_network=allow_network,
    )

_lf_meta = invoke_config.get("metadata", {})
if not json_output:
    with console.status(...):  # spinner machinery
        output = _call_skill_or_agent() or None
else:
    output = _call_skill_or_agent() or None
```

Eliminates the class of bug entirely — no duplicate arg list to drift. Worth doing as part of the fix so a future added kwarg can't repeat the same mistake.

### Test coverage

Add to `tests/unit/test_cli_runner.py` (or wherever `execute_task` is unit-tested):

```python
@pytest.mark.parametrize("json_output", [False, True])
def test_allow_network_propagates_in_both_json_modes(monkeypatch, json_output):
    captured = {}
    def fake_exec(*args, allow_network=False, **kwargs):
        captured["allow_network"] = allow_network
        return {"result": "ok"}
    monkeypatch.setattr(
        "src.cli.runner._execute_skill_or_agent", fake_exec
    )
    # … run execute_task(..., json_output=json_output, allow_network=True)
    assert captured["allow_network"] is True
```

Parametrizing on `json_output` is the key — the bug is exactly that the two modes disagree.

### Recommendation

**Fix.** One-line change is the minimum; structural polish (factor out + single call site) is strongly preferred because it removes the drift surface entirely. ~30 minutes including the parametrized test. Also grep for other `if not json_output / else` pairs in `runner.py` to confirm no sibling kwargs suffer the same fate — a quick sweep is cheap.

---

## Issue #8 — Function-entrypoint selection is an ordering heuristic, not a decorator lookup (High)

### Summary

Every call site in the codebase that answers the question *"which public async function in this source is the tool?"* does so by iterating public async definitions and returning the first one under some ordering — `dir()` alphabetical in `ToolLoader`, `ast.walk` source order in `_extract_function_name`. Both heuristics are wrong for some legitimate module layout. The FastMCP decorator `@mcp.tool` is the only unambiguous signal identifying the entrypoint, and nothing in the codebase reads it. Today the mis-selection is hidden only by the generator's `_validate_code` enforcing "exactly one public top-level async def" — a contract known to only one file that six others silently depend on.

This finding supersedes Issue #5's framing. Issue #5 treated the `ToolLoader` vs "rest of codebase" split as the bug; #8 shows the "rest of codebase" is also wrong, just for a different class of inputs. The fix touches seven call sites, not two.

### Where

- `src/verification/behavioral.py:69` — `_extract_function_name`: `ast.walk` + first public async = source order
- `src/cli/runner.py:786` — runtime MCP-tool execution: `_extract_function_name(source_code)`
- `tests/evaluation/execution.py:744` — compositional chain MCP-tool execution: `_extract_function_name(source_code)`
- `src/verification/coverage.py:280` — Pillar 3 coverage verifier replay: `_extract_function_name(source_code)`
- `src/verification/behavioral.py:595` — Tier 2 behavioral probe: `_extract_function_name(source_code)`
- `tests/evaluation/harness.py:1241` — eval harness execution: `_extract_function_name(source_code)`
- `scripts/test_pipeline.py:253` — test script: `_extract_function_name(source_code)`

(Issue #5's two `dir()` sites at `tool_loader.py:131, 185` are a separate-but-related ninth/tenth occurrence — same bug class, different heuristic.)

### Reproduction

Reporter's input reproduced verbatim:

```python
src = """
async def helper_format_rows(rows):
    return [str(r) for r in rows]

async def summarize_sales(data):
    return {"total": sum(data)}
"""
print(_extract_function_name(src))
# → 'helper_format_rows'
```

Confirmed on tip of `iter/6-execution-backends`. `ast.walk` emits nodes in document order; the first `AsyncFunctionDef` is `helper_format_rows` because it's defined first in the file. Every downstream runtime / verification path that uses this name executes or probes the helper, not the tool.

### Why this is latent today, not already firing

Same invariant as Issue #5: `src/orchestrator/generators/mcp_tool.py::_validate_code` rejects any module with `async_def_count != 1`. Private helpers (`_`-prefixed) don't count. With one public async def, "first in any order" is trivially correct. **But**:

- Pillar 3 Merge (`_merge_tools`) lands `combined_source_code` in the registry without running `_validate_code`.
- Pillar 3 Split (`_split_tools`) lands per-variant `source_code` without running `_validate_code`.
- Any future LLM prompt that legitimately produces a helper + entrypoint (e.g., a prompt that asks for "clean separation of the data-transform step") produces output that the gate passes (Tier 1 doesn't AST-count async defs) but downstream execution runs the wrong function.

The reporter's repro isn't hypothetical — it's exactly what a merge LLM would produce when asked to combine two tools and the LLM decides to extract shared rows-formatting as a helper.

### The unambiguous signal the code could use instead

FastMCP identifies tools by decorator. The generator prompt requires `@mcp.tool()` on the entrypoint function; `_validate_code` enforces `@mcp.tool` appears in source. AST-matching the decorator is deterministic:

```python
def _find_mcp_tool_function(source_code: str) -> str | None:
    """Return the name of the @mcp.tool-decorated public async function.

    Returns the function name when exactly one match exists. Returns None
    on parse error, zero matches, or multiple matches (ambiguous — caller
    should reject or fall back).
    """
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return None

    matches: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        for dec in node.decorator_list:
            # Accepts both @mcp.tool and @mcp.tool(...) forms.
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "tool":
                matches.append(node.name)
                break
    return matches[0] if len(matches) == 1 else None
```

Properties:

- **Unambiguous for generator output.** `_validate_code` requires exactly one `@mcp.tool`; the generator path produces exactly one match.
- **Correct for merge/split output** when the LLM follows the FastMCP pattern (which the prompts require). If the LLM adds a helper function, the helper has no `@mcp.tool` decorator, so it's ignored.
- **Handles the `@mcp.tool` vs `@mcp.tool()` forms** via the `Call` unwrap.
- **Ignores other decorators** (`@lru_cache`, `@contextmanager`, etc.) because the `.attr == "tool"` check is specific.
- **Loudly returns `None`** when ambiguous (multiple matches) — caller surfaces a VerificationReport finding rather than silently picking wrong.

### Fallback policy for legacy / non-FastMCP artifacts

Some legacy registry rows may predate the decorator requirement, and skill-embedded Python fences don't necessarily follow FastMCP conventions. Chain:

1. **Decorator match** (`_find_mcp_tool_function`) → unambiguous, use it.
2. **Zero matches, exactly one public async def** → use that one, log WARNING that the decorator was missing.
3. **Zero matches, multiple public async defs, or parse error** → return `None`; caller fails the verification / execution path with a clear "could not identify tool entrypoint" error. **Do not** silently pick one.

The old `_extract_function_name` becomes a thin wrapper delegating to this chain, so all seven call sites inherit correct behavior for free.

### Impact of fixing

- **Pillar 2 failure attribution stays accurate** for merge/split outputs. Chapter 4.5's telemetry no longer contaminated by "the helper I probed didn't work" readings.
- **Pillar 3 Split's coverage verifier** (replay via `coverage.py:280`) exercises the real tool — the replay signal becomes meaningful for the cases where LLM merge produced a helper-first layout.
- **Runtime execution** (`runner.py:786`, `execution.py:744`) calls the right function at CLI and chain-step time.
- **Tier 2 behavioral** (`behavioral.py:595`) probes the right function. False-pass (helper happens to return plausibly) and false-fail (helper explodes because it's called with entrypoint inputs) both disappear.

### Remediation

**A. Replace `_extract_function_name` with the decorator-aware helper (recommended).** Add `_find_mcp_tool_function` next to `_extract_function_name` in `behavioral.py`; rewrite `_extract_function_name` as the fallback chain described above. All seven call sites work unchanged.

**B. Also port the constraint into the Tier 1 static scanner.** When an artifact claims MCP_TOOL type, require exactly one public async def carrying `@mcp.tool`. Catch the violation at verification time instead of trusting downstream selection to handle ambiguity. Defense in depth — this makes the decorator the contract Chapter 4.5 documents, not just a de-facto selector.

**C. Document the contract in CLAUDE.md.** Under "Stable Interfaces" or "Architectural Constraints", add a one-liner: "MCP_TOOL artifacts are identified by the `@mcp.tool` decorator, not by function name ordering. Generators must emit exactly one decorated public async def; the static scanner enforces this."

Cost: A is ~30 LOC of source + ~60 LOC of tests; B is ~20 LOC in `static_scanner.py`; C is two lines of docs.

### Test coverage

In a new `tests/unit/test_function_selection.py` (or extend `test_behavioral.py`):

```python
def test_helper_first_then_entrypoint_selects_decorated():
    src = '''
    async def helper_format_rows(rows): ...
    @mcp.tool()
    async def summarize_sales(data): ...
    '''
    assert _find_mcp_tool_function(src) == "summarize_sales"

def test_entrypoint_first_then_helper_selects_decorated():
    # Same test, opposite source order — decorator matters, order doesn't.

def test_alphabetical_before_decorator_selects_decorated():
    # apple_helper (no decorator) + zebra_tool (@mcp.tool) — asserts we don't
    # regress the ToolLoader-alphabetical case.

def test_no_decorator_single_public_async_falls_back_with_warning():
    # Legacy artifact path.

def test_no_decorator_multiple_public_async_returns_none():
    # Unambiguous failure, not silent wrong selection.

def test_ambiguous_multiple_decorated_returns_none():
    # Two functions both carry @mcp.tool — generator shouldn't produce this
    # but if it ever does, we fail loudly.
```

One parametrized `pytest.mark.parametrize` across all seven call sites would lock the invariant at the behavior level.

### Recommendation

**Land #8 before #1 / #5 / #6.** Rationale:

1. **Before #6.** #6 fixes Pillar-2 failure attribution at the pip-requirement layer. #8 fixes the same thing at the function-selection layer. Landing them in one eval-prep sweep means the 400-record run produces clean telemetry from the start.
2. **Before #1 / #5.** #1 deletes ToolLoader's host path and #5 addresses the two `dir()` sites in that path. If #8 is landed first, #5's standalone fix (if #1 is deferred) can delegate to `_find_mcp_tool_function` directly — no intermediate `_extract_function_name` detour.
3. **Before any Pillar-3 eval.** Merge/split are where the invariant breaks in practice; the longitudinal and shift runs exercise them heavily.

Estimated effort: ~1.5-2 hours including tests. Touches seven call sites but each is a one-line swap to the new helper.

---

## Issue #9 — Compositional path crashes on registry-matched AGENT due to `agent_config` dict/str mismatch (High)

### Summary

`GeneratedArtifact.agent_config` is typed `dict | None`, but registry metadata stores it as a JSON string (because ChromaDB metadata values must be primitives). The compositional chain's `_match_to_artifact` helper reads `meta.get("agent_config")` and passes the string straight into `GeneratedArtifact(...)`. Pydantic's `dict_type` validator rejects it with a `ValidationError`. Any chain step that hits a registry-matched AGENT artifact crashes at reconstruction time. The CLI single-artifact path already handles this correctly — the crash is specific to the compositional path.

### Where

- `src/orchestrator/loading_node.py:157-164` — the serialization step: when an AGENT is freshly registered, `agent_config` (dict) is `json.dumps`-ed into `metadata["agent_config"]`.
- `src/registry/store.py:516-517` — `seed_registry` / `add_seeded_capability` writes the caller's `agent_config` into metadata verbatim (already a string for seeded rows).
- `src/orchestrator/composition.py:359-370::_match_to_artifact` — the crash site: `agent_config=meta.get("agent_config")` passes the string **unchanged** into `GeneratedArtifact(...)`.
- `src/schemas/task.py:143` — the schema contract: `agent_config: dict | None`. Pydantic strict-mode enforcement rejects the string.

### Reproduction

```python
from src.schemas.task import GeneratedArtifact
from src.schemas.capability import CapabilityType

# What composition.py:367 hands to the schema constructor —
# whatever loading_node.py:160-164 or store.py:517 wrote to ChromaDB metadata.
json_str_from_metadata = '{"system_prompt": "you are a helper", "skills": []}'

GeneratedArtifact(
    capability_name="foo",
    capability_type=CapabilityType.AGENT,
    agent_config=json_str_from_metadata,
)
# → pydantic.ValidationError: agent_config
#    Input should be a valid dictionary [type=dict_type, input_type=str]
```

Confirmed on tip of `iter/6-execution-backends`.

### Asymmetry with the CLI path

`src/cli/runner.py:1210-1221::_try_registry_match` already normalizes the dict/string shape before constructing the proxy:

```python
agent_config_raw = meta.get("agent_config")
agent_config = None
if agent_config_raw:
    try:
        agent_config = (
            json.loads(agent_config_raw)
            if isinstance(agent_config_raw, str)
            else agent_config_raw
        )
    except (json.JSONDecodeError, TypeError):
        agent_config = None

proxy = GeneratedArtifact(..., agent_config=agent_config)
```

The compositional path was never given the equivalent decoder, so the two sites produce different proxies for the same registry row.

Similarly, `src/registry/store.py:523-528` does its own JSON-decode round-trip when building the tokenization snapshot — evidence that the author knew about the string serialization and handled it inconsistently across call sites.

### Threat model / impact

- **Compositional chains with registry-matched AGENT steps crash.** Whether the chain catches the `ValidationError` or propagates it depends on surrounding `try/except`, but either way the AGENT step doesn't run; subsequent chain steps that depend on its output cascade into failure.
- **Pillar 3 reuse telemetry is biased.** Registry-reuse for AGENT artifacts via the compositional path is systematically zero — every reuse attempt crashes at reconstruction. Chapter 6's Gini / Core-Work Classification Accuracy / utility-score-based KL analyses all undercount AGENT reuse because the reuse path never reaches `InvocationRecord` write.
- **Merge/split AGENT variants are unreachable via composition.** Pillar 3 Split and Merge produce AGENT variants whose `agent_config` lands in metadata as a JSON string (loading_node serialization); compositional chains that try to reuse them crash the same way.

### Remediation

**A. Normalize at the boundary in `composition.py::_match_to_artifact` (minimum fix).**

```python
@staticmethod
def _match_to_artifact(match: MatchItem, ref: CapabilityReference) -> GeneratedArtifact:
    meta = getattr(match.capability, "metadata", {}) or {}

    agent_config_raw = meta.get("agent_config")
    agent_config: dict | None = None
    if agent_config_raw:
        if isinstance(agent_config_raw, dict):
            agent_config = agent_config_raw
        elif isinstance(agent_config_raw, str):
            try:
                agent_config = json.loads(agent_config_raw)
            except (json.JSONDecodeError, TypeError):
                agent_config = None  # or {"system_prompt": agent_config_raw} — see note

    return GeneratedArtifact(
        capability_name=ref.capability_name,
        capability_type=ref.capability_type,
        source_code=meta.get("source_code"),
        skill_content=meta.get("skill_content"),
        agent_config=agent_config,
        description=getattr(match.capability, "description", None),
        generated_at=datetime.now(UTC),
    )
```

Note on the `except` branch: `store.py:528` falls back to `{"system_prompt": agent_config}` (treat the raw string as a plain system prompt). That preserves information but may mis-shape a genuinely-broken string. `_try_registry_match` in the CLI silently drops to `None`. Two different policies for the same situation — worth picking one and applying it everywhere.

**B. Factor the decode into a shared helper (recommended polish).**

```python
# src/schemas/task.py or src/registry/store.py
def decode_agent_config_metadata(raw: Any) -> dict | None:
    """Decode the ChromaDB-serialized agent_config back to a dict.

    Shared by:
    - src/orchestrator/composition.py::_match_to_artifact
    - src/cli/runner.py::_try_registry_match
    - src/registry/store.py snapshot construction
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, TypeError):
            return None
    return None
```

All three sites call this helper. No drift. One unit test with the four input shapes (`None`, `""`, `dict`, `str(valid-JSON)`, `str(invalid-JSON)`).

**C. Push the normalization into the schema (rejected).**

`GeneratedArtifact.agent_config: dict | str | None` with a Pydantic `@field_validator` that JSON-decodes strings. This muddles the schema contract — downstream code would need to know that `.agent_config` might have been a string that got silently decoded, and the codec logic leaks into the schema. Worse than B.

### Test coverage

Add to `tests/unit/test_composition.py`:

```python
def test_match_to_artifact_normalizes_json_string_agent_config():
    """Registry metadata stores agent_config as JSON string (loading_node serialization).
    _match_to_artifact must decode it back before handing to GeneratedArtifact.
    """
    cap = _make_cap_with_metadata({
        "agent_config": '{"system_prompt": "you are a helper", "skills": []}'
    })
    match = MatchItem(capability=cap, score=0.9)
    ref = CapabilityReference(capability_type=CapabilityType.AGENT, ...)

    artifact = CompositionalExecutor._match_to_artifact(match, ref)
    assert isinstance(artifact.agent_config, dict)
    assert artifact.agent_config["system_prompt"] == "you are a helper"
```

Plus equivalents for `isinstance(raw, dict)` (pre-serialized rows) and malformed-string fallback behavior.

### Recommendation

**Fix.** Ship Option B (shared helper) because three files already handle this and two of them already disagree about the edge-case policy — unifying them closes a drift surface. Estimated effort: ~20 LOC + 5 unit tests, ~45 minutes. Land **before the 400-record eval run** because AGENT reuse telemetry is currently zero-biased.

---

## Issue #10 — CLI returns exit 0 on execution failure via registry-match path (High)

### Summary

`execute_task` at `src/cli/runner.py:2244-2260` has two success-exit branches. The first (line 2244) requires both artifact and output — correct. The second (line 2258) exits 0 whenever `matches and not gaps`, regardless of whether execution was attempted or whether it succeeded. The comment at 2256-2257 says the intent is "seeded descriptor without source code" (a registry hit that has no executable payload), but the actual check doesn't discriminate between "nothing to execute" and "tried and failed."

### Where

- `src/cli/runner.py:2244-2260` — the two exit-0 branches
- `src/cli/runner.py:1224-1241` — `_try_registry_match` swallows execution exceptions and returns `(proxy, None)`, making the "tried and failed" case indistinguishable from "never attempted" at the exit-code site
- `src/cli/runner.py:2258` — the buggy check: `if (matches) and not (gaps): return 0`

### Reproduction

Reporter's repro: task-level execution failed (output was `null`), `$?` was `0`.

Code-path reproduction:

1. Gap detection fires → `final_state.matches` populated with registry matches; `final_state.gaps` empty (all required caps matched).
2. CLI dispatches to `_try_registry_match` or `_execute_mcp_tool` / `_execute_skill_or_agent`.
3. Execution raises (Docker down, AGENT timeout, Issue #9's AGENT-validation crash, sandbox OOM, etc.) → the surrounding `except Exception: output = None` at line 1239-1241 catches it.
4. Control returns to `execute_task` with `artifact != None` and `output = None`.
5. Line 2244 `if artifact is not None and output:` → False (output is falsy).
6. Line 2258 `if (matches) and not (gaps):` → True (matches populated at gap-detection time). **Returns 0.**

### Threat model / impact

- **Scripted callers get false positives.** `mint run --json` in CI, eval harnesses, watchdogs → check `$?` → record "task passed" → record a task as successful that produced no output.
- **Thesis telemetry contaminated.** `EvaluationHarness` may (depending on wiring) rely on the CLI exit code as a success signal. Any task that fails execution via the registry-match path is silently classed as passing. This biases reported success rates upward, especially for AGENT tasks where Issue #9's crash is systematic.
- **Contradicts the §12 finding #14/#15/#16 strict-gate pattern** — `docs/MASTER_PLAN_2026_04_13.md` commits to "no silent fallback paths anywhere in the pipeline." This is a silent fallback at the CLI-exit layer.
- **Contradicts CLAUDE.md's exit-code table** — the table documents codes 2 (decomposition), 3 (output-contract), 4 (verification), 5 (classification) for failure modes. Execution failure via registry match is not in the table and falls through to code 0.

### Remediation

**A. Distinguish "non-executable seeded descriptor" from "tried and failed."**

The real predicate for the intended exit-0 case is "the matched capability has no executable payload." Check that directly:

```python
# After line 2243 (current code):
# Already: `if artifact is not None and output: return 0`  -- real success

# Case 2 rework:
if artifact is not None:
    # Something was attempted. We already know output is falsy.
    _has_payload = (
        getattr(artifact, "source_code", None)
        or getattr(artifact, "skill_content", None)
        or getattr(artifact, "agent_config", None)
    )
    if _has_payload:
        # We tried to execute and got nothing — that's a failure.
        # Prefer a dedicated exit code so the CLI layer is machine-readable.
        return 6  # "execution produced no output"
    # No payload — seeded-descriptor case, intended exit 0.
    return 0

# Case 3 — no artifact at all but matches-without-gaps (truly a pure-lookup
# case where the CLI layer never picked a single artifact).
if (_get_final("matches", None) or []) and not (_get_final("gaps", None) or []):
    return 0

return 1
```

**B. Record execution failure explicitly instead of inferring from `output`.**

`_try_registry_match` already catches the exception at line 1239; it should record the error so the exit-code logic doesn't have to guess:

```python
# return (proxy, output)  -- current
return (proxy, output, exec_error)  # exec_error is non-None on the except branch
```

Thread `exec_error` through to the exit-code site and branch on it explicitly. Cleaner than inferring from `output is None`. Combines well with A.

**C. Pick a new exit code and document it.**

The §12 strict-gate table assigns 2 / 4 / 5 / 3 to layered failures. Execution failure after a successful verification+classification path deserves its own slot. Suggest **code 6 — "execution failure after successful pipeline."** Add a row to:

- `CLAUDE.md` exit-code table in the "Three-layer strict-gate pattern" section
- Any runbook that maps exit codes to user-facing messages
- The parametrized exit-code test (if one exists; if not, add one — `tests/unit/test_cli_exit_codes.py`)

### Test coverage

Parametrize across the four cases:

```python
@pytest.mark.parametrize("scenario,expected_exit", [
    ("artifact_with_output",           0),  # happy path
    ("artifact_no_payload_no_output",  0),  # seeded descriptor, nothing to execute
    ("artifact_with_payload_failed",   6),  # tried, failed — THE FIX
    ("no_artifact_no_matches",         1),  # total pipeline failure
])
def test_cli_exit_code_discriminates_execution_failure(scenario, expected_exit):
    ...
```

The critical case is `artifact_with_payload_failed` — today returns 0, must return non-zero post-fix.

### Recommendation

**Fix (A + B + C).** Estimated effort: ~30 LOC across `runner.py` + one new parametrized test + two doc-string updates, ~45 minutes. Land **before the 400-record eval run** because it directly biases reported success rates.

Secondary audit: grep `src/cli/runner.py` for other branches that `return 0` on conditions that don't include "execution succeeded" — ensure the exit-code contract is uniform across every path.

---

## Issue #11 — Non-dict MCP outputs crash `_save_output`; falsey outputs silently coerced to `{}` (Medium-High for thesis integrity)

### Summary

Two half-bugs sharing a root cause: `parsed.get("result", {}) or {}`. This idiom appears at three sites and does two wrong things at once. (a) For truthy non-dict outputs (`5`, `[1,2,3]`, `"hello"`) the raw value is returned unchanged and downstream `_save_output` calls `.items()` on it, crashing with `AttributeError`. (b) For falsey outputs of any shape (`0`, `0.0`, `False`, `""`, `[]`) the value is silently replaced with `{}`, losing the legitimate answer.

### Where

- `src/cli/runner.py:888` — CLI MCP-tool execution return
- `src/cli/runner.py:1353` — `_save_output` scan: `for key, value in output.items():`
- `tests/evaluation/execution.py:918` — compositional chain MCP-tool execution return
- `tests/evaluation/harness.py:1351` — eval harness MCP-tool execution return

### Reproduction

**Half A — crash on non-dict truthy output:**

```python
output = 5
for key, value in output.items():  # line 1353
    ...
# → AttributeError: 'int' object has no attribute 'items'

output = [1, 2, 3]
for key, value in output.items(): ...
# → AttributeError: 'list' object has no attribute 'items'
```

Confirmed by direct Python execution.

**Half B — falsey coercion across all three sites:**

```
0                    or {}  → {}
0.0                  or {}  → {}
False                or {}  → {}
''                   or {}  → {}
[]                   or {}  → {}
{}                   or {}  → {}   (no-op)
{'a': 1}             or {}  → {'a': 1}  (truthy dict passes through)
5                    or {}  → 5        (truthy scalar passes through — enables Half A)
[1, 2, 3]            or {}  → [1, 2, 3] (truthy list passes through — enables Half A)
```

Confirmed by direct replay on tip of `iter/6-execution-backends`.

### Why the idiom is wrong

`parsed.get("result", {}) or {}` conflates two distinct questions:

1. "Did the tool's output envelope contain a `result` key at all?" → discriminates "tool raised / no output" from "tool returned something."
2. "Is the returned value truthy?" → discriminates "tool returned a useful value" from "tool returned a falsy value."

The right discriminator for "tool raised" is #1 — check for the key's presence, not the value's truthiness. Falsy is a legitimate value. The idiom answers #1 via the second-argument default (`{}`), then destroys the answer by applying #2.

### Threat model / impact

**Half A crashes:**

- Any MCP tool that returns `5` (count), `[1, 2, 3]` (list result), `"summary text"` (string result) crashes the CLI sidecar write. Whether the CLI as a whole aborts depends on the outer try/except; at minimum the output-file directory (`data/mint_output/<cap_name>_<ts>/`) is not populated.
- Tier 2 behavioral testing probes tools with boundary inputs. A tool whose normal return shape is scalar/list falls through to `.items()` at the harness layer too (harness.py:1351 returns the coerced value, and any downstream write assumes dict).

**Half B coercions bias thesis eval:**

The business-analytics testbed (from `tests/evaluation/tasks.py::ALL_TASKS`) includes tasks whose correct output is legitimately zero-valued or empty:

- "Count refunds above $10k" when the fixture has none → `0` or `{"count": 0}`. If the LLM emits just `0`, the harness records `{}`.
- "Filter orders older than N days" when no rows match → `[]`. Recorded as `{}`.
- "Is the dataset clean?" validator returning `False` → recorded as `{}`.
- Empty-string summary for edge-case input → recorded as `{}`.

Downstream consequences:

- `OutputContract.check()` compares `{}` against the task's declared `required_keys` → false-negative failure. The task is marked as "wrong output shape" when the shape was correct but the value happened to be falsy.
- Tier 2 behavioral sees `{}` for any tool whose boundary behavior is "return 0 / [] / False" and may reject correct generalizing tools.
- `EvaluationRecord.output_in_range` flag gets set incorrectly for scalar-zero tasks.
- Chapter 6 reports systematic under-counting of correct zero-value answers — a direction-of-bias against tasks where the LLM's answer is "no" or "none."

### Remediation

**A. Sentinel-based result presence check (replaces the `or {}` idiom).**

Apply at `runner.py:888`, `execution.py:918`, `harness.py:1351`:

```python
# Before:
return parsed.get("result", {}) or {}

# After:
_SENTINEL = object()
result = parsed.get("result", _SENTINEL)
if result is _SENTINEL:
    # No result key at all — treat as failure (tool crashed without reporting).
    return {}
return result  # Preserve falsy values: 0, False, "", []
```

Factor `_SENTINEL` + the logic into a small helper in `src/registry/metrics.py` or a new `src/cli/output_coerce.py` so all three sites call one function.

**B. Dict-coerce non-dict outputs before the `_save_output` scan.**

`src/cli/runner.py:1353`:

```python
# Before:
for key, value in output.items():
    ...

# After:
if isinstance(output, dict):
    scan_dict = output
else:
    # Scalar / list / string outputs need to be persisted too — wrap for the scan.
    scan_dict = {"_result": output}
for key, value in scan_dict.items():
    ...
```

The full-result JSON dump at line 1373 (`json.dumps(output, indent=2, default=str)`) already handles arbitrary types; only the per-key scan needs the coercion.

**C. Flip the truthy check at the save-dispatch site.**

`src/cli/runner.py:2105`:

```python
# Before:
if output:
    _save_output(...)
elif artifact is not None:
    placeholder = {"_empty": True, "_capability": cap_name}
    ...

# After:
if output is not None:
    _save_output(...)
elif artifact is not None:
    ...
```

Same truthy/falsy conflation at a higher level — `output=0` is currently routed to the `_empty` placeholder branch. Fix this or Half B's fix at line 888 is undone two frames up.

**D. Audit `OutputContract.check()` for the same pattern.**

`src/orchestrator/output_contract_resolver.py` applies `required_keys` and `required_files` against the task output. If it does `for k, v in output.items()` or `if not output:`, scalar/list outputs either crash or are treated as empty. Follow-on pass after A/B/C to confirm the resolver handles non-dict outputs sensibly (likely: a scalar output cannot satisfy `required_keys` — which is fine, but the failure reason should be explicit, not a crash or a silent "empty output").

### Test coverage

Parametrized tests covering all falsy + non-dict shapes:

```python
@pytest.mark.parametrize("value", [0, 0.0, False, "", [], {}, {"k": 1}, 5, [1, 2, 3], "hello"])
def test_execute_mcp_tool_preserves_falsy_and_non_dict_outputs(value, monkeypatch):
    """Tool result of `value` must round-trip through _execute_mcp_tool unchanged."""
    # Mock sandbox_execute to return {"success": True, "stdout": json.dumps({"result": value})}
    # Assert _execute_mcp_tool returns exactly `value` (not `{}`).
    ...

@pytest.mark.parametrize("value", [0, 0.0, False, "", [], {"k": 1}, 5, [1, 2, 3], "hello"])
def test_save_output_handles_non_dict_outputs(tmp_path, value):
    """_save_output must not crash on scalar / list / string outputs."""
    _save_output(tmp_path, value, artifact=..., ...)  # must not raise
    assert (tmp_path / "<cap>_<ts>" / "output.json").exists()
```

Same parametrization at `tests/evaluation/test_execution.py::test_execute_mcp_tool_in_chain` and `tests/evaluation/test_harness.py` for the other two coercion sites.

### Recommendation

**Fix A + B + C together** before the 400-record eval run. Half A (crash) is a hard blocker for any tool that returns a non-dict truthy value. Half B (falsy coercion) is a silent correctness bug that distorts eval results in a direction that favors structured-output tasks over scalar-valued-answer tasks. Estimated effort: ~30 LOC across three files + a shared coercion helper + the parametrized tests, ~1-1.5 hours.

Optional polish (**D**) can follow — ~30 more minutes to confirm `OutputContract.check()` doesn't suffer the same conflation. Worth doing before declaring Half B closed.

---

## Cross-cutting observations

1. **Trust-boundary documentation drift.** CLAUDE.md's "MCP Tool Loading Strategy" paragraph claims sandboxed-subprocess loading; the code does host import + plain-subprocess smoke test. Fixing #1 by removing the host path aligns code with claim. Keeping the host path would require weakening the claim — which weakens the thesis's Pillar 2 story.

2. **"Validate-at-boundary" not yet uniformly applied.** #2 (skill name) and #4 (`$EDITOR`) are both inputs crossing a trust boundary (user / LLM → filesystem / shell) without boundary validation. A short audit pass over other similar sites (CLI arg parsing in `mint`, LLM-returned file paths in generators, fixture paths in eval harness) would be worth scheduling before Chapter 4 — call it Step 13.5b if helpful.

3. **`atexit` as sole cleanup contract (#3) is fine for the thesis eval shape** (bounded process lifetime, bounded task count). It is not fine for any future daemon-shaped deployment. Flag in Chapter 7.

4. **Latent invariants held together by cross-file contracts (#5 + #8 + #9).** `ToolLoader`'s alphabetical selection **and** `_extract_function_name`'s source-order selection are both correct today only because the generator's `_validate_code` enforces exactly-one-public-async-def — and `composition.py::_match_to_artifact` is correct today only as long as no AGENT is ever matched from the registry (because the JSON-string/dict contract was enforced inconsistently across the three files that serialize/deserialize `agent_config`). Seven call sites silently depend on a contract enforced in one upstream file for #8; three call sites disagree with each other on the same serialization format for #9. None of these cross-file relationships are testable from any single file. The fix pattern is the same in both cases: eliminate the implicit contract by either (a) replacing the heuristic with an explicit signal the data carries (decorator for #8) or (b) factoring the codec logic into a shared helper every site calls (#9).

5. **Silent-degradation paths break Pillar 2 failure attribution (#6 + #7 + #10 + #11-half-B).** Four distinct manifestations of the same anti-pattern. (a) #6's `_VALID_PKG` regex drops valid requirement strings and falls back to no-install. (b) #7's `--json` branch silently omits `allow_network` kwarg and default-falses it. (c) #10's exit-code branch silently returns 0 on execution failure because matches-and-no-gaps is a weaker predicate than the comment's stated intent. (d) #11's `or {}` coerces legitimate zero/false/empty outputs to an empty dict. Every case converts "we couldn't do what you asked" into "we did something that looks similar enough to continue." Chapter 4.5's disclosure note for Tier 1.5 framing — that Tier 1.5 exists to *route failure attribution* to the right category — depends on this not happening. A systematic sweep for `or {}`, `or None`, `or False`, and `return 0 if ... else 1` patterns in `src/verification/`, `src/cli/`, and `tests/evaluation/` before the 400-record eval run is cheap insurance. §12 finding #14/#15/#16's "no silent fallbacks anywhere" commitment is larger than the three layers it currently names.

6. **Duplicate call sites with drifted kwargs (#7 specifically).** The JSON-vs-non-JSON duplicate in `execute_task` is a class of bug: wrapping the *same call* in two branches just to attach a UI concern (a spinner) means any future kwarg added to the call site must be mirrored in both branches. A single point of call with the UI wrapped conditionally would prevent this entire class. Worth one pass to audit `src/cli/runner.py` for the same shape.

7. **Type-contract boundaries between storage and schema (#9 + #11-half-A).** The codebase has two schema boundaries — ChromaDB metadata (primitives only) and Pydantic models (typed). Neither side knows about the other's constraints, so crossings are hand-coded at every site: `loading_node.py` serializes dict→JSON string, `_match_to_artifact` doesn't deserialize, `_try_registry_match` does. Same pattern at the sandbox boundary: the sandbox returns JSON-parsed objects (any type), downstream consumers assume dict. Both boundaries would benefit from a named codec layer (a `StorageCodec` or `ResultEnvelope` module) that documents the on-the-wire shape and provides typed encode/decode helpers. Otherwise every future addition re-solves the same problem ad-hoc and drift accumulates.

8. **Thesis-evaluation contamination vector (#6 + #9 + #10 + #11).** Four of eleven findings directly distort the telemetry Chapter 6 reads — not as crashes (easy to notice) but as systematic biases (hard to notice). Respectively: wrong failure-mode attribution (Tier 1.5/2 blame the tool for bad pip names), AGENT reuse undercounting (every compositional AGENT match crashes), success-rate over-counting (execution failures returning exit 0), zero-value answer under-counting (falsey outputs coerced to empty). The combined effect is optimistic on some axes and pessimistic on others, so absolute numbers and comparisons across task types are both affected. Landing these four before Phase 5 is not optional — the eval is already 18-hour unattended and ~$6; a second run to re-collect clean telemetry is expensive.

## Suggested order of operations

Eighteen findings now. **All shipped 2026-04-20** across a single multi-session sweep. The ordering below is preserved for audit-trail purposes; every row is ✅ closed.

### Tranche 1 — eval-blocking (all shipped)

Ordered within the tranche by a mix of effort and dependency:

1. **#15** — coverage verifier `sort_keys` symmetry + `_semantic_equal` defence-in-depth. Pre-fix manufactured false-positive split coverage. ~30 min. ✅
2. **#18** — longitudinal analyzer bucket utility/events by timestamp window (not task_id merge). Pre-fix corrupted per-cycle snapshots on any repeating task; Step 18.8 shift-detection verdicts were unreliable. ~45 min. ✅
3. **#12** — three-layer DAG validator (decomposition-layer `_validate_depends_on_indices` + fail-closed `build_execution_order` + run-loop guard via None-check). Pre-fix invalid `depends_on` / cycles phantom-executed and reported `chain_success=True`. ~1.5-2 hours. ✅
4. **#13** — `contained_join` helper + `_sanitise_component` at three unsanitised path joins in `_save_output`. Pre-fix a crafted `cap_name` / output key / `output_files` filename escaped `mint_output/<run>/`. ~45 min. ✅
5. **#17** — `analyze_results.py::ALL_TASK_IDS` imported from `ALL_TASKS` single source of truth. Pre-fix `range(1, 19)` dropped T19 + T20 from every Chapter 6 per-task table. ~5 min. ✅
6. **#7** — single-call-site factoring for SKILL/AGENT execution; no kwarg can drift. ~30 min. ✅
7. **#9** — shared `decode_agent_config_metadata` helper at three sites. Unblocks AGENT registry-reuse telemetry. ~45 min. ✅
8. **#10** — distinguish "tried and failed" from "non-executable seeded descriptor"; new exit code 6. ~45 min. ✅
9. **#11** — sentinel-based `extract_tool_result` + `as_dict_for_save` + `if output is not None`. Preserves legitimate falsy outputs. ~1-1.5 hours. ✅
10. **#16** — `serialize_for_invocation` truncation emits valid JSON sentinel; coverage detects it. Pre-fix replay crashed on truncated input_args. ~30 min. ✅
11. **#6** — `packaging.requirements.Requirement` + `pip_requirement_rejected` finding. ~1-1.5 hours. ✅
12. **#8** — `_find_mcp_tool_function` + fallback chain at all seven call sites. ~1.5-2 hours. ✅

### Tranche 2 — shipped in the same sweep

13. **#2** — skill-name regex + `is_relative_to` containment. ✅
14. **#4** — `subprocess.run` list-form + `try/finally`. ✅
15. **#14** — confirmation test for the #6 fix. ✅
16. **#1 + #5 bundle** — removed host-import path from `loading_node`. CLAUDE.md rewritten. ✅
17. **#3** — `ToolLoader` docstring covers the `atexit` cleanup contract. ✅

### Behavioral regressions landed

- `CompositionalExecutor.build_execution_order()` returns `list[int] | None` (was `list[int]`); `run()` short-circuits to a failure ChainResult on `None`. Decomposition sticky flag `decomposition_succeeded=False` flips on invalid `depends_on` / cycles; CLI exit 2 on retry exhaustion. (#12)
- `_save_output` silently sanitises output dict keys and `output_files` entries that would escape `run_dir`; crafted names like `"../escape"` are rewritten to under-base names. (#13)
- `execute_task` JSON mode respects `--allow-network`. (#7)
- CLI exit code 6 — "execution failure after successful pipeline." (#10)
- Falsy tool outputs (`0`, `False`, `[]`, `""`) are preserved instead of coerced to `{}`. Any downstream consumer treating `{}` as "empty result" must now handle scalars. (#11)
- Coverage verifier uses symmetric `sort_keys=True` + `_semantic_equal` parse-then-compare. Same-content-different-order outputs now correctly report `handled=False`. (#15)
- `serialize_for_invocation` oversize payloads emit `{"__truncated__": true, ...}` sentinel instead of broken JSON. Coverage detects it + tolerates legacy rows. (#16)
- `ALL_TASK_IDS` now includes T19 + T20; every Chapter 6 per-task table covers the full testbed. (#17)
- Longitudinal per-cycle utility/events are isolated across task repetitions. Step 18.8 shift-detection verdicts are now reliable. (#18)
- `loading_node` log-line "host_loadable" count removed. (#1)
- VerificationReport schema gains `pip_requirement_rejected` finding category. (#6)
- `record_cycle_utility_scores` / `record_cycle_events` gained optional `occurred_at` parameter. (#18)

---

**End of audit.** _Extended 2026-04-20 (first extension) with findings #5 and #6. Extended 2026-04-20 (second extension) with findings #7 and #8; Issue #5's recommendation revised to defer to the #8 bundle. Extended 2026-04-20 (third extension) with findings #9, #10, #11 and a new two-tranche ordering. Extended 2026-04-20 (fourth extension) with findings #12, #13, #14. Extended 2026-04-20 (fifth extension) with findings #15, #16, #17, #18 (detail in `security_audit_2026_04_19_ext5.md`). **All 18 findings fixed 2026-04-20** — the full audit landed in the same session. ~+100 regression tests across the sweep; full unit suite at the tip contains the guards for every finding._
