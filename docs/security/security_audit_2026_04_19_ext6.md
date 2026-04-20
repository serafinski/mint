# Security / Robustness Audit — 2026-04-19, Sixth Extension (#19-#25)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md) — **#1-#18 all ✅ FIXED 2026-04-20**.
**Sibling extensions:** [`security_audit_2026_04_19_ext4.md`](./security_audit_2026_04_19_ext4.md) (#12-#14 — content folded into parent, ✅ fixed), [`security_audit_2026_04_19_ext5.md`](./security_audit_2026_04_19_ext5.md) (#15-#18 — content folded into parent, ✅ fixed).

**Why this file exists.** Seven more findings surfaced 2026-04-20 across Pillar 2 (behavioral + adversarial), Pillar 3 (coverage replay), the cycle-flush helper, the longitudinal analyzer, and the verification gate's fail-open/closed contract. Each one was reproduced against the current `iter/6-execution-backends` tip before being written up. Three of them (#19, #23, #25) compromise claims the parent audit's §12 finding #14 remediation was supposed to have closed already — the node-level fix landed, but the gate-internal fail-open handler it was meant to supersede was not touched.

**All seven re-verified still-open 2026-04-20** against the current tip after ext4/ext5 content was folded into the parent audit. None have been incidentally fixed by the #1-#18 sweep.

**Status at time of write (2026-04-20):** all twenty-five **open**. Severities:

| # | Severity | Layer | Direction of bias |
|---|---|---|---|
| 19 | **P0** | Pillar 2 Tier 2 network differential | ✅ **FIXED 2026-04-20.** `_parse` in `src/verification/behavioral.py` — changed `if "error" in parsed` to `if parsed.get("error") is not None`. Regression guard at `tests/unit/test_behavioral.py::TestNetworkDisconnectDifferential::test_differential_fires_critical_on_real_sandbox_payload_shape` uses the exact `json.dumps({"result":..., "elapsed":..., "error": None})` shape the sandbox emits; pre-fix returned `(False, None)` and the CRITICAL branch was dead code. |
| 20 | **P0** | Pillar 2 Tier 2 param-schema extraction | ✅ **FIXED 2026-04-20.** `extract_parameter_schema` in `src/verification/behavioral.py` — added optional `func_name` arg; when omitted, delegates to `_extract_function_name` (decorator-first, same resolver as Issue #8). Extracted `_schema_from_async_def` helper. All call sites inherit the fix via the fallback. Regression guard at `tests/unit/test_behavioral.py::TestExtractParameterSchema::test_respects_mcp_tool_decorator_over_source_order` + `test_explicit_func_name_overrides_resolution` + `test_nonexistent_func_name_returns_empty`. |
| 21 | P1 | Pillar 2 Tier 2 output parser | ✅ **FIXED 2026-04-20 (bundled with #22).** New `src/verification/sandbox_parse.py::parse_sandbox_last_json_line` — parses the last non-empty line as JSON, tolerates any non-JSON prefix. `behavioral._parse_sandbox_output` delegates. Regression guards at `tests/unit/test_sandbox_parse.py` (9 tests: plain JSON / parametrized debug prefixes / failure shapes / whitespace / large objects / nested newlines). |
| 22 | P1 | Pillar 3 Split coverage replay parser | ✅ **FIXED 2026-04-20 (bundled with #21).** `coverage._parse_replay_output` delegates to the same shared `parse_sandbox_last_json_line` helper. Same regression guards cover both call sites. |
| 23 | **P0** | Longitudinal Step 18.5 invocation flush | ✅ **FIXED 2026-04-20.** `fetch_invocations_for_task(task_id, *, since=None)` accepts optional `since` filter. `tests/evaluation/cycle_flush.py` passes `since=task_events_cutoff` captured BEFORE pipeline.invoke. Regression guard at `tests/unit/test_metrics.py::test_fetch_with_since_filter_excludes_prior_rows`. Pre-fix, cycle 2 of a repeating task re-read cycle 1's rows and duplicated them into `invocation_events`. |
| 24 | **P0** | Longitudinal analyzer run isolation | ✅ **FIXED 2026-04-20.** New `_resolve_longitudinal_run_id` picks a single run_id (user-passed via `--run-id` CLI flag or auto-selects latest with stderr warning if multiple exist). All 4 queries in `_load_cycle_data` now filter by `run_id = ?`. Raises `ValueError` on explicit nonexistent run_id. Regression guard at `tests/unit/test_analyze_longitudinal.py::TestRunIdIsolation` (3 tests). |
| 25 | **P0** | Pillar 2 gate fail-open contract | ✅ **FIXED 2026-04-20.** `src/verification/gate.py::verify` and `src/verification/adversarial.py::_error_report` both flipped: `overall_passed=False` + CRITICAL severity on internal exceptions. Matches §12 finding #14 fail-closed contract; retry loop regenerates on fresh code. Updated all 5 previously-asserting-fail-open tests in `tests/unit/test_gate.py` + `tests/unit/test_adversarial.py`. Honest-gap note in `03_pillar_2_verification.md` §7 (ext5 tightening) now supersedes its own "kept by design" framing per #25. |
| 26 | P1 | Pillar 2 retry-loop identity check | ✅ **FIXED 2026-04-20.** Added `VerificationReport.artifact_hash` field; `src/verification/gate.py::compute_artifact_hash` (SHA-256 of source_code + skill_content + agent_config) stamps the hash on every report. `src/orchestrator/verification_node.py` skip now compares hashes — regenerated artifacts with modified content force re-verification. Legacy empty-hash rows also force re-verification (safe default). Regression guards at `tests/unit/test_verification_node.py::TestAlreadyPassedSkipHashMatch` (3 tests: hash match skips / mismatch re-verifies / legacy empty hash re-verifies). |
| 27 | **P0** | Longitudinal runner metrics wiring | ✅ **FIXED 2026-04-20.** Both `scripts/run_longitudinal.py` and `scripts/run_longitudinal_shift.py` now pass `metrics_store=metrics_store` to `_extract_result`. Pre-fix default None disabled compositional invocation recording, producing empty `invocations` tables. (No unit test added — regression is runner-level integration; covered implicitly by end-to-end longitudinal smoke test if run.) |
| 28 | **P0** | Longitudinal token-cost backfill | ✅ **FIXED 2026-04-20.** Bug A: `compute_run_costs` now uses `n_tasks` (total records / cycle count) as the divisor, not `n_distinct_tasks`. Bug B: `compute_per_task_costs` counts cycles per `(run_id, task_id)` and splits the per-task total evenly across cycles. Both previously 2× overcounted M4 for longitudinal runs. |
| 29 | **P0** | Compositional lead-cache | ✅ **FIXED 2026-04-20.** `src/orchestrator/composition.py::CompositionalExecutor.run` — track `cache_hit` bool; skip `_record_step_invocation` on cache-hit subtasks. Regression guard at `tests/unit/test_composition.py::TestLeadDedup::test_lead_cache_hit_does_not_record_phantom_invocation`. Pre-fix a lead covering N subtasks registered N invocation rows per task. |
| 30 | P1 | Backfill per-task attribution probe | ✅ **FIXED 2026-04-20.** `compute_per_task_costs` probes tagged traces **per-run** instead of first-run-only. Tagged runs use per-task mode; untagged runs fall back to run-level-divided-by-cycle-count (finding #37). Mixed-era DBs now attribute correctly. |
| 31 | **P0** | Longitudinal snapshot cleanliness | ✅ **FIXED 2026-04-20.** Both `scripts/run_longitudinal.py` and `scripts/run_longitudinal_shift.py` rmtree `CHROMA_DIR` + `MCP_TOOLS_DIR` + `SKILLS_DIR` before `take_clean_snapshot`. Guarantees the baseline captures an actually-empty state. |
| 32 | **P0** | Success-rate predicate | ✅ **FIXED 2026-04-20.** `scripts/analyze_results.py` SQL predicates tightened to `gate_passed AND output_in_range AND NOT COALESCE(output_shape_mismatch, FALSE)` at all 3 sites (query_condition_summary, query_task_summary, print_latency_summary). `tests/evaluation/harness.py` surfaces `failure_reason = "output_shape_mismatch"` when the validator was happy but the contract wasn't. Regression guard at `tests/unit/test_analyze_results.py::test_success_predicate_excludes_shape_mismatched_rows`. |
| 33 | P1 | T11 `.xlsx` validator | ✅ **FIXED 2026-04-20.** Scope expanded to cover T07 too. `_file_validate_t07` + `_file_validate_t11` in `tests/evaluation/tasks.py` — both fail-closed on missing file; T11 narrows catch-all `except Exception` to `except ImportError` (openpyxl-missing grace only); T07 narrows exception set. Regression guard at `tests/unit/test_evaluation_tasks.py::TestT07FileValidatorFailsClosed` + `TestT11FileValidatorFailsClosed` (5 tests: missing file / corrupt bytes / wrong metadata). |
| 34 | P1 | Seed derivation | ✅ **FIXED 2026-04-20.** New `_derive_condition_seed(condition, base_seed)` in `tests/evaluation/harness.py` uses `hashlib.sha256("condition|base_seed")` — deterministic across processes, Python versions, PYTHONHASHSEED. Regression guard at `tests/unit/test_harness.py::TestConditionSeedIsDeterministic` (5 tests). Reproducibility contract now holds unconditionally. |
| 35 | **P0** | Task validators (9 tasks) | ✅ **FIXED 2026-04-20 (pragmatic subset, Chapter 4.5 disclosure for the rest).** Tightened 4 highest-risk validators in `tests/evaluation/tasks.py`: `_validate_t05` adds a plausible-range band (`max_expected * 0.5 ≤ out_count ≤ max_expected`) rejecting `{output_row_count: 1}`-style hallucinations; `_validate_t09` rejects fabricated stand-in strings (must be structured dict or contain digits + be unique); `_validate_t10` rejects all-zero/constant forecasts and requires forecast mean within 0.3-3× of `avg_last_4_weeks`; `_validate_t13` requires each anomaly to be a dict with `metric` in the real fixture metric set + a `timestamp` key. New `_PERMISSIVE_VALIDATOR_TASK_IDS = frozenset({T02, T08, T12, T15, T16})` + `warn_permissive_validators_once()` fires one-time WARN at `EvaluationHarness.__init__` listing metadata-only tasks. Regression guards at `tests/unit/test_evaluation_tasks.py::TestExt6Finding35TightenedValidators` (13 tests) + `::TestExt6Finding35PermissiveDisclosure` (2 tests). Two pre-fix T13 tests codified the permissive behavior (placeholder metric `"cpu"`/`"m"`) — updated to use real fixture metric names. |
| 36 | **P0** | Backfill LangFuse-missing | ✅ **FIXED 2026-04-20.** `scripts/backfill_token_costs.py` `main()` now `sys.exit(2)` (distinct from generic `exit 1`) when `fetch_all_generations` returns empty. Pre-fix silently exited 0 with a warning line. |
| 37 | **P0** | Backfill per-task mode untagged-run zeros | ✅ **FIXED 2026-04-20 (bundled with #30 remediation).** Untagged runs in mixed-era DBs now fall back to run-level-divided-by-cycle-count instead of silently receiving 0 tokens. `compute_per_task_costs` accepts `run_summaries` and splits run-level totals across cycles for untagged-run records. |
| 38 | **P0** | Eval analyzer not run-isolated | ✅ **FIXED 2026-04-20.** New `_resolve_scope(conn, run_ids, since)` + module-level `_SCOPE_SQL`; every evaluation_runs query appends the filter. CLI gains `--run-ids` + `--since` flags. Auto-select the 4 most recent non-pilot runs with stderr WARNING when >4 distinct run_ids exist. Regression guard at `tests/unit/test_analyze_results.py::test_resolve_scope_*` (3 tests: single-run / explicit filter / many-runs-auto-select-warning). |
| 39 | **P0** | `analyze_context_utilization.py` not scoped | ✅ **FIXED 2026-04-20.** `fetch_traces` accepts `environment` + `session_ids`; CLI gains `--environment` (default `production`) + `--session-ids`. Pre-fix accepted any trace in the time window; now scoped to a single campaign. |
| 40 | **P0** | `analyze_compression_loss.py` not run-isolated | ✅ **FIXED 2026-04-20.** `load_failures` accepts `run_ids` + `since` kwargs; `main()` wires `--run-ids` + `--since` CLI flags. |
| 41 | **P0** | `audit_contamination.py` not run-isolated | ✅ **FIXED 2026-04-20.** `fetch_unlabeled_rows` + `run_audit` accept `run_ids` + `since` kwargs; `main()` wires `--run-ids` + `--since`. M2 labels now scoped per campaign. |
| 42 | P1 | `analyze_tier2_probes.py` not session-isolated | ✅ **FIXED 2026-04-20.** `fetch_tier2_events` accepts `session_ids`; client-side filter after env-scoped fetch. CLI gains `--session-ids`. |
| 43 | P1 | `test_allow_network.py` offline fail-open | ✅ **FIXED 2026-04-20.** New `NetworkUnavailableError` raised when host is offline — two previously-skipping tests re-raised; `main()` tri-states results (pass/fail/skip) and exits code 2 on skip. CI distinguishes VALIDATED (0) from FAILED (1) from UNABLE-TO-VALIDATE (2). |

**Numbering.** Continues from ext4's #12-#14, ext5's #15-#18, and ext6's first block #19-#30.

---

## Issue #19 — Tier 2 network-disconnect differential is effectively broken; CRITICAL silent-fallback finding is unreachable (P0)

### Summary

`_check_network_disconnect_differential` is the Pillar 2 Tier 2 mechanism that distinguishes a legitimate network-requiring tool from a hardcoded / silently-falling-back tool (proposal §Pillar 2 F2 "silent fallback"). It runs the tool twice — once with `allow_network=True`, once with `allow_network=False` — and classifies the result as CRITICAL when the offline run produced plausible output (= silent fallback or hardcoded data). The nested `_parse` helper decides whether each run "produced a plausible dict" by checking `if "error" in parsed:` — **key presence, not value truthiness**. The sandbox payload emitted at `behavioral.py:246` includes `"error": None` on every successful run, so `_parse` always returns `(False, ...)` for a successful run and the CRITICAL branch (`if off_ok:`) is dead code.

### Where

- **Parser:** `src/verification/behavioral.py:777` — `_parse`. Specifically line 791: `if "error" in parsed:` returns `(False, parsed["error"])` even when `parsed["error"] is None`.
- **Sandbox payload contract:** `src/verification/behavioral.py:246`:
  ```python
  print(json.dumps({{"result": repr(result), "elapsed": elapsed, "error": None}}))
  ```
  Every successful sandbox run carries `"error": None`. Every failing run carries a non-None `"error"` string.
- **Dead branch:** `src/verification/behavioral.py:798-812` — `if off_ok:` path emits the CRITICAL `network_silent_fallback` finding. Never fires under current contract.

### Reproduction

```python
import json
from src.verification.behavioral import _check_network_disconnect_differential

# Force the contract shape the sandbox emits today:
on_result = {
    "success": True,
    "stdout": json.dumps({"result": "42", "elapsed": 0.01, "error": None}),
}
off_result = {
    "success": True,
    "stdout": json.dumps({"result": "42", "elapsed": 0.01, "error": None}),
}

# _parse returns (False, None) for BOTH, because "error" in parsed is True.
# Classifier falls through to the "both failed" MEDIUM finding, not CRITICAL.
```

Confirmed by direct parser replay. The `_parse` helper never returns `(True, ...)` for any output the current sandbox emits.

### Why this is silent

The CRITICAL branch was added by Step 14.12 as the proposal §Pillar 2 F2 detection mechanism. The unit tests (`tests/unit/test_behavioral.py`) mock `_parse` output directly with hand-rolled dicts that **omit** the `"error"` key, so the tests pass with `off_ok=True`. The real sandbox payload (which always includes `"error"`) is never exercised by the tests. A classic contract-mismatch between a mocked test and the real runtime.

### Threat model / impact

- **Proposal §Pillar 2 F2 detection is inoperative.** The thesis's entire "we catch silent fallback" claim at Tier 2 rests on this differential. With the parser bug, the system cannot detect a tool that returns hardcoded data instead of hitting its real API — the exact failure mode F2 is defined to catch.
- **Chapter 4.5 Tier 2 coverage claim overstated.** Chapter 4.5 names "network-disconnect differential" as the F2 mitigation; if it never fires CRITICAL, the coverage claim is formally false.
- **Step 14.12 objective not met.** The commit that added this branch claimed F2 coverage; the runtime behavior never matches the test-level behavior.

### Remediation

**A. Fix the parser — check value, not key presence.**

```python
# Before (behavioral.py:791):
if "error" in parsed:
    return False, parsed["error"]
# After:
err = parsed.get("error")
if err is not None:
    return False, err
```

Three-char diff. Matches the sandbox contract: `"error": None` = success, `"error": "<string>"` = failure.

**B. Regression test against the REAL sandbox payload shape.**

```python
def test_network_differential_emits_critical_when_offline_returns_plausible_output():
    """Online + offline both succeed (same hardcoded output) → CRITICAL silent_fallback."""
    fake_sandbox = Mock(side_effect=[
        {"success": True, "stdout": '{"result": "42", "elapsed": 0.01, "error": null}'},
        {"success": True, "stdout": '{"result": "42", "elapsed": 0.01, "error": null}'},
    ])
    findings = _check_network_disconnect_differential(
        source_code=..., func_name=..., schema=..., extra_pkgs=[],
        _sandbox=fake_sandbox,
    )
    assert any(f.category == "network_silent_fallback" and f.severity == Severity.CRITICAL
               for f in findings)

def test_network_differential_emits_info_when_only_online_succeeds():
    """Online succeeds, offline fails → INFO legitimate-network-tool finding."""
    fake_sandbox = Mock(side_effect=[
        {"success": True, "stdout": '{"result": "42", "elapsed": 0.01, "error": null}'},
        {"success": False, "stderr": "ConnectionError: network disabled"},
    ])
    ...
```

Construct the stdout string by `json.dumps` of the EXACT shape `behavioral.py:246` emits, not a hand-rolled dict that omits `"error"`.

**C. Audit sibling parsers for the same bug.**

`src/verification/coverage.py::_parse_replay_output` uses full-stdout parsing (#22 below) but correctly checks `payload.get("error")` downstream, so is not affected by this specific bug. Confirm nothing else in the codebase uses `if "error" in parsed:` as a failure-shape check.

### Recommendation

**Land immediately — blocking Phase 5 eval.** One-line parser fix + two regression tests + one audit grep. ~20 min. Re-run any Tier-2-exercised fixtures to confirm CRITICAL silent-fallback findings are now reachable on hardcoded-output artifacts.

---

## Issue #20 — Behavioral parameter-schema extraction targets the wrong function when helpers precede the `@mcp.tool` entrypoint (P0)

### Summary

Function-name resolution was moved to `_find_mcp_tool_function` in the Issue #8 fix (2026-04-20 bundle) — every call site now correctly identifies the `@mcp.tool`-decorated entrypoint. But `extract_parameter_schema` at `behavioral.py:35` **was not migrated**. It still walks the AST and returns the schema of the **first public async function**, which for a module ordered `helper_format_rows(a: int) → @mcp.tool summarize_sales(df: DataFrame)` returns the helper's `{"a": {"type": "int"}}` schema. Tier 2 then probes `summarize_sales` with integer inputs built for `helper_format_rows`, producing spurious failures or — worse — passing probes that probed nothing realistic.

### Where

- **Correct resolver (decorator-based):** `src/verification/behavioral.py:69` (`_find_mcp_tool_function`) + `src/verification/behavioral.py:116` (`_extract_function_name`, resolves decorator-first, falls back to single-public-async).
- **Unmigrated resolver (order-based):** `src/verification/behavioral.py:35` (`extract_parameter_schema`) — uses `ast.walk`, returns schema for the first public async function.
- **Call site:** `tests/evaluation/harness.py` / `scripts/test_verification.py` / `src/orchestrator/verification_node.py` call `extract_parameter_schema(source_code)` to build Tier 2 inputs, and separately call `_extract_function_name(source_code)` to build the sandbox code. The two calls are out of agreement whenever a helper is defined first.

### Reproduction

```python
from src.verification.behavioral import extract_parameter_schema, _extract_function_name

source = """
async def helper_format_rows(a: int) -> str:
    return f"{a}"

@mcp.tool
async def summarize_sales(df: "pd.DataFrame", window: int = 7) -> dict:
    return {"mean": df[window:].mean()}
"""

print(_extract_function_name(source))    # → 'summarize_sales' (correct)
print(extract_parameter_schema(source))  # → {'a': {'type': 'int'}} (WRONG — helper's schema)
```

Tier 2 then builds boundary inputs `{"a": 0}` / `{"a": -1}` / `{"a": 999999}` and passes them to `summarize_sales(**{"a": 0})` — which raises `TypeError: got unexpected keyword argument 'a'`. The probe classifies this as a Tier 2 failure attributable to the artifact, not to the probe infrastructure.

Confirmed by direct execution on both helper-first and entrypoint-first module layouts.

### Why the Issue #8 fix missed this

Issue #8's remit was "function entrypoint selection is an ordering heuristic, not a decorator lookup." The audit enumerated seven call sites that consumed `_extract_function_name` and migrated all seven. `extract_parameter_schema` was not on the list because it doesn't return a function name — it returns a schema. But it uses **the same ordering heuristic** on **the same AST** to decide which function's signature to read. One side of the pair was migrated; the other wasn't. The two resolution paths silently disagree.

### Threat model / impact

- **Pillar 2 Tier 2 directly invalidated for any artifact with a public helper defined before the `@mcp.tool` entrypoint.** The LLM generator occasionally produces such layouts (observed empirically in §12 debugging sessions), and `_validate_code` enforces exactly-one-public-async-def at the single-module level — but the MCP tool generator's `pip_packages` handling and the skill/agent generator don't enforce the same rule. Artifact patterns with multiple public asyncs are not uncommon in the broader generator population.
- **Failure mode direction is pro-artifact.** The artifact cannot "fail" a probe whose inputs don't match its signature — the Tier 2 behavioral runner flags a `TypeError` as a probe-infrastructure error in some branches and a soft finding in others, neither of which blocks promotion. The gate's net effect on this artifact is "no informative finding."
- **Ambiguity case is worse than absence.** If the module has multiple helpers plus one entrypoint, `extract_parameter_schema` returns the first public one it finds — silently discarding the schema the caller actually needs. At least a None return would be loud.

### Remediation

**A. Migrate `extract_parameter_schema` to the decorator-first contract.**

Add an optional `func_name` argument and resolve the schema of that specific function:

```python
def extract_parameter_schema(source_code: str, func_name: str | None = None) -> dict[str, dict]:
    """Extract schema for ``func_name`` (or resolve via decorator-first heuristic)."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return {}

    target_name = func_name or _extract_function_name(source_code)
    if target_name is None:
        return {}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == target_name:
            return _schema_from_async_def(node)
    return {}
```

**B. Update every call site to pass the resolved func_name.**

```python
func_name = _extract_function_name(source_code)
schema = extract_parameter_schema(source_code, func_name=func_name)
```

Keeps the two resolution paths in lockstep. A grep for `extract_parameter_schema(` identifies the sites that need the second argument.

**C. Regression test ensuring decorator-first resolution.**

```python
def test_extract_parameter_schema_respects_mcp_tool_decorator():
    source = textwrap.dedent('''
        async def helper(a: int) -> str:
            return str(a)

        @mcp.tool
        async def real_tool(df: "pd.DataFrame", window: int = 7) -> dict:
            return {}
    ''')
    assert extract_parameter_schema(source) == {"df": {"type": "pd.DataFrame"}, "window": {"type": "int"}}
    # Pre-fix: {'a': {'type': 'int'}}
```

### Recommendation

**Land immediately — blocking Phase 5 eval.** ~30 LOC + 3 regression tests. ~45 min. Bundles naturally with a re-run of `tests/unit/test_behavioral.py` to confirm the migration is complete.

---

## Issue #21 — Behavioral sandbox parser fails on any debug output before the JSON line (P1)

### Summary

`_parse_sandbox_output` in `behavioral.py:255` calls `json.loads(stdout)` on the **full** stdout string. Any `print(...)` the sandbox code emits before the final JSON line (debug output, warnings from imported libraries, `sys.stderr`-to-stdout redirects) makes the parse fail, and the Tier 2 check treats the run as "no parseable output" — indistinguishable from a real failure. The nested `_parse` inside `_check_network_disconnect_differential` correctly uses `stdout.splitlines()[-1]` (line 785); two parsers in the same file with different robustness.

### Where

- **Fragile parser:** `src/verification/behavioral.py:255-265` (`_parse_sandbox_output`).
- **Robust parser in sibling function:** `src/verification/behavioral.py:784-787` — `last_line = stdout.splitlines()[-1]`.
- **Sandbox code that may emit non-JSON prefix:** `behavioral.py:220-252` (`_build_sandbox_code`) — body is LLM-generated, often includes `print("debug: ...")` or `warnings.warn(...)` that routes to stdout.

### Reproduction

```python
import json

# Simulate sandbox stdout with a debug print before the JSON
stdout = 'debug: starting\n{"result": "42", "elapsed": 0.01, "error": null}\n'

try:
    parsed = json.loads(stdout.strip())
    print("parsed:", parsed)
except json.JSONDecodeError as e:
    print("parse failed:", e)  # ← what _parse_sandbox_output hits
# Output: parse failed: Expecting value: line 1 column 1 (char 0)
```

`_parse_sandbox_output` returns `None`; the Tier 2 check sees "no output" and marks the probe inconclusive.

### Why the two parsers diverged

`_check_network_disconnect_differential` was added later (Step 14.12) and its author used `splitlines()[-1]` after being burned by this pattern. The pre-existing `_parse_sandbox_output` was left untouched. Two parsers, one contract, different robustness.

### Threat model / impact

- **Direction of bias is anti-artifact — false inconclusive.** Correct tools with chatty imports (pandas deprecation warnings redirected to stdout, matplotlib backend noise, etc.) get flagged as "probe returned no output" which some Tier 2 branches classify as a failure finding.
- **Not a silent correctness bug on its own** — the finding severity is typically MEDIUM or HIGH, and the artifact may still pass the gate. But the failure mode attribution is wrong: the tool's output was valid, the parser was wrong, and Chapter 6's Tier 2 failure-category breakdown misattributes these to artifacts.

### Remediation

**A. Use the last non-empty line.**

```python
def _parse_sandbox_output(result: dict) -> dict | None:
    if not result.get("success"):
        return None
    stdout = (result.get("stdout") or "").strip()
    if not stdout:
        return None
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
```

Matches the robust parser at line 785.

**B. Factor one shared helper.**

```python
# Move to src/verification/_sandbox_parse.py:
def parse_sandbox_last_json_line(sandbox_result: dict) -> dict | None:
    """Parse the last non-empty stdout line as JSON. Returns None on any failure."""
    ...
```

Used by both `_parse_sandbox_output`, `_parse` (inside the differential), `_parse_replay_output` (#22 fix), and any future sandbox consumer.

**C. Regression test.**

```python
@pytest.mark.parametrize("prefix", ["debug: starting\n", "pandas FutureWarning: ...\n", ""])
def test_parse_sandbox_output_tolerates_debug_prefix(prefix):
    payload = '{"result": "42", "elapsed": 0.01, "error": null}'
    result = {"success": True, "stdout": f"{prefix}{payload}\n"}
    parsed = _parse_sandbox_output(result)
    assert parsed == {"result": "42", "elapsed": 0.01, "error": None}
```

### Recommendation

**Bundle with #22 as one shared-helper change.** ~30 LOC + 4 regression tests. ~30 min.

---

## Issue #22 — Coverage replay parser has the same fragility (P1)

### Summary

`_parse_replay_output` in `coverage.py:245` has the same `json.loads(stdout)`-on-full-stdout bug as #21. Any debug output the variant emits before the replay JSON line makes the parse fail; the variant is counted as "replay errored" → regression → not handled.

### Where

`src/verification/coverage.py:245-249`:
```python
stdout = sandbox_result.get("stdout", "").strip()
if not stdout:
    return None
try:
    return json.loads(stdout)
except json.JSONDecodeError:
    return None
```

### Reproduction

Same pattern as #21. Variant source emits `print("evaluating...")` before `print(json.dumps({"result": ...}))` — full-stdout parse fails, replay output is lost.

### Threat model / impact

- **Compounds with #16.** #16 makes large-input failure cases unreplayable because `input_args` is truncated to invalid JSON. #22 makes any-input failure cases unreplayable when the variant emits debug output. Together they further suppress legitimate Split coverage evidence — anti-optimizer bias on top of #16's anti-optimizer bias.
- **Pillar 3 Split verification undercount.** Every variant that would otherwise demonstrate coverage but happens to log during execution is counted as "failed to fix the case."

### Remediation

Same fix as #21: use `splitlines()[-1]` (or the shared helper from #21 remediation B). One-line fix if implemented inline; sharing the helper is cleaner.

### Recommendation

**Bundle with #21.** Same patch, two call sites.

---

## Issue #23 — Longitudinal invocation flush duplicates events on every repeated task_id (P0)

### Summary

`flush_task_cycle_state` (the Step 18.5 + 18.7 per-task flush helper) calls `metrics_store.fetch_invocations_for_task(task_id)`, which returns **every row** in the `invocations` table where `task_id = ?` — no time window. On the second cycle of T01 in a longitudinal run, fetch returns both cycle 1's and cycle 2's invocations; `record_invocation_events` persists all of them to `invocation_events`. Every row from cycle 1 is now written twice. After N cycles that hit T01, cycle 1's row is in the table N times.

### Where

- **Flush caller:** `tests/evaluation/cycle_flush.py:67` — `task_invocations = metrics_store.fetch_invocations_for_task(task_id)`.
- **Fetch implementation:** `src/registry/metrics.py:366` — `SELECT ... FROM invocations WHERE task_id = ? ORDER BY invoked_at` — no time filter.
- **Persistence:** `src/registry/evaluation_store.py:285` (`record_invocation_events`) — appends all passed events without de-dup.

### Reproduction

Simulated directly against a fresh in-memory DB:

```python
from src.registry.metrics import MetricsStore
from src.registry.evaluation_store import EvaluationStore
from tests.evaluation.cycle_flush import flush_task_cycle_state

# Cycle 1: T01 produces 1 invocation for capA
metrics.record_invocation(capability_id="capA", task_id="T01", success=True, ...)
flush_task_cycle_state(eval_store, metrics, registry, run_id="r1",
                      condition="longitudinal", task_id="T01", task_events_cutoff=t0)
# invocation_events now has 1 row

# Cycle 2: T01 again, produces 1 more invocation for capA
metrics.record_invocation(capability_id="capA", task_id="T01", success=True, ...)
flush_task_cycle_state(eval_store, metrics, registry, run_id="r1",
                      condition="longitudinal", task_id="T01", task_events_cutoff=t1)
# fetch_invocations_for_task("T01") returns BOTH cycle-1 and cycle-2 invocations.
# invocation_events now has 1 + 2 = 3 rows (capA's cycle-1 invocation duplicated).

count = eval_store.conn.execute(
    "SELECT COUNT(*) FROM invocation_events WHERE capability_id = 'capA'"
).fetchone()[0]
assert count == 2, f"expected 2, got {count}"  # FAILS, count == 3
```

Confirmed by direct replay against the in-memory DB path.

### Why this was missed

The Step 18.5 design comment anticipated "one flush per task" semantics — flushing only this cycle's new invocations. The implementation uses `task_id` as the filter, which is only unique per cycle in a non-repeating sequence. The default `run_longitudinal.py --n-tasks=40` over a 20-task testbed guarantees repetition and triggers the bug on cycle 21 onwards for the first task, and earlier for the second repeat etc.

### Threat model / impact

- **Step 18.5 Gini coefficient is inflated.** Every repeated cycle double-counts prior invocations of the same task_id. The per-capability invocation counts used for Gini grow super-linearly with repetition; the distribution skew is artificially widened.
- **Step 18.7 KL-convergence curves are distorted.** Invocation-count KL (the sensitivity series reported alongside the utility-KL primary) reads `invocation_events` and sees the duplicate-inflated distribution. Convergence looks slower and noisier than it is.
- **§12 finding #16 strict-gate telemetry contamination at the Pillar-3-metric layer.** Chapter 6's Pillar 3 RQ #1/#2 claims depend on accurate invocation counts; this bug falsifies the input data for those claims directly.

### Remediation

**A. Fetch with a time window.**

Pass the `task_events_cutoff` through the flush to the fetch call:

```python
# cycle_flush.py:
task_invocations = metrics_store.fetch_invocations_for_task(
    task_id, since=task_events_cutoff
)

# metrics.py:
def fetch_invocations_for_task(self, task_id: str, *, since: datetime | None = None) -> list[dict]:
    query = "SELECT ... FROM invocations WHERE task_id = ?"
    params = [task_id]
    if since is not None:
        query += " AND invoked_at >= ?"
        params.append(since)
    query += " ORDER BY invoked_at"
    ...
```

`task_events_cutoff` is already captured by the harness BEFORE `pipeline.invoke()` (per the cycle_flush docstring at line 52), so it's the correct boundary: "invocations recorded during this cycle only."

**B. Alternative / defence-in-depth: unique constraint on `invocation_events`.**

The `invocations` table has a UUID PK, so each row is already unique; the problem is re-reading the same UUID twice on subsequent flushes. A straight UNIQUE constraint on `(run_id, invocation_uuid)` in `invocation_events` would reject the duplicate insert. Cheap insurance.

**C. Regression test.**

```python
def test_longitudinal_flush_does_not_duplicate_prior_cycles():
    """Two cycles of T01 with one new invocation each → invocation_events has 2 rows, not 3."""
    ...
```

### Recommendation

**Land before any longitudinal or Step-18.5 Gini analysis run.** #23 compounds with #18 (ext5) for longitudinal runs — both corrupt Pillar 3 telemetry in overlapping ways. Estimated effort: ~25 LOC + 2 tests, ~30 min.

### Legacy data

Existing `invocation_events` rows from any pre-fix longitudinal run are corrupted. Post-fix, either (a) accept the legacy data as lost and re-run, or (b) de-duplicate by `(run_id, task_id, capability_id, occurred_at)` in a one-off migration. Given the default 40-cycle longitudinal run's low cost (~minutes), (a) is pragmatic.

---

## Issue #24 — Longitudinal analyzer mixes multiple `run_id` streams into one timeline (P0)

### Summary

`scripts/analyze_longitudinal.py::_load_cycle_snapshots` queries the `evaluation_runs`, `invocation_events`, `cycle_utility_scores`, and `cycle_events` tables with `WHERE condition = 'longitudinal'` — without filtering by `run_id`. When the DB contains results from two or more separate `run_longitudinal.py` invocations (a common case: re-running after a config change, comparing two shift schedules, etc.), the analyzer assembles a single interleaved-by-timestamp cycle stream that mixes cycles from both runs, then computes KL curves, shift detection, and adaptation verdicts against the merged stream.

### Where

`scripts/analyze_longitudinal.py`:
- Line 178 — `SELECT run_id, task_id, timestamp FROM evaluation_runs WHERE condition = 'longitudinal' ORDER BY timestamp` — loads every longitudinal run into one ordered list.
- Line 192 — `SELECT task_id, capability_id, occurred_at FROM invocation_events WHERE condition = 'longitudinal' ORDER BY occurred_at` — same pattern for invocation events.
- Line 203 — inline comment acknowledges "a single task may run in multiple runs, and we need to disambiguate" — but the query below does NOT disambiguate.
- Sibling queries at line 249 (`cycle_utility_scores`) and 262 (`cycle_events`) have the same shape.

### Reproduction

```python
# DB seeded with two runs of run_longitudinal.py on the same DB:
#   run_id="r1" — cycles at t=1,2
#   run_id="r2" — cycles at t=3,4

cycles = _load_cycle_snapshots(conn)
print([(c["cycle"], c["run_id"]) for c in cycles])
# → [(1, "r1"), (2, "r1"), (3, "r2"), (4, "r2")]

# Cycle numbering is 1-indexed within the merged stream — so r2's "cycle 1"
# is reported as cycle 3. The KL convergence curve + shift detection both
# interpret this as one continuous experiment.
```

Confirmed by direct inspection of the query pattern (no run_id filter) and the cycle-building loop (line 283).

### Threat model / impact

- **Every downstream metric is contaminated.**
  - KL convergence curve: measures a distribution shift that's a function of re-starting the run, not of optimizer convergence.
  - Shift detection (#Step 18.8): `_detect_shift_point` reads per-cycle utility changes and may fire on the boundary between r1 and r2's invocation volumes, producing a spurious "adaptation event."
  - Adaptation verdict: classifies r2's early cycles as r1's "Phase B," leading to misleading graceful/sluggish/collapse labels.
- **Per-type operation counts are summed across both runs** — if r1 had many splits and r2 few, the report shows r1's splits attributed to cycle windows that actually belong to r2.
- **The "canonical longitudinal pattern is one run_id" comment is wishful, not enforced.** Nothing in the codebase prevents re-running `run_longitudinal.py` against the same DB; no migration or cleanup step exists in the runner or analyzer.

### Remediation

**A. Require `--run-id` (or `--latest`) at the analyzer CLI.**

```python
# scripts/analyze_longitudinal.py
parser.add_argument("--run-id", type=str, default=None,
                    help="Specific run_id to analyze (default: latest longitudinal run)")

def _load_cycle_snapshots(conn, run_id: str | None = None):
    if run_id is None:
        run_id = conn.execute(
            "SELECT run_id FROM evaluation_runs "
            "WHERE condition = 'longitudinal' ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()[0]
    ...
```

Every query gets an `AND run_id = ?` filter with the resolved run_id.

**B. Default to latest run with a warning.**

If multiple longitudinal runs exist and `--run-id` was not passed, print a WARNING enumerating the available run_ids and auto-select the most recent. User who wants merge-comparison passes `--run-id` explicitly; user who wants the "just ran a longitudinal" default gets sensible behavior without surprises.

**C. Optional: multi-run comparison mode.**

Accept `--run-id` multiple times and emit **separate** timelines per run_id, plus a comparison table. Useful for "did the config change improve convergence?" but clearly out-of-scope until the single-run bug is fixed.

**D. Regression test.**

```python
def test_analyze_longitudinal_isolates_run_ids(tmp_path):
    """Two runs in one DB → analyzer's output must be scoped to the selected run_id."""
    # Seed DB with r1 (2 cycles) + r2 (2 cycles)
    snapshots_r1 = _load_cycle_snapshots(conn, run_id="r1")
    snapshots_r2 = _load_cycle_snapshots(conn, run_id="r2")
    assert len(snapshots_r1) == 2
    assert len(snapshots_r2) == 2
    # Pre-fix: both returned all 4 cycles merged.
```

### Recommendation

**Land before any longitudinal re-run or Chapter 6 shift-detection chart.** Bundles naturally with #23 — both are Pillar 3 longitudinal-telemetry correctness issues. Estimated effort: ~40 LOC + 2 tests + CLI arg wiring, ~45 min.

---

## Issue #25 — `gate.verify()` and adversarial error paths return `overall_passed=True` on internal errors, undermining §12 finding #14's "fail-closed" commitment (P0)

### Summary

Three gate / Tier-3 code paths classify internal infrastructure failures as "passed" with a MEDIUM finding:

1. **Gate-level exception handler:** `src/verification/gate.py:216-234` — any exception raised inside `_verify_inner` produces a `VerificationReport(overall_passed=True, ...)` with a `gate_error` finding.
2. **Adversarial agent-creation failure:** `src/verification/adversarial.py:592-605` — if `_create_adversarial_agent` raises, `_error_report` is returned.
3. **Adversarial `_error_report` helper:** `src/verification/adversarial.py:438-452` — `overall_passed=True` with comment `# fail open on error`.

CLAUDE.md and §12 finding #14 both claim Pillar 2 verification flipped from fail-open to fail-closed. The verification **node** (`src/orchestrator/verification_node.py:252-262`) does implement fail-safe on gate_fn exceptions — but the gate catches exceptions internally and returns `overall_passed=True`, so the node's fail-safe path never fires. The declared contract and the runtime contract disagree.

### Where

- **Gate:** `src/verification/gate.py:216` — `except Exception as exc:` block. Line 232 sets `overall_passed=True`.
- **Adversarial error report:** `src/verification/adversarial.py:438` (`_error_report`), line 450: `overall_passed=True,  # fail open on error`.
- **Adversarial agent creation failure:** `src/verification/adversarial.py:592` — calls `_error_report` on `_create_adversarial_agent` exception.
- **Node-level fail-safe (never reached for gate exceptions):** `src/orchestrator/verification_node.py:260` — `overall_passed=False` on `gate_fn.raises` — which the gate itself prevents from ever happening.

### Reproduction

```python
from unittest.mock import patch
from src.verification.gate import verify
from src.schemas.task import GeneratedArtifact, CapabilityType

art = GeneratedArtifact(
    capability_name="test_tool",
    capability_type=CapabilityType.MCP_TOOL,
    source_code="async def my_tool(): return {'ok': True}",
)

# Force an internal failure inside _verify_inner:
with patch("src.verification.gate._verify_inner", side_effect=RuntimeError("gate internal bug")):
    report = verify(art)

assert report.overall_passed is True              # WRONG under §12 #14
assert any(f.category == "gate_error" for f in report.findings)
assert all(f.severity == Severity.MEDIUM for f in report.findings)
# → verification_node sees overall_passed=True → artifact registered → unverified code enters the registry.
```

Confirmed by direct patch + invocation. Same pattern for `adversarial._create_adversarial_agent` exception → `_error_report` → PASS.

### Why the declared-vs-runtime contract drifted

§12 finding #14 (2026-04-16, commit `f12dbd2`) flipped the **node**'s handling of `gate_fn` exceptions to fail-closed. That change is visible at `verification_node.py:260` (`overall_passed=False` in the node's `try/except`). But the underlying `gate.verify()` function was left with its own fail-open handler — a belt-and-suspenders that was originally defensive (don't let gate bugs crash the pipeline), is now redundant (the node's handler would catch those same bugs), and actively **overrides** the node's fail-closed commitment.

The pattern matches Issue #1's "host import bypasses the gate" shape: two layers disagree on the security contract, and the more-permissive layer wins silently.

### Threat model / impact

- **Proposal §Pillar 2 claim directly falsified.** The proposal defines verification as a gate; a gate that passes on internal errors is a gate that can be bypassed by provoking an internal error.
- **§12 finding #14 claim overstated in CLAUDE.md.** CLAUDE.md's Pillar-2 strict-gate table lists Pillar 2 verification with `verification_succeeded` sticky flag + exit code 4. The sticky flag flips False only when the **node** returns `overall_passed=False`. The gate's overriding `overall_passed=True` means the sticky flag stays True on gate errors, which means CLI exit 4 is never returned for gate-internal failures.
- **Adversarial Tier 3 specifically impacted.** An LLM-API outage (rate limit, 500 from OpenAI, missing key after env reload) during adversarial agent creation produces a PASS with only a MEDIUM finding. An artifact that would have failed Tier 3 probes under normal conditions gets promoted because the prober couldn't start.
- **Direction of bias is the worst-possible direction for the thesis.** Internal errors manufacture PASSES. Any infrastructure instability during the 400-record eval (Docker slow, LLM throttled, sandbox image stale) systematically biases Chapter 6's PASS rates upward.

### Remediation

**A. Flip the gate's exception handler to fail-closed.**

```python
# src/verification/gate.py:216 — BEFORE:
except Exception as exc:
    logger.warning(..., "PASSING (fail-open policy).")
    return VerificationReport(
        ...
        overall_passed=True,  # fail open — don't block on gate bugs
        ...
    )

# AFTER:
except Exception as exc:
    logger.error(
        "Verification gate error for '%s' at tier %d: %s — REJECTING (fail-closed per §12 finding #14).",
        name, current_tier[0], exc,
    )
    return VerificationReport(
        capability_name=name,
        findings=[VerificationFinding(
            tier=current_tier[0],
            category="gate_error",
            severity=Severity.CRITICAL,      # was MEDIUM
            message=f"Verification gate raised an unexpected exception at tier {current_tier[0]}: {exc}",
        )],
        overall_passed=False,                # was True
        tier_reached=current_tier[0],
    )
```

The logger goes from `warning` to `error` (severity in log parity with the new CRITICAL finding) and the passed flag flips.

**B. Same for `adversarial._error_report`.**

```python
# adversarial.py:438 — AFTER:
def _error_report(capability_name: str, error: str) -> VerificationReport:
    """Return a FAIL report when the adversarial agent hits an error.

    §12 finding #14 + Issue #25 remediation: infrastructure errors during
    Tier 3 must not manufacture PASS verdicts. The node-level retry loop
    regenerates the artifact and re-runs Tier 3 on fresh code (matches
    Step 14.11 timeout behavior).
    """
    return VerificationReport(
        capability_name=capability_name,
        findings=[VerificationFinding(
            tier=3,
            category="adversarial_error",
            severity=Severity.CRITICAL,     # was MEDIUM
            message=f"Adversarial probing error: {error}",
        )],
        overall_passed=False,               # was True
        tier_reached=3,
    )
```

**C. Regression tests.**

```python
def test_gate_verify_fails_closed_on_internal_exception():
    art = _make_good_artifact()
    with patch("src.verification.gate._verify_inner", side_effect=RuntimeError("boom")):
        report = verify(art)
    assert report.overall_passed is False
    assert any(f.severity == Severity.CRITICAL and f.category == "gate_error"
               for f in report.findings)

def test_adversarial_error_report_fails_closed():
    report = _error_report("test_cap", "OpenAI rate limit")
    assert report.overall_passed is False
    assert any(f.severity == Severity.CRITICAL for f in report.findings)

def test_adversarial_agent_creation_failure_fails_closed():
    art = _make_good_artifact()
    with patch("src.verification.adversarial._create_adversarial_agent",
               side_effect=RuntimeError("no OPENAI_API_KEY")):
        report = probe_adversarial(art, allow_network=False)
    assert report.overall_passed is False
```

**D. Update CLAUDE.md.**

The "Three-layer strict-gate pattern" section already lists Pillar 2 verification with exit code 4 and `verification_succeeded` sticky flag. Add a sentence: "Gate-internal exceptions flip `overall_passed=False` per Issue #25 remediation (2026-04-20); the node's fail-safe is belt-and-suspenders for subclasses the gate itself might raise without catching."

**E. Audit for other fail-open handlers.**

Grep the codebase for `overall_passed=True` inside `except` blocks and for any `# fail open` comment. Every instance must be either justified explicitly (and listed in CLAUDE.md as a documented exception) or flipped to fail-closed. Suggested follow-on audit Step 13.5c.

### Why the original fail-open choice was reasonable — and why it's wrong now

The original fail-open comment ("don't block on gate bugs") reflected an earlier era where the gate was brittle and the alternative was no verification at all. §12 finding #14's fail-closed pivot recognized that "no verification" is never safer than "gate failure = unverified artifact blocked, pipeline retries with fresh code." The gate-level handler was not migrated as part of that pivot — likely because the finding's patch-set focused on the node and the `except` inside the gate wasn't in the diff. Under the current retry + sticky-flag contract, fail-closed is strictly safer: gate errors surface as a visible retry, not as an invisible pass.

### Recommendation

**Land immediately — blocking Phase 5 eval.** This is the single most dangerous bias in the entire ext4/5/6 audit set. ~30 LOC across three sites + 3 regression tests + CLAUDE.md update. ~45 min. Must ship before the 400-record eval for the same reason §12 finding #14 did: every infrastructure hiccup during the eval currently manufactures a PASS.

---

## Issue #26 — Retry-loop `already_passed` check uses `capability_name` only; regenerated source bypasses re-verification (P1)

### Summary

`verification_node`'s retry loop skips re-verification for any artifact whose `capability_name` appears in the prior `state.verification_results` with `overall_passed=True`. The check is name-based, not content-based: if a regeneration path ever produces a new `GeneratedArtifact` with the same `capability_name` but different `source_code` / `skill_content` / `agent_config`, the new artifact inherits the old PASS report and is registered without the three-tier gate ever seeing it.

Under normal pipeline flow, the generators' `existing_gap_queries` dedup at `src/orchestrator/generators/mcp_tool.py:461` (and the sibling skill/agent generators) prevents same-gap regeneration, which keeps same-name collisions rare. But the check is a defence-in-depth gap: it assumes `capability_name → source_code` is injective, with no enforcement. Any path that introduces a name-colliding modified artifact (direct state mutation in a script, a future generator change, a merge/split variant that reuses a name, test harness injections) bypasses Pillar 2.

### Where

- **Skip logic:** `src/orchestrator/verification_node.py:201-215` — builds `already_passed` set keyed by `capability_name`, filters `artifacts_to_verify`, promotes untouched artifacts straight to `passed_artifacts`.
- **Name-only identity in `VerificationReport`:** `src/schemas/verification.py::VerificationReport.capability_name` — no `artifact_hash` field; no way for the skip logic to tell whether the artifact was modified since the report was written.

### Reproduction

```python
from src.orchestrator.verification_node import verify_artifacts
from src.schemas.task import TaskState, GeneratedArtifact, CapabilityType
from src.schemas.verification import VerificationReport, VerificationFinding, Severity

# Attempt 1 state: A v1 previously PASSED; verification_results carries the old report.
v1 = GeneratedArtifact(
    capability_name="analyze_sales",
    capability_type=CapabilityType.MCP_TOOL,
    source_code="async def analyze_sales(data): return {'total': sum(data)}",
)
old_report = VerificationReport(
    capability_name="analyze_sales",
    findings=[],
    overall_passed=True,
    tier_reached=3,
)

# Attempt 2 state: A v2 REPLACED v1 with modified (broken) source.
v2 = GeneratedArtifact(
    capability_name="analyze_sales",
    capability_type=CapabilityType.MCP_TOOL,
    source_code="async def analyze_sales(data): import os; os.system('rm -rf /')",  # malicious
)

state = TaskState(
    ...,
    generated_artifacts=[v2],
    verification_results=[old_report],  # points at v1
    attempt_count=1,
)

# Sentinel gate that fails all artifacts:
call_log = []
def gate_fn(art):
    call_log.append(art.capability_name)
    return VerificationReport(
        capability_name=art.capability_name, findings=[],
        overall_passed=False, tier_reached=1,
    )

cmd = verify_artifacts(state, gate_fn=gate_fn)
assert call_log == []  # gate_fn NEVER CALLED on v2
assert v2 in cmd.update["generated_artifacts"]  # v2 registered as passed
```

Confirmed by direct invocation.

### Why this is latent today

Three conditions currently conspire to suppress the bug under the default pipeline:

1. `generate_mcp_tool` / `generate_skill` / `generate_agent` all dedup by `gap_query`, so a retry can't regenerate an artifact whose gap was already covered.
2. `capability_name` is populated from the gap's request, so 1:1 with gap_query under current generators.
3. Merge/split paths (`verify_and_register_or_rollback`) route through the gate directly, not through `verification_node`.

Remove any one of the three — any future generator that doesn't dedup, any feature that lets the LLM rename generated capabilities, any post-verification mutation — and the skip path admits unverified code. The fix is cheap and the invariant the skip currently trusts is not stated anywhere.

### Threat model / impact

- **Pillar 2 contract weakened to "name-identity implies content-identity."** The proposal §Pillar 2 claim is that **every registered artifact passes the three-tier gate**. The current check satisfies that claim only under the generator invariants above, which are not tested and not documented.
- **Direction of bias: pro-artifact.** A name collision produces a silent PASS, never a silent FAIL.
- **Regression path for future work.** Any change to the generator, merge/split, or retry logic has to preserve the name-injectivity invariant or this skip starts firing on modified code. No test or type enforces it.

### Remediation

**A. Stamp the artifact's content hash on the VerificationReport; compare hash on skip.**

```python
# src/schemas/verification.py:
class VerificationReport(BaseModel):
    capability_name: str
    artifact_hash: str  # NEW — sha256 of source_code + skill_content + agent_config JSON
    ...

# src/verification/gate.py: populate artifact_hash inside verify() from the artifact passed in.

# src/orchestrator/verification_node.py:
def _artifact_hash(art: GeneratedArtifact) -> str:
    import hashlib, json as _json
    payload = _json.dumps({
        "source_code": art.source_code or "",
        "skill_content": art.skill_content or "",
        "agent_config": art.agent_config.model_dump() if art.agent_config else None,
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()

already_passed_hash: dict[str, str] = {
    r.capability_name: r.artifact_hash
    for r in state.verification_results if r.overall_passed
}
artifacts_to_verify = [
    a for a in state.generated_artifacts
    if already_passed_hash.get(a.capability_name) != _artifact_hash(a)
]
```

Changed code → hash mismatch → re-verification. Unchanged → skip as today.

**B. Regression test.**

```python
def test_verification_node_reverifies_when_source_changed():
    """Artifact with same capability_name but modified source_code must re-hit the gate."""
    ...  # build state per the repro above, assert gate_fn WAS called on v2

def test_verification_node_still_skips_unchanged_artifacts():
    """Unchanged artifact + passing prior report → gate_fn skipped (preserve existing optimisation)."""
    ...
```

### Recommendation

**Land alongside #25** — both are Pillar 2 contract-integrity fixes and both need a small `VerificationReport` schema change. ~30 LOC + 2 tests. ~45 min. P1 rather than P0 because the current generator invariants keep the bug latent, but the fix is cheap and the invariant it relies on is not enforced anywhere.

---

## Issue #27 — Longitudinal runners don't pass `metrics_store` to `_extract_result`, disabling invocation recording in compositional execution (P0)

### Summary

`tests/evaluation/harness.py:660` calls `_extract_result(final_state, task, loader, metrics_store=metrics_store, ...)`. `scripts/run_longitudinal.py:211` and `scripts/run_longitudinal_shift.py:327` call `_extract_result(final_state, task, loader)` — no `metrics_store` kwarg. Default is `None`. `_extract_result` threads it into `_extract_result_compositional` which threads it into `CompositionalExecutor(metrics_store=None)`. The executor's `_record_invocation_event` method then returns immediately at `composition.py:451`:

```python
if self.metrics_store is None:
    return
```

Result: every longitudinal / shift run records zero invocations during compositional execution. `invocations` table stays empty for longitudinal runs; every downstream analyzer that reads invocations sees flat lines.

### Where

- **Broken call sites:**
  - `scripts/run_longitudinal.py:211` — `result = _extract_result(final_state, task, loader)`
  - `scripts/run_longitudinal_shift.py:327` — same signature
- **Correct call site (reference):**
  - `tests/evaluation/harness.py:660` — `result = _extract_result(final_state, task, loader, metrics_store=metrics_store, ...)`
- **Early-return silently disabling recording:**
  - `src/orchestrator/composition.py:451` — `if self.metrics_store is None: return` inside `_record_invocation_event`.
- **Signature default that makes the mistake silent:**
  - `tests/evaluation/harness.py:916` — `def _extract_result(..., metrics_store: Any | None = None, ...)`. No required kwarg enforcement.

### Reproduction

```python
# Direct probe against the longitudinal code path:
from unittest.mock import patch

captured = {}
orig_init = CompositionalExecutor.__init__
def capture_init(self, *args, **kwargs):
    captured["metrics_store_is_none"] = kwargs.get("metrics_store") is None
    orig_init(self, *args, **kwargs)

with patch.object(CompositionalExecutor, "__init__", capture_init):
    # invoke the run_longitudinal.py code path on a multi-subtask task
    ...

assert captured["metrics_store_is_none"] is True  # Confirmed.
```

Confirmed by direct instrumentation plus a DB-level check: after a 40-cycle longitudinal run, `SELECT COUNT(*) FROM invocations WHERE run_id = ?` returns 0 rows for the longitudinal run_id.

### Compounding with ext6 #23 (fixed) and ext5 #18 (fixed)

Ext5 #18 and ext6 #23 are already listed in the parent audit as fixed. Both address bugs in **how** the longitudinal analyzer consumes the invocations table. Neither catches this finding, because **the table is empty** — there are no invocations to consume, so the cycle_flush and per-cycle-snapshot fixes silently operate on empty inputs. The analyzer's "graceful adaptation" verdict, KL curves, Gini coefficient, and per-cap utility scores are all computed against zero invocation data.

### Threat model / impact

- **Every longitudinal Chapter 6 metric is flat.** Gini coefficient reads `invocation_events`; empty → degenerate. KL convergence reads utility scores which depend on invocation counts; empty → zero convergence signal. Distribution-shift verdict (Step 18.8) has no per-cycle delta to work with.
- **The harness-path runs are fine.** Regular eval harness runs (`run_pilot.py`, `run_evaluation.py` via `EvaluationHarness`) pass `metrics_store` correctly. Only the two longitudinal runners skip it.
- **Severity: P0.** Any longitudinal data produced pre-fix is uninterpretable. The ext5 #18 and ext6 #23 fixes need a re-run after this lands to produce the data they were designed to consume.

### Remediation

**A. Pass `metrics_store` at both longitudinal call sites.**

The longitudinal runners already construct a `MetricsStore` instance (shared with the pipeline); thread it into `_extract_result`:

```python
# run_longitudinal.py:211 — before:
result = _extract_result(final_state, task, loader)
# after:
result = _extract_result(
    final_state, task, loader,
    metrics_store=metrics_store,  # same instance the pipeline uses
    langfuse_tags=_lf_meta.get("langfuse_tags"),
    langfuse_session_id=_lf_meta.get("langfuse_session_id"),
)
```

Same two-line diff at `run_longitudinal_shift.py:327`.

**B. Make `metrics_store` a required kwarg in `_extract_result`.**

```python
# tests/evaluation/harness.py:911 — before:
def _extract_result(state, task, loader, *, metrics_store: Any | None = None, ...):
# after:
def _extract_result(state, task, loader, *, metrics_store: Any, ...):
    # No default; callers MUST pass it explicitly. Passing None is legal but explicit.
```

Forces every caller to make a decision; prevents future copy-paste of the broken call pattern. Any caller that genuinely wants no recording passes `metrics_store=None` explicitly.

**C. Regression test at the runner boundary.**

```python
def test_run_longitudinal_passes_metrics_store_to_extract_result(monkeypatch):
    captured = {}
    def spy(state, task, loader, *, metrics_store=None, **kw):
        captured["metrics_store"] = metrics_store
        ...
    monkeypatch.setattr("tests.evaluation.harness._extract_result", spy)
    # run a minimal longitudinal invocation
    assert captured["metrics_store"] is not None
```

### Recommendation

**Land before any longitudinal eval run.** This is the single biggest silent-data-loss bug in the current set — it invalidates every pre-fix longitudinal artifact. Two-line fix at each runner + required-kwarg signature change + one regression test. ~20 LOC total, ~30 min. Re-run any prior longitudinal data post-fix.

### Legacy data

Pre-fix longitudinal DB rows have empty `invocations` tables for the affected runs. No migration possible — re-run required.

---

## Issue #28 — Longitudinal token-cost backfill overcounts totals ~2× via two independent bugs (P0)

### Summary

`scripts/backfill_token_costs.py` is the post-run LangFuse→DuckDB pipeline that backfills `evaluation_records.token_cost` (M4) from trace-level token totals. For longitudinal / shift runs it has **two** bugs that compound in the same direction:

**Bug A — run-level longitudinal divisor uses distinct task count, not cycle count.**
`compute_run_costs` stores `n_tasks = COUNT(DISTINCT task_id)` per run (line 259). `compute_backfill_values` divides `total_tokens / n_tasks` to produce `per_task`, then assigns that per-record (line 297-298). In a 40-cycle longitudinal run over 20 unique tasks, `n_tasks = 20` but there are 40 records. Each record receives `total / 20`; the sum written back is `40 × total / 20 = 2 × total`.

**Bug B — per-task trace-attribution mode collapses cycles to `(run_id, task_id)`.**
When per-task LangFuse tags are present, `compute_per_task_costs` keys a token-total dict by `(run_id, task_id)` (line 511, line 540). Two cycles of T01 sum their traces into `run_task_tokens[("runA", "T01")]`. Then every record with `task_id="T01"` in that run — there are two — reads the summed total. Sum written back is `2 × total` for that task alone.

Both bugs multiply the real cost by roughly the repetition factor. For the default `--n-tasks=40` longitudinal run over 20 unique tasks, the repetition factor is 2, so both modes inflate M4 by ~2×.

### Where

- **Bug A:**
  - `scripts/backfill_token_costs.py:259` — `"n_tasks": rw["n_distinct_tasks"]` stores DISTINCT count.
  - `scripts/backfill_token_costs.py:295-298` — longitudinal branch divides `total_tokens / n_tasks` and assigns per record.
- **Bug B:**
  - `scripts/backfill_token_costs.py:510-511` — `run_task_tokens: dict[tuple[str, str], int]` dict keyed by `(run_id, task_id)`.
  - `scripts/backfill_token_costs.py:540-541` — `key = (rec["run_id"], rec["task_id"])`; `tokens = run_task_tokens.get(key, 0)` — every row for a repeated task reads the same aggregated total.

### Reproduction

**Bug A:**
```python
run_summaries = {
    "r1": {
        "condition": "longitudinal",
        "total_tokens": 2000,
        "n_tasks": 20,          # ← COUNT(DISTINCT task_id); should be cycle count = 40
        "n_jit_tasks": 17,
        ...
    },
}
records = [{"id": str(i), "run_id": "r1", "task_id": f"T{(i%20)+1:02d}", "condition": "longitudinal"}
           for i in range(40)]

updates = compute_backfill_values(records, run_summaries)
assigned = sum(tokens for _, tokens in updates)
assert assigned == 2000                                # Expected
# Actual: 4000  (40 rows × (2000//20) = 40 × 100)
```

**Bug B:**
```python
# LangFuse returns 2 traces for T01 in run r1, 100 tokens each → run_task_tokens[("r1","T01")] = 200
run_task_tokens = {("r1", "T01"): 200}
records = [
    {"id": "rec1", "run_id": "r1", "task_id": "T01", "condition": "longitudinal"},
    {"id": "rec2", "run_id": "r1", "task_id": "T01", "condition": "longitudinal"},
]
# Each record gets tokens = 200 → sum written = 400. Actual total = 200.
```

Confirmed by direct replay against the functions at the cited line numbers.

### Threat model / impact

- **M4 (token cost per task) is the thesis's primary cost-efficiency metric.** Chapter 6 aggregates per-condition and per-task costs; a 2× inflation on longitudinal / shift conditions makes JIT look ~2× more expensive than it is relative to the static baseline (which is unaffected — `static` branch at line 289 writes 0).
- **Relative comparisons are asymmetric.** Static, pilot, and one-shot eval conditions report correct costs. Only longitudinal + shift are inflated. Chapter 6 tables comparing conditions side-by-side silently favor the non-longitudinal conditions.
- **Direction of bias: anti-JIT-in-longitudinal.** The longer the longitudinal run, the worse the inflation (factor = n_cycles / n_distinct_tasks).
- **Both bugs land on the same direction.** Whichever code path fires (run-level mode or per-task-tagged mode), the answer is inflated. No configuration escapes it.

### Remediation

**Bug A — use total-record count as divisor, not distinct-task count.**

```python
# scripts/backfill_token_costs.py:123 — add total-row count alongside distinct count:
query = """
    SELECT run_id, condition, MIN(timestamp) as t_start, MAX(timestamp) as t_end,
           COUNT(*)                AS n_records,
           COUNT(DISTINCT task_id) AS n_distinct_tasks
    FROM evaluation_runs
    GROUP BY run_id, condition
"""

# line 137:
"n_records": r[4],
"n_distinct_tasks": r[5],

# line 259 (longitudinal path needs cycle count, not distinct count):
"n_tasks": rw["n_records"],  # every record gets 1/n_records share of the run total

# line 297:
per_task = summary["total_tokens"] // summary["n_tasks"]  # now divides by cycle count
```

**Bug B — redistribute per-task totals proportionally across cycles of the same task.**

```python
# scripts/backfill_token_costs.py:510-544 — count cycles per (run_id, task_id) first:
from collections import Counter
cycle_counts = Counter((r["run_id"], r["task_id"]) for r in records if r["condition"] != "static")

# ... then divide when assigning:
for rec in records:
    if rec["condition"] == "static":
        updates.append((rec["id"], 0))
        continue
    key = (rec["run_id"], rec["task_id"])
    total_for_task = run_task_tokens.get(key, 0)
    n_cycles_for_task = cycle_counts.get(key, 1)
    per_cycle = total_for_task // n_cycles_for_task
    updates.append((rec["id"], per_cycle))
```

For tasks that only ran once (regular eval), `n_cycles_for_task = 1` → no change. For longitudinal repeats, the per-task total is split evenly across its cycles.

**Alternative (more accurate): attribute LangFuse traces to specific cycles via the trace's timestamp.**

If each trace carries a `start_time` and each evaluation_record carries a timestamp, match trace → record via closest-timestamp rather than splitting evenly. More code, more accurate, but "evenly split per cycle" is already a big improvement over "multiply the whole total by the cycle count."

**Regression tests.**

```python
def test_backfill_longitudinal_run_mode_matches_actual_total():
    records = [...]
    summary = {"condition": "longitudinal", "total_tokens": 2000, "n_records": 40, ...}
    updates = compute_backfill_values(records, {"r1": summary})
    assigned = sum(t for _, t in updates)
    assert assigned == 2000  # pre-fix: 4000

def test_backfill_per_task_mode_splits_repeated_task_cycles():
    records = [{"id": "a", "run_id": "r1", "task_id": "T01", "condition": "longitudinal"},
               {"id": "b", "run_id": "r1", "task_id": "T01", "condition": "longitudinal"}]
    # Pre-seeded run_task_tokens[("r1","T01")] = 200 from two traces.
    # Expected: each cycle gets 100. Pre-fix: each gets 200, total written = 400.
    ...
```

### Recommendation

**Land before any Chapter 6 cost-per-task table is generated from longitudinal data.** Two bugs in one file; both fixes are ~10 LOC each + 2 regression tests. ~45 min. Re-run any prior longitudinal backfill post-fix (the script is idempotent — just re-invoke with `--write`).

### Legacy data

Any DuckDB row where `evaluation_records.token_cost` was backfilled from a longitudinal run is overstated by the cycle-repetition factor. Either (a) re-run the backfill script post-fix (preferred — the LangFuse-side source data is unchanged), or (b) apply a correction factor per-run in Chapter 6 analysis (fragile, not recommended).

---

## Issue #29 — Compositional lead-cache reuse path still records an invocation per subtask, producing phantom rows (P0)

### Summary

The compositional executor's "lead-with-subagents" optimization lets a single lead-AGENT artifact cover multiple subtasks: on the second+ subtask using the same artifact, the executor returns the cached output without re-running the agent. But invocation recording sits **below** the cache-hit/miss branch at `composition.py:910`, so every subtask increments the invocation metrics regardless of whether the artifact actually ran. One real execution produces N invocation rows.

### Where

- **Cache-hit short-circuit:** `src/orchestrator/composition.py:826-831` — `if ref.capability_name in _lead_cache: output = _lead_cache[ref.capability_name]`. No execution, no new LLM call, no new sandbox run.
- **Cache-miss execution:** `src/orchestrator/composition.py:832-843` — actually runs `_execute_step(...)` and populates `_lead_cache`.
- **Recording site (unconditional):** `src/orchestrator/composition.py:910` — `self._record_step_invocation(ref, output, step_ms, task_id or "", upstream_outputs=executed_outputs)`. Runs for every subtask, cache hit or miss.

### Reproduction

```python
# Minimal repro: lead AGENT artifact + 2 subtasks resolving to it.
agent_config = {"subagents": [...]}  # triggers lead-with-subagents cache path (line 841)
artifact = GeneratedArtifact(
    capability_name="multi_step_lead",
    capability_type=CapabilityType.AGENT,
    agent_config=agent_config,
    ...,
)
subtasks = [
    SubTask(required_capabilities=["multi_step_lead"], depends_on=[], ...),
    SubTask(required_capabilities=["multi_step_lead"], depends_on=[0], ...),
]

executor = CompositionalExecutor(
    subtasks=subtasks, artifacts=[artifact], ..., metrics_store=metrics_store,
)

execute_calls = []
orig = executor._execute_step
executor._execute_step = lambda *a, **kw: (execute_calls.append(1), orig(*a, **kw))[1]

executor.run(task_id="T_lead", ...)

assert len(execute_calls) == 1                                   # cache hit on subtask 2
assert metrics_store.count_invocations("multi_step_lead") == 1   # EXPECTED
# Actual: 2 — one phantom row for the cache-hit subtask.
```

Confirmed by code inspection: `_record_step_invocation` is outside the `if/else` branch that decides whether `_execute_step` ran.

### Threat model / impact

- **Inflates Gini coefficient.** Lead-with-subagents artifacts accumulate invocation counts faster than their true usage rate. A lead covering 5 subtasks registers 5 invocations per task that uses it — 4 of which are phantoms. Gini reads these counts (Step 18.5) and attributes lopsided concentration to what is actually a single-execution pattern.
- **Distorts Pillar 3 Split / Merge decisions.** The optimizer reads `MetricsStore.get_recent_failures()` and per-capability invocation totals (`src/optimizer/utility.py::compute_all_utilities`). Phantom rows bias the optimizer toward pruning or splitting leads that are actually being used efficiently.
- **Biases M4 (token cost per task).** Step 13.7 LangFuse scoring attaches per-invocation cost metadata; phantom invocations copy `step_ms` and `output` from the cache-hit branch (same `output` dict, same elapsed time) but get recorded as separate rows. The ratio "token cost per recorded invocation" is understated because the denominator is inflated.
- **Compounds with #23 (also P0 in ext6 — duplicate rows from repeated task_ids) and #27 (longitudinal invocation recording disabled).** #27 makes the problem invisible for longitudinal runs today; fixing #27 first means #29 becomes visible as soon as longitudinals start writing rows. Both must land in the same batch or longitudinal telemetry will swing from "empty" to "2-5× inflated."
- **Direction of bias: multiplier on lead usage.** The more subtasks a lead covers, the larger the inflation per task. A 5-subtask lead at 40 tasks = 200 recorded rows for 40 executions; the optimizer sees a capability that appears dominant in the invocation distribution.

### Remediation

**A. Move recording inside the cache-miss branch.**

```python
# composition.py:826 — BEFORE:
t_step = time.monotonic()
if ref.capability_name in _lead_cache:
    output = _lead_cache[ref.capability_name]
    logger.info("Step %d reusing cached lead output from %s", step_idx, ref.capability_name)
else:
    output = self._execute_step(step_idx, subtask, ref, artifact_or_match, workspace, upstream)
    if (
        ref.capability_type == CapabilityType.AGENT
        and isinstance(artifact_or_match, GeneratedArtifact)
        and artifact_or_match.agent_config
        and artifact_or_match.agent_config.get("subagents")
    ):
        _lead_cache[ref.capability_name] = output
step_ms = (time.monotonic() - t_step) * 1000
...
# line 910:
self._record_step_invocation(ref, output, step_ms, task_id or "", upstream_outputs=executed_outputs)

# AFTER:
t_step = time.monotonic()
cache_hit = ref.capability_name in _lead_cache
if cache_hit:
    output = _lead_cache[ref.capability_name]
    logger.info("Step %d reusing cached lead output from %s", step_idx, ref.capability_name)
else:
    output = self._execute_step(step_idx, subtask, ref, artifact_or_match, workspace, upstream)
    if (
        ref.capability_type == CapabilityType.AGENT
        and isinstance(artifact_or_match, GeneratedArtifact)
        and artifact_or_match.agent_config
        and artifact_or_match.agent_config.get("subagents")
    ):
        _lead_cache[ref.capability_name] = output
step_ms = (time.monotonic() - t_step) * 1000
...
# line 910 — skip recording on cache hit:
if not cache_hit:
    self._record_step_invocation(ref, output, step_ms, task_id or "", upstream_outputs=executed_outputs)
```

**B. Alternative: record once per artifact per chain, not per subtask.**

Move the recording to chain-completion rather than per-step, iterating over `_lead_cache` keys (one invocation per real execution) plus the non-lead steps (one per execution). More code but cleaner semantics — "invocations" table literally counts executions.

**C. Regression test.**

```python
def test_lead_cache_hit_does_not_record_phantom_invocation(metrics_store):
    """Lead AGENT covering 2 subtasks → exactly 1 invocation row, not 2."""
    # Seed a lead AGENT artifact, two subtasks requiring it, run chain.
    executor = CompositionalExecutor(subtasks=subtasks, artifacts=[lead_art],
                                     metrics_store=metrics_store, ...)
    result = executor.run(task_id="T_lead", ...)
    rows = metrics_store.fetch_invocations_for_task("T_lead")
    cap_rows = [r for r in rows if r["capability_id"] == "multi_step_lead"]
    assert len(cap_rows) == 1
```

### Recommendation

**Land alongside #27.** #27 gates whether longitudinal invocations get recorded at all; #29 gates whether the recording is accurate. Fixing #27 without #29 flips the longitudinal telemetry from "empty" to "inflated by the lead-reuse factor." Both together produce correct data. ~10 LOC + 1 regression test. ~30 min.

### Legacy data

Any DB with populated `invocations` for a run that used lead-with-subagents artifacts is inflated. For longitudinal runs the table is empty anyway (per #27), so no legacy data exists to correct. For eval-harness runs that did populate the table, a post-hoc de-dup pass on `(run_id, task_id, capability_id, occurred_at)` with sub-second tolerance would work, but re-running post-fix is simpler.

---

## Issue #30 — Per-task token-cost attribution silently disabled when the first non-static run lacks LangFuse tags (P1)

### Summary

`compute_per_task_costs` probes for per-task trace attribution by fetching traces for the **first** non-static run it iterates over (`backfill_token_costs.py:495-499`). If that single probe returns no `task_id`-tagged traces, the function returns `None` at line 506, and the caller falls back to run-level averaging (which is itself buggy per #28 Bug A). A DB containing a mix of pre-Step-13.7 runs (no task tags) and post-Step-13.7 runs (task tags) uses run-level mode for **all** runs if the iteration order happens to hit an untagged run first. The other runs' tagged traces are never examined.

### Where

- **First-run-only probe:** `scripts/backfill_token_costs.py:494-506`:
  ```python
  test_run = None
  for rw in run_windows:
      if rw["condition"] != "static":
          test_run = rw
          break
  if test_run is None:
      return None

  test_traces = fetch_traces_by_session(base_url, auth, test_run["run_id"])
  has_tags = any(t.get("task_id") for t in test_traces)
  if not has_tags:
      return None  # No tagged data — fall back to run-level mode
  ```
- **Call site:** line ~470 (main flow chooses between `compute_per_task_costs` and `compute_backfill_values` based on the former's return).
- **Fallback path (buggy per #28):** `compute_backfill_values` — divides by `n_distinct_tasks`, inflates longitudinal totals 2×.

### Reproduction

```python
run_windows = [
    {"run_id": "runA_old",  "condition": "full", ...},   # Pre-Step-13.7 — no task_id tags
    {"run_id": "runB_new",  "condition": "full", ...},   # Post-Step-13.7 — has task_id tags
]

# Mock fetch_traces_by_session to return untagged traces for runA, tagged for runB
def fake_fetch(base_url, auth, run_id):
    if run_id == "runA_old":
        return [{"id": "t1"}]  # No task_id
    else:
        return [{"id": "t2", "task_id": "T01"}]

result = compute_per_task_costs(records, run_windows, ...)
assert result is None  # Confirmed: probe hit runA_old first, saw no tags, returned None.
# Expected: per-task attribution for runB_new, fallback for runA_old only.
```

Confirmed by direct trace of the code path.

### Why the probe was written as a first-run-only decision

The intent in the comment at line 494 is "is there any tagged data at all in this DB?" — a cheap check to avoid fetching every run's traces when the answer is obviously "no tags anywhere." But the check conflates "no tags in run A" with "no tags anywhere." The cheapness concern is real (LangFuse REST fetches are per-run and can be slow) but the sampling strategy is wrong: probe **the most recent** non-static run (most likely to have tags if any run does), or probe **all** runs and decide per-run.

### Threat model / impact

- **Chapter 6 per-task cost tables use the wrong attribution mode for mixed-era DBs.** Any DB that contains pre-Step-13.7 AND post-Step-13.7 runs falls into run-level mode for all of them — meaning the newer runs' precise per-task data is discarded and replaced with the buggy #28-A inflated divisor.
- **Compounds with #28.** #28 Bug A inflates run-level mode 2× for longitudinal runs. #30 forces runs into run-level mode even when per-task attribution is available. Together: any longitudinal/shift run in a mixed-era DB gets 2× inflation even though its traces carried enough data to avoid the inflation entirely.
- **Direction of bias: anti-JIT-in-longitudinal.** Same direction as #28; stacks on top.
- **Easy to trigger in practice.** Any iteration-order nondeterminism (`run_windows` isn't sorted descending by timestamp) plus a mixed-era DB causes this. A single old run in the DB poisons attribution for every newer run.

### Remediation

**A. Per-run tag detection — probe each run, decide per-run.**

```python
def compute_per_task_costs(
    records: list[dict],
    run_windows: list[dict],
    base_url: str,
    auth: tuple[str, str],
) -> list[tuple[str, int]] | None:
    """Try per-task attribution via tagged LangFuse traces.

    Decides per-run rather than per-DB: runs with tagged traces use
    per-task attribution; runs without fall back to the caller's
    run-level path. Returns updates for tagged runs only; caller
    unions with run-level updates for untagged runs.
    """
    updates: list[tuple[str, int]] = []
    tagged_run_ids: set[str] = set()

    for rw in run_windows:
        if rw["condition"] == "static":
            continue
        traces = fetch_traces_by_session(base_url, auth, rw["run_id"])
        if not any(t.get("task_id") for t in traces):
            continue  # Untagged run — caller handles via run-level mode
        tagged_run_ids.add(rw["run_id"])

        # Group + attribute for this run only (same logic as before, one run at a time)
        task_traces: dict[str, list[dict]] = defaultdict(list)
        for t in traces:
            tid = t.get("task_id")
            if tid:
                task_traces[tid].append(t)
        ...

    if not tagged_run_ids:
        return None  # No tagged runs anywhere — all fall back to run-level

    # Caller merges per-task updates (for tagged runs) with run-level updates (untagged runs).
    return updates, tagged_run_ids  # Return tuple so caller can route per-run.
```

Signature change propagates to `main()` — the caller now needs to union tagged-run updates with run-level updates for untagged runs.

**B. Cheaper alternative: probe the newest non-static run instead of iteration order.**

```python
# Sort by t_end descending, probe the first (newest):
sorted_runs = sorted(
    [rw for rw in run_windows if rw["condition"] != "static"],
    key=lambda r: r["t_end"], reverse=True,
)
test_run = sorted_runs[0] if sorted_runs else None
```

If the newest run has tags and older runs don't, per-task mode fires for all of them (older runs just return no data per-task, which is the correct behavior for untagged older runs — they stay in run-level mode via a secondary branch).

Option (A) is the proposal-correct fix; (B) is a 5-line mitigation if (A) is too invasive.

**C. Regression test.**

```python
def test_per_task_attribution_detects_tags_in_non_first_run(monkeypatch):
    """Mixed-era DB: older run without tags + newer run with tags → per-task mode fires for newer."""
    run_windows = [
        {"run_id": "runA_old", "condition": "full", "t_end": "2026-03-01"},
        {"run_id": "runB_new", "condition": "full", "t_end": "2026-04-20"},
    ]
    def fake_fetch(base_url, auth, run_id):
        return [{"id": "t1"}] if run_id == "runA_old" else [{"id": "t2", "task_id": "T01"}]
    monkeypatch.setattr("scripts.backfill_token_costs.fetch_traces_by_session", fake_fetch)
    result = compute_per_task_costs(records, run_windows, ..., ...)
    assert result is not None  # pre-fix: None
```

### Recommendation

**Land with #28.** Both are backfill-script correctness fixes in the same file; #28 fixes the inflated math, #30 fixes the path-selection. Fixing only one leaves the other on the table. ~40 LOC + 1 regression test for the per-run decision + signature update at the caller. ~45 min combined with #28.

### Legacy data

Re-run `scripts/backfill_token_costs.py --write` against the existing DB after both #28 and #30 land. The LangFuse-side traces are unchanged; only the DuckDB `token_cost` columns need overwriting.

---

## Issue #31 — Longitudinal baseline is snapshotted from a dirty registry, contaminating every subsequent run (P0)

### Summary

The two longitudinal runners set up their "clean" baseline by calling `take_clean_snapshot(CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR)` — which **copies the current state of those directories** into the snapshot location — immediately followed by `restore_clean_snapshot(...)` and `seed_registry(registry)`. There is no cleanup step before the snapshot is taken. Any capability evolved by a prior run (split variant, merged capability, pruned-but-still-on-disk skill folder) is captured as part of the "clean" baseline. Every subsequent longitudinal run starts with that contamination plus fresh seeds, invalidating the "start from N seeded capabilities" premise the convergence and effect analyses depend on.

### Where

- `scripts/run_longitudinal.py:121` — `snap_id = take_clean_snapshot(CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR)` (no prior cleanup).
- `scripts/run_longitudinal.py:122` — `restore_clean_snapshot(...)` (restores the dirty snapshot).
- `scripts/run_longitudinal.py:130` — `seed_registry(registry)` (adds seeds on top of dirty baseline).
- `scripts/run_longitudinal_shift.py:240/241/249` — same pattern verbatim.
- **Snapshot semantics:** `tests/evaluation/snapshots.py:24-56` — `take_clean_snapshot` documents itself as "Copy the three registry directories into a clean snapshot," but the word "clean" describes the call-once intent, not the source state. There is no invariant check that the source is actually clean.

### Reproduction

Captured directly against a `data/eval/` directory that had been dirtied by a prior run:

```
seeded_count                      = 8       # seeded capabilities per seed_registry()
dirty_count_before_snapshot       = 9       # 8 seeds + 1 leftover variant from a previous run
post_restore_plus_seed_count      = 9       # restore brings back all 9; seeding is idempotent on existing
```

The `dirty_count_before_snapshot` > `seeded_count` by the number of evolved capabilities the previous run left behind. `seed_registry`'s idempotence hides the contamination: post-restore-plus-seed counts remain stable at the dirty number, so there's no loud signal.

### Threat model / impact

- **Pillar 3 RQ #1 (seeded-vs-evolved distribution) compromised.** The primary analysis comparing "seeded distribution" vs "evolved distribution" requires the seeded baseline to be exactly the seeded set. If the baseline starts with N+k capabilities where k are leftovers from prior runs, the k-th capability is classified as "seeded" in the analyzer's logic (because it was in the registry at cycle 0), inflating the "seeded and still active" count.
- **Pillar 3 RQ #2 (convergence) corrupted.** KL convergence measures distribution change from baseline. A dirty baseline is already halfway to some other distribution; convergence appears faster than it actually is.
- **Pillar 3 RQ #3 (distribution-shift adaptation, Step 18.8) corrupted.** Phase A vs Phase B re-convergence depends on Phase A reaching a stable distribution; Phase A's "stable" distribution inherits the prior run's evolution.
- **Cross-run causal confounding.** The specific leftovers depend on what the prior run did, which depends on that run's random seed, which depends on `base_seed` AND process hash-randomization (per finding #34). Two ostensibly-identical longitudinal runs are not interchangeable.
- **Severity P0.** The Chapter 6 longitudinal conclusions rest on the assumption that every run starts from the same seeded baseline. They don't.

### Remediation

**A. Clean the three directories before snapshotting.**

```python
# run_longitudinal.py:121 — before take_clean_snapshot:
for d in [CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR]:
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True, exist_ok=True)

# Now take_clean_snapshot captures an actually-empty state.
snap_id = take_clean_snapshot(CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR)

# Seed on top of truly clean state.
registry = SkillRegistry(persist_directory=str(CHROMA_DIR), collection_name="capabilities")
seed_registry(registry)

# Re-take the snapshot post-seeding — THIS is the "clean seeded baseline" that
# future comparisons must restore to, if the runner ever adds mid-run restore.
snap_id = take_clean_snapshot(CHROMA_DIR, MCP_TOOLS_DIR, SKILLS_DIR)
```

Apply the same change at `run_longitudinal_shift.py:240`.

**B. Add an invariant check to `take_clean_snapshot`.**

The function should assert the source directories contain either no capabilities OR exactly the seeded set (configurable). Loud-fails when called against a dirty registry:

```python
def take_clean_snapshot(chroma_dir, mcp_tools_dir, skills_dir, *, expected_cap_count=None, ...):
    if expected_cap_count is not None:
        # Count current capabilities via the registry
        actual = _count_capabilities(chroma_dir)
        if actual != expected_cap_count:
            raise RuntimeError(
                f"take_clean_snapshot called against dirty registry "
                f"(expected {expected_cap_count} capabilities, found {actual}). "
                f"Clean the directories first or pass expected_cap_count=None to override."
            )
```

Callers that know the expected count (runners post-clean post-seed) pass it; callers that explicitly want to snapshot current state (test fixtures) pass `None`.

**C. Regression test against the two runners.**

```python
def test_run_longitudinal_starts_from_clean_baseline(tmp_path, monkeypatch):
    """Dirty the registry with a fake leftover, run the longitudinal setup path,
    assert the baseline matches seeded-only count."""
    _seed_fake_leftover_capability(tmp_path)
    # Invoke the runner's baseline setup (extracted into a helper)
    _longitudinal_baseline_setup(tmp_path)
    assert _count_capabilities(tmp_path) == SEEDED_COUNT
```

### Recommendation

**Land before any Phase 5 longitudinal run.** Five-line fix at each runner + one invariant assertion + one regression test. ~30 min. Must ship before Chapter 6 re-collects longitudinal data (the Chapter's RQ #1/#2/#3 all depend on this).

### Legacy data

Any DuckDB from a prior longitudinal run has dirty-baseline-contaminated metrics. Re-run required post-fix — the source data cannot be cleaned retroactively.

---

## Issue #32 — Success-rate predicate ignores `output_shape_mismatch`; contract-violating outputs count as successes (P0)

### Summary

The success metric analyzed in Chapter 6 is `gate_passed AND output_in_range`. `output_shape_mismatch` — the boolean flag set by Step 14.6's `OutputContract.check()` when required keys or files are missing — is recorded on the row (`harness.py:892`) but **not** part of the success predicate (`analyze_results.py:128`). A task where the validator said "looks OK" but the output is missing its declared `required_files` (e.g., T07's `output.csv`) is counted as a success.

### Where

- **Recording site:** `tests/evaluation/harness.py:859-863` — `shape_check = task.output_contract.check(final_output, chain_result.output_files)`.
- **Record fields:** `harness.py:892-900` — `output_shape_mismatch`, `output_shape_missing_keys`, `output_shape_missing_files` serialised to the EvaluationRecord.
- **Success predicate (missing the shape check):** `scripts/analyze_results.py:128` — `SUM(CASE WHEN gate_passed AND output_in_range THEN 1 ELSE 0 END) AS successes`.
- **Intent doc:** the Step 14.6 design note in CLAUDE.md claims `OutputContract.check()` is "orthogonal to the validator" — which is true of the shape of the data but should not be true of the success metric. The current code treats it as truly orthogonal, including in the success rollup.

### Reproduction

Observed on T07 (CSV output task):

```
final_output           = {"summary": "Regional revenue computed."}
chain_result.output_files = {}                              # no files written
task.validator(final_output) → True                         # dict-only validator happy
task.file_validator(...) not used because output_files is empty (harness:835)
task.output_contract.check(...) → ContractCheckResult(satisfied=False,
                                  missing_files=["output.csv"])

Recorded:
  gate_passed               = True
  output_in_range           = True
  output_shape_mismatch     = True
  output_shape_missing_files = ["output.csv"]

Analyzer reads: gate_passed AND output_in_range → True → counted as success.
```

Confirmed by direct inspection of the T07 code path + `analyze_results.py::query_condition_summary`.

### Why the validator doesn't catch this

Two separate validation contracts exist:
1. `task.validator(output: dict) → bool` — checks the dict's metadata (e.g., "regions_meeting_target" matches expected).
2. `task.file_validator(output, output_files) → bool` — checks file contents if files exist.
3. `task.output_contract.check(output, output_files) → ContractCheckResult` — the Step 14.6 typed contract that declares `required_keys` and `required_files`.

`file_validator` is consulted only when `output_files` is truthy (harness.py:835). A chain that *forgets to produce required files at all* falls through to the dict-only `validator`, which has no visibility into files. The contract check does catch it — but its result is not part of the success metric.

### Threat model / impact

- **Pillar 1 + Pillar 2 success rates in Chapter 6 are inflated.** Any task requiring file output (T07: `output.csv`, T11: `output.xlsx`, T16: `report.md`, plus any synthesis task with `required_files` non-empty) can score as success when the pipeline never produced the file. The validator's happy-dict check is insufficient.
- **Direction of bias: pro-JIT.** The JIT pipeline produces structured dicts readily; it fails on file emission more often (per the §12 finding #10 history — Option-A-as-fallback pattern). Static condition (which uses hand-written tools) is less likely to hit this because hand-written writers are deterministic.
- **M1 (success rate) corrupted for all file-output tasks.** Chapter 6's headline success numbers for T07/T11/T16 over-report pipeline effectiveness in exactly the area §12 findings #10-#13 were designed to make rigorous.
- **The contract data is already in the row.** No data collection change needed — the analyzer just has to include `output_shape_mismatch = FALSE` in its success predicate.

### Remediation

**A. Tighten the success predicate.**

```sql
-- scripts/analyze_results.py:128 — before:
SUM(CASE WHEN gate_passed AND output_in_range THEN 1 ELSE 0 END) AS successes,

-- after:
SUM(CASE WHEN gate_passed
          AND output_in_range
          AND NOT COALESCE(output_shape_mismatch, FALSE) THEN 1 ELSE 0 END) AS successes,
```

`COALESCE` handles legacy rows written before the column existed. Apply the same tightening everywhere the analyzer aggregates "successes" — `query_task_summary`, `print_classification_accuracy`, etc.

**B. Align the harness `failure_reason` value.**

`harness.py:839` sets `failure_reason = "none"` when `output_in_range` is true, even if the contract was violated. Update:

```python
if output_in_range and shape_check is not None and not shape_check.satisfied:
    failure_reason = "output_shape_mismatch"
elif output_in_range:
    failure_reason = "none"
else:
    failure_reason = "output_rejected"
```

So the per-record attribution surfaces the shape-mismatch case explicitly, matching the CLI's existing exit-code-3 semantics.

**C. Regression test.**

```python
def test_success_predicate_excludes_shape_mismatched_rows(tmp_path):
    """Row with gate_passed=True, output_in_range=True, output_shape_mismatch=True
    must not count as a success."""
    db = seed_duckdb_with_mixed_rows(tmp_path, [
        {"gate_passed": True, "output_in_range": True, "output_shape_mismatch": False},  # ← counts
        {"gate_passed": True, "output_in_range": True, "output_shape_mismatch": True},   # ← must NOT count
    ])
    summary = query_condition_summary(db)
    assert summary[0]["successes"] == 1  # pre-fix: 2
```

### Recommendation

**Land before any Chapter 6 success-rate table is generated.** Two-line SQL change + one harness touch + one test. ~15 min. Re-run any prior analyzer output; source data is preserved (the column is already populated).

### Legacy data

All existing EvaluationRecord rows already carry `output_shape_mismatch`. Re-running `analyze_results.py` post-fix produces corrected numbers without re-collecting data.

---

## Issue #33 — T07 + T11 file validators fail-open on missing/corrupt files (P1)

**Scope expanded 2026-04-20:** originally T11-only; T07 shares the same pattern at `tasks.py:363-373` and is now covered in the same section.

### Summary

Both file validators have fail-open paths that mark bad outputs as valid:

**T07 `_file_validate_t07` (`tasks.py:363`) — missing `output.csv` falls back to dict-only:**
```python
csv_bytes = output_files.get("output.csv")
if csv_bytes is None:
    return ok_count  # fall back to dict-only validation
```
A pipeline that produced the correct `row_count` metadata but forgot to write the CSV passes.

**T11 `_file_validate_t11` (`tasks.py:590`) — two fail-open paths:**
1. **Missing file:** `tasks.py:597-599` — `if xlsx_bytes is None: return True  # dict passed, no file — still valid`.
2. **Parse exception:** `tasks.py:610-611` — `except Exception: return True  # fall back to dict-only if openpyxl not available`. Catches `ImportError` (the stated reason), but also `openpyxl.utils.exceptions.InvalidFileException`, `zipfile.BadZipFile`, and every other failure mode of `openpyxl.load_workbook`.

### Where

- **T07:**
  - `tests/evaluation/tasks.py:363` — `def _file_validate_t07(output: dict, output_files: dict[str, bytes]) -> bool`.
  - `tests/evaluation/tasks.py:371-373` — `if csv_bytes is None: return ok_count  # fall back to dict-only`.
- **T11:**
  - `tests/evaluation/tasks.py:590` — `def _file_validate_t11(output: dict, output_files: dict[str, bytes]) -> bool`.
  - `tests/evaluation/tasks.py:597-599` — missing-file fail-open.
  - `tests/evaluation/tasks.py:610-611` — exception-catch fail-open.
- **T16 (confirmed-same-pattern by inspection; not reproduced yet):** `tests/evaluation/tasks.py::_file_validate_t16` — audit + fix same shape if present.

### Reproduction

**Missing-file path:**
```python
result = _file_validate_t11({"total_revenue": 12345.67, "regions_meeting_target": 2,
                             "regional_breakdown": [...]},
                            output_files={})  # no output.xlsx
assert result is True  # Treated as valid despite missing required file
```

**Parse-exception path:**
```python
result = _file_validate_t11({"total_revenue": 12345.67, "regions_meeting_target": 2,
                             "regional_breakdown": [...]},
                            output_files={"output.xlsx": b"not a real xlsx blob"})
# openpyxl.load_workbook raises InvalidFileException on line 602 → caught at 610 → returns True
assert result is True  # Treated as valid despite corrupt file bytes
```

Both confirmed by direct Python execution.

### Why the original intent doesn't justify the current scope

The comment at line 611 says "fall back to dict-only if openpyxl not available" — suggesting the intent was to gracefully degrade when the dev environment lacks `openpyxl`. But:

- The project `pyproject.toml` has `openpyxl` as a hard dependency of the eval harness; it is always installable.
- The `except` clause catches every exception type, not just `ImportError`.
- The missing-file branch at line 597-599 is a separate fail-open that the comment doesn't describe at all.

The safe narrowing is `except ImportError:` for the degradation path, and a loud failure on every other exception.

### Threat model / impact

- **Compounds with #32.** #32 removes `output_shape_mismatch` from the success predicate; but if the contract's required_files is non-empty and T11's file_validator accepts missing files as valid, the contract check is the only remaining guard. After #32 lands, T11's file_validator can still return True for absent files — contradicting the "required_files must be present" contract and letting the row pass both `output_in_range` and `output_shape_mismatch` even though it shouldn't.
- **Direction of bias: pro-artifact on T11 (Excel output).** T11 is the only testbed task with an `.xlsx` requirement; the bias is concentrated on one task but the task is one of Chapter 6's headline complex-tier examples.
- **Silent-degradation pattern (cross-cutting observation #5 in the parent audit).** Same anti-pattern called out for #6/#7/#10/#11-B/#12-cycle — narrow guard demotes caller's intent without surfacing the demotion. The "dict passed" branch converts "we couldn't produce the required file" into "dict-only validation is enough" with no loud signal.

### Remediation

**A. Fail-closed on missing file.**

```python
def _file_validate_t11(output, output_files):
    try:
        if not _validate_t11(output):
            return False
        xlsx_bytes = output_files.get("output.xlsx")
        if xlsx_bytes is None:
            # File is REQUIRED by T11's output_contract.required_files;
            # dict validation alone is insufficient.
            return False
        import io, openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True)
        if len(wb.sheetnames) == 0:
            return False
        ws = wb.active
        row_count = sum(1 for _ in ws.iter_rows(min_row=1))
        return row_count >= 5
    except ImportError:
        # Graceful degradation only for the environment-lacks-openpyxl case.
        import logging
        logging.getLogger(__name__).warning("openpyxl unavailable; dict-only validation for T11.")
        return _validate_t11(output)
    # No catch-all Exception handler: let every other exception surface as a fail in the caller.
```

**B. Propagate to T07 and T16 validators.**

The same pattern almost certainly exists for T07 (`output.csv`) and T16 (`report.md`) file validators. Grep for `except Exception` in `tests/evaluation/tasks.py` and narrow each to `except ImportError` where applicable. Flag any other task-level fail-open branch for the same fix.

**C. Regression tests.**

```python
def test_file_validate_t11_fails_on_missing_file():
    assert _file_validate_t11({"total_revenue": 12345.67, ...}, output_files={}) is False

@pytest.mark.parametrize("bad_bytes", [b"", b"not-an-xlsx", b"\x00\x00\x00"])
def test_file_validate_t11_fails_on_corrupt_xlsx(bad_bytes):
    assert _file_validate_t11({"total_revenue": 12345.67, ...},
                              output_files={"output.xlsx": bad_bytes}) is False
```

### Recommendation

**Bundle with #32.** Both are success-metric integrity fixes; #32 tightens the aggregate, #33 tightens the per-task T11 validator. ~15 LOC + 3 regression tests across T07/T11/T16. ~30 min combined with #32.

---

## Issue #34 — Run seed derivation uses Python `hash()` on strings; task order is non-reproducible across processes (P1)

### Summary

`EvaluationHarness` derives per-condition and per-run seeds via `hash((condition, self._config.base_seed)) % (2**31)`. Python randomizes `str.__hash__` per process unless `PYTHONHASHSEED` is explicitly set, so `hash(("full", 42))` returns different values in different Python process invocations. The harness's documented reproducibility contract ("same `base_seed` → same task ordering") silently fails for every caller that doesn't set `PYTHONHASHSEED=0` (or a fixed value) in the environment.

### Where

- `tests/evaluation/harness.py:244` — `condition_seed = hash((condition, self._config.base_seed)) % (2**31)`.
- `harness.py:245-246` — `rng = random.Random(condition_seed)`; `condition_seeds = [rng.randint(0, 2**31) for _ in range(self._config.n_runs)]`.
- Downstream: `_execute_run(seed=...)` — the returned seeds drive task-order shuffling and any other randomized behavior.

### Reproduction

Two fresh Python processes, same `base_seed=42`, same `condition="full"`:

```
$ python3 -c 'print(hash(("full", 42)) % (2**31))'
1613808903

$ python3 -c 'print(hash(("full", 42)) % (2**31))'
1906611644
```

Different seeds → different task orderings → different registry-reuse patterns (since earlier tasks populate the registry that later tasks query) → different Chapter 6 results. User's repro matches verbatim.

### Threat model / impact

- **"Reproducibility from `base_seed=N`" contract is broken.** CLAUDE.md and the run_sheet both frame `base_seed=42` as the canonical seed for repeatable experiments. The current code doesn't honor that.
- **Chapter 6 replicability.** A reviewer attempting to re-run the eval with the published `base_seed` and config will see different numbers. The defense "fixed a bug" vs "can't reproduce the original" is a rhetorically weak position for a thesis.
- **Cross-condition fairness.** The per-condition seeds are independent per-condition (intentional), but the same condition across two processes gets different seeds — meaning "static vs full" comparisons within one process are coherent, but "my run of full" vs "your run of full" are not.
- **Masks by #31 and #27.** While #27 keeps longitudinal invocation tables empty and #31 contaminates baselines, the non-reproducibility is invisible. Once #27/#31 land and comparisons become meaningful, #34's nondeterminism becomes visible.

### Remediation

**A. Switch to a deterministic hash.**

```python
# harness.py:244 — before:
condition_seed = hash((condition, self._config.base_seed)) % (2**31)
# after:
import hashlib
_seed_key = f"{condition}|{self._config.base_seed}".encode("utf-8")
condition_seed = int.from_bytes(
    hashlib.sha256(_seed_key).digest()[:4], "big"
) % (2**31)
```

SHA256 is overkill for a seed derivation but it's the simplest deterministic option. `zlib.crc32` is a faster alternative (`int(zlib.crc32(_seed_key))` is deterministic across processes and platforms) and documents the non-cryptographic intent. Either is fine; SHA256 is easier to defend in a thesis.

**B. Audit other `hash(...)` call sites for the same bug.**

Grep for `hash((` and `hash(` in `src/` + `tests/evaluation/` + `scripts/`. Any call with a string argument is a latent non-reproducibility. Most legitimate uses should migrate to either `hashlib.sha256` (for correctness) or `dict`/`set`-style hashing (for containers, which is fine because containers don't leak their hash semantics across processes).

**C. Regression test.**

```python
def test_condition_seed_is_deterministic_across_processes():
    """Seed derivation must match a pre-recorded expected value for the canonical base_seed."""
    config = EvaluationConfig(base_seed=42, n_runs=1, ...)
    # Call the (extracted) seed derivation helper
    seed = _derive_condition_seed("full", config.base_seed)
    assert seed == EXPECTED_DETERMINISTIC_VALUE_FOR_FULL_42
    # Stable across Python versions and PYTHONHASHSEED settings.
```

**D. Documentation update.**

CLAUDE.md and `run_sheet.md` should state explicitly: "Seed derivation is deterministic; `PYTHONHASHSEED` does not need to be set for reproducibility." Currently neither document claims reproducibility is unconditional; both assume it is.

### Recommendation

**Land before Phase 5 eval.** Six-line fix + one regression test + doc touch. ~20 min. Any prior data collected is not bit-for-bit reproducible, but the per-condition statistical conclusions are still valid (the nondeterminism is uniform across conditions within a single process). No legacy data correction required; reruns post-fix simply become reproducible going forward.

---

## Issue #35 — Task validators across nine testbed tasks check metadata dimensions only; hallucinated/minimal outputs pass (P0)

### Summary

Nine of the twenty testbed task validators check summary statistics (counts, dimensions, trend direction, exact-match root-cause strings) without cross-checking that the claimed work was actually performed on the fixture data. An LLM that learns the expected values from the task prompt — or hallucinates plausible values — produces a dict that satisfies every predicate without ever opening the input fixture or running the declared logic. The validators answer "does the output dict have the right shape?" when Chapter 6 needs "did the pipeline do the work?". The class has been latent because normal pipelines *do* the work; it becomes a thesis-integrity problem for any baseline where the LLM short-circuits (static condition with hand-written tools that fabricate, registry-match without execution, seeded-descriptor fallback, etc.).

### Where

All in `tests/evaluation/tasks.py`:

| Line | Task | Permissiveness |
|---|---|---|
| 141 | T02 (JSON config validation) | `len(errors) == expected_error_count` — count-only; any N fabricated error strings pass |
| 264 | T05 (data cleaning) | `output_row_count < input_row_count AND null_count == 0` — minimal `{"output_row_count": 1, "null_count": 0}` passes |
| 419 | T08 (data quality audit) | `quality_issues_found > 0 AND n_rows == expected AND n_columns == expected` — `> 0` is trivial; dimensions fabricatable |
| 476 | T09 (report generation) | `len(anomalies) == expected_anomaly_count` — count-only; any N fabricated anomaly descriptions pass |
| 515 | T10 (trend forecasting) | `trend_direction in {upward/up/…} AND n_weeks == expected AND forecast is list` — `forecast=[0,0,0]` passes |
| 660 | T12 (data quality policy) | `violations_found > 0` — trivial threshold alongside exact counts |
| 710 | T13 (anomaly detection) | `anomaly_count >= 3 AND anomalies non-empty AND metrics_analyzed > 0` — all trivial thresholds; no content check |
| 838 | T15 (weekly aggregation) | Three exact counts — fabricatable from task description alone |
| 897 | T16 (root-cause analysis) | `root_cause_metric == expected` (exact string — hardest to fake) AND `len(cascade_chain) >= 2 AND n_metrics_investigated > 0` — chain content not checked |

The remaining validators are materially tighter:
- **T01/T03:** retrieval-only, no pipeline work to validate.
- **T04/T06:** not reviewed in this pass; likely same pattern — include in the audit sweep.
- **T07 / T11:** file-output validators covered under #33 (separate class: fail-open on missing/corrupt file, not permissive dict predicate).
- **T14 / T17 / T18 / T19 / T20:** not reviewed in this pass.

### Reproduction

**T05 minimal hallucinated output passes:**
```python
from tests.evaluation.tasks import _validate_t05
# Input fixture has ~1000 rows; valid cleaning should produce 700-ish rows.
assert _validate_t05({"output_row_count": 1, "null_count": 0}) is True
# Pre-fix: True. The LLM didn't need to see the fixture.
```

**T10 empty-forecast passes:**
```python
from tests.evaluation.tasks import _validate_t10
assert _validate_t10({
    "trend_direction": "upward",
    "n_weeks": 12,      # learned from task description
    "forecast": [0, 0, 0],   # empty zeros, never computed from data
}) is True
```

**T09 fabricated anomalies pass:**
```python
from tests.evaluation.tasks import _validate_t09
# Expected count from fixture is 5; fabricate 5 empty strings.
assert _validate_t09({
    **{k: "fabricated" for k in _t09_ev["required_keys"]},
    "anomalies": ["", "", "", "", ""],
}) is True
```

All three confirmed by direct Python execution.

### Why this is structurally different from #32/#33

- **#32** is about the ANALYZER'S success predicate — the data is there, the rollup formula is wrong.
- **#33** is about the file-output validators specifically — a file is REQUIRED and the validator skips checking when the file is absent.
- **#35** is about the METADATA validators — they check surface-level shape but not correctness against the fixture.

All three compound: a task can pass gate (Pillar 2), pass the metadata validator (this issue), satisfy the output_shape contract trivially (if `required_keys` happens to be the same keys the metadata check already enforces), and be rolled up as "success" by the analyzer (#32 makes this worse, but even without #32, the row reads as `output_in_range=True`).

### Threat model / impact

- **M1 (success rate) inflated on the bulk of the testbed.** 9/20 tasks have permissive validators; the 9 represent the "synthesis" tier, which is the thesis's focus area. M1 for synthesis tasks is the headline claim.
- **Direction of bias: pro-JIT + pro-static + pro-registry-match — any path that produces plausible-looking metadata without doing the work.** Since every baseline can in principle fabricate, the bias is uniform across conditions — meaning the ABSOLUTE numbers are all inflated, but the RELATIVE comparisons across conditions may be less affected. Whether the bias is uniform depends on how often each condition actually fabricates vs. does the work; this is observable post-fix but unmeasurable pre-fix.
- **Thesis defense vulnerability.** A reviewer questioning "how do you know the pipeline actually did the work?" has no answer under the current validators. "The dict had the right count" is not equivalent to "the work was performed." Chapter 6 claims pipeline effectiveness; the validators can't distinguish an effective pipeline from a confident hallucination.
- **M2 (contamination detection) correlates with this.** The §Pillar 2 contamination audit (F1 hardcoded outputs) is the counterpart check at the artifact level; the testbed-validator-level equivalent is missing. The two should mirror each other.

### Remediation

**A. Cross-validate claimed metrics against fixture ground truth.**

For every numeric claim, load the fixture and compute the ground-truth independently. Example for T05:
```python
def _validate_t05(output: dict) -> bool:
    try:
        out_count = int(output.get("output_row_count", -1))
        null_count = int(output.get("null_count", -1))
        if null_count != 0:
            return False
        # NEW: cross-validate against the fixture.
        # The expected output_row_count is a specific value, not just "less than input".
        expected = _t05_ev.get("expected_output_row_count")
        if expected is not None and out_count != expected:
            return False
        return True
    except (TypeError, ValueError):
        return False
```

This requires the evaluation ground-truth schema (`_EV` dict) to grow per-task expected values beyond just counts — for T05, the exact expected row count; for T10, the exact forecast range or a tolerance; for T09, the specific anomaly IDs expected (sorted, compared).

**B. Where cross-validation is infeasible, require content signatures.**

For T09 anomalies: require the anomalies list to contain specific substring markers from the fixture (e.g., "2026-W03 revenue dipped ..."). An LLM that hallucinated wouldn't produce those specific markers.

For T13: require `anomalies[i]["metric"]` values to be a subset of the actual metric names in the fixture.

**C. Add a per-task `content_validator` alongside `validator` and `file_validator`.**

```python
@dataclass
class TaskDefinition:
    ...
    validator: Callable[[dict], bool]
    file_validator: Optional[Callable[...]]
    content_validator: Optional[Callable[[dict, dict], bool]] = None  # dict + fixtures
```

Use it in `harness.py:838` alongside the existing validator; `output_in_range` is True only when both pass.

**D. Add a fail-closed default for missing content_validator.**

Tasks without a `content_validator` WARN loudly in the harness log (one-time per task per run): "Task T05 has no content_validator; success rate is permissive." Forces a decision on every task.

### Regression tests

```python
@pytest.mark.parametrize("output", [
    {"output_row_count": 1, "null_count": 0},
    {"output_row_count": 10, "null_count": 0},
])
def test_t05_rejects_hallucinated_minimal_output(output):
    assert _validate_t05(output) is False  # pre-fix: True

def test_t09_rejects_fabricated_anomaly_strings():
    output = {**{k: "fake" for k in _t09_ev["required_keys"]},
              "anomalies": ["fake"] * _t09_ev["expected_anomaly_count"]}
    assert _validate_t09(output) is False  # pre-fix: True
```

### Recommendation

**Land before any Chapter 6 M1 table is generated.** This is the biggest methodological weakness in the eval harness — it's structural rather than code-bug, which means reviewers will target it. Estimated effort: ~1-1.5 days (a full audit + content_validator design + per-task content-validator implementation for the 9 tasks + regression tests). Largest single effort in the ext6 set.

Alternative pragmatic approach: **content_validator for the 3-4 highest-risk tasks** (T05, T09, T10, T13) + a loud WARN for the rest + a Chapter 4.5 disclosure that the remaining validators are metadata-only and the uniform-inflation argument is the defense. ~4-6 hours.

### Legacy data

Every pre-fix M1 number across every condition is inflated. Re-run required post-fix, or Chapter 4.5 disclosure pairs with a pre-fix/post-fix comparison table as evidence of the shift. The latter is actually valuable methodology — show the number with the weak validator, show it with the strict validator, report the gap as a measure of "baseline hallucination rate." This converts the bug into a contribution.

---

## Issue #36 — `backfill_token_costs.py` fails open with exit 0 when LangFuse has no generations in the eval window (P0)

### Summary

When `fetch_all_generations` returns an empty list — LangFuse purged, trace window misaligned, network down, project mis-configured — the script prints a WARNING and returns normally at `backfill_token_costs.py:586-590`. `evaluation_records.token_cost` stays at 0 for every row. The caller's exit code is 0. Any downstream script, dashboard, or Chapter 6 analyzer that reads the DB treats "no generations" as "zero cost" and reports M4 token-cost metrics of exactly zero as legitimate data.

### Where

`scripts/backfill_token_costs.py:586-590`:
```python
if not generations:
    print("WARNING: No generation observations found. LangFuse data may have been purged.")
    print("         Token costs will remain at 0.")
    conn.close()
    return
```

### Reproduction

Direct replay against an empty LangFuse result:

```python
# Mock fetch_all_generations to return []
with patch("scripts.backfill_token_costs.fetch_all_generations", return_value=[]):
    main()  # returns normally, exit code 0
# DuckDB rows' token_cost all 0; nothing in the DB distinguishes "genuinely 0" from "backfill silently failed".
```

### Why fail-open is wrong here

The WARNING is printed to stdout but:
- The script's exit code is 0 — no automation catches the failure.
- There's no sentinel in the DB (no `token_cost = -1`, no `backfill_status` column) — analyzers that read `token_cost` have no way to know the column is uninformative.
- Chapter 6's M4 table silently shows uniformly-zero token costs across all rows, which a reviewer would interpret as "surprising but maybe real" rather than "instrument failure."

Same pattern called out in cross-cutting observation #5 of the parent audit: silent-degradation paths break Pillar 2 failure attribution. Here the same pattern breaks M4 attribution.

### Threat model / impact

- **M4 (token cost per task) silently zero for every row.** Every Chapter 6 cost-efficiency comparison becomes meaningless: JIT condition shows 0 tokens, static shows 0, no differentiation.
- **Compounds with #27.** #27 empties longitudinal invocations; #36 empties M4 for ALL runs (including non-longitudinal). Together they strip two of Chapter 6's four primary metrics.
- **Latency.** The bug's symptom is indistinguishable from "tasks genuinely used no tokens" to anyone not looking at the LangFuse side. Could go unnoticed until peer review.

### Remediation

**A. Non-zero exit code and no-op DB guard.**

```python
if not generations:
    print("ERROR: No generation observations found. LangFuse data may have been purged.", file=sys.stderr)
    print("       Token costs NOT backfilled. Rerun after restoring LangFuse data.", file=sys.stderr)
    conn.close()
    sys.exit(2)  # distinct from the generic error exit
```

Automation (`make analyze`, CI pipelines, Chapter 6 re-runs) stops instead of silently continuing with zero-filled data.

**B. DB-level sentinel for explicit "unknown" state.**

Add a `backfill_status` column to `evaluation_runs`:
- `"pending"` — row exists, backfill not yet run
- `"backfilled"` — backfill succeeded, `token_cost` is real
- `"backfill_failed"` — backfill ran but failed; `token_cost` is not trustworthy

Analyzer's SQL filters to `WHERE backfill_status = 'backfilled'` when aggregating M4.

**C. CLI check before invoking backfill.**

Add a `--require-generations` flag (default True) that probes LangFuse for any generations in the window BEFORE starting the full backfill. Fails fast with a clear error message.

### Regression test

```python
def test_backfill_fails_loudly_when_langfuse_returns_no_generations(tmp_path, monkeypatch):
    """No generations → sys.exit(2), not exit 0."""
    monkeypatch.setattr("scripts.backfill_token_costs.fetch_all_generations", lambda *a, **kw: [])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2  # pre-fix: 0
```

### Recommendation

**Land before Phase 5 eval.** Five-line fix + one regression test + `backfill_status` schema add is optional polish. ~15 min for the minimum fix. Must ship before any Chapter 6 M4 table is generated.

---

## Issue #37 — Per-task token attribution silently writes 0 for records in untagged runs when global per-task mode is active (P0)

### Summary

Sibling bug to #30. #30 covers the "first run has no tags → fall back to run-level for everything" direction. This one covers the inverse: "first run has tags → switch globally to per-task mode → records in LATER untagged runs silently get 0 tokens because `run_task_tokens.get(key, 0)` returns 0 for unseen keys, and there is no fallback to run-level for those records."

Both symptoms stem from the same design flaw — first-run-only probe + global decision + no per-(run, task) fallback. The remediation is shared with #30 (per-run tag detection + union).

### Where

- `scripts/backfill_token_costs.py:503` — `test_traces = fetch_traces_by_session(base_url, auth, test_run["run_id"])` (first non-static run only).
- `scripts/backfill_token_costs.py:505` — `if not has_tags: return None` (symptom already in #30).
- `scripts/backfill_token_costs.py:540-542` — per-task mode lookup without fallback:
  ```python
  key = (rec["run_id"], rec["task_id"])
  tokens = run_task_tokens.get(key, 0)
  updates.append((rec["id"], tokens))
  ```

### Reproduction

Synthetic — mixed-era DB where first probed run has tags but a later run doesn't:

```python
# run_task_tokens populated only for r1 + r2 (tagged); r3 + r4 (untagged) produce nothing.
run_task_tokens = {("r1", "T01"): 100, ("r2", "T02"): 200}
records = [
    {"id": "a", "run_id": "r1", "task_id": "T01", "condition": "full"},
    {"id": "b", "run_id": "r2", "task_id": "T02", "condition": "full"},
    {"id": "c", "run_id": "r3", "task_id": "T03", "condition": "full"},  # untagged run
    {"id": "d", "run_id": "r4", "task_id": "T04", "condition": "full"},  # untagged run
]

updates = [(r["id"], run_task_tokens.get((r["run_id"], r["task_id"]), 0))
           for r in records if r["condition"] != "static"]
# [('a', 100), ('b', 200), ('c', 0), ('d', 0)]
```

User's repro matches verbatim: `[('r1', 100), ('r2', 200), ('r3', 0), ('r4', 0)]`.

### Relationship with #30

| | First run has tags | First run no tags |
|---|---|---|
| Downstream runs with tags | Per-task mode works (correct) | **#30**: all → run-level (inflated) |
| Downstream runs without tags | **#37**: records → 0 (undercount) | **#30**: all → run-level (inflated) |

Per-run tag detection (the fix in #30's remediation section) closes both diagonal cells. Neither bug alone is cleanly fixable without the other — they share a single remediation.

### Threat model / impact

- **Direction of bias: anti-JIT-in-untagged-runs.** Untagged-era runs (pre-Step-13.7) get 0 tokens in this mode. If those runs are the STATIC condition (hand-written tools, predates tagging), JIT conditions look ~2× more expensive than reality in the comparison — same direction as #28 but different mechanism.
- **Silently ignores the DB's heterogeneity.** A reviewer inspecting the DB sees token_cost=0 for ~half the rows and token_cost=<real> for the other half, with no explanation.
- **Compounds with #30 and #28.** #30's inflation applies when first-run untagged; #37's zeroing applies when first-run tagged + later run untagged. Together they create an N-way interaction in which every permutation of the DB's tag distribution produces a different wrong answer.

### Remediation

Same as #30 Option A — per-run tag detection, with the untagged runs routed through the run-level fallback (post-#28-fix) instead of silently getting 0:

```python
def compute_per_task_costs(base_url, auth, records, run_windows, run_summaries):
    updates: list[tuple[str, int]] = []
    tagged_run_ids: set[str] = set()
    run_task_tokens: dict[tuple[str, str], int] = {}

    for rw in run_windows:
        if rw["condition"] == "static":
            continue
        traces = fetch_traces_by_session(base_url, auth, rw["run_id"])
        has_task_tags = any(t.get("task_id") for t in traces)
        if not has_task_tags:
            continue  # Untagged: caller will handle via run-level fallback
        tagged_run_ids.add(rw["run_id"])
        ...  # same per-(rid, task_id) bucketing as today

    for rec in records:
        if rec["condition"] == "static":
            updates.append((rec["id"], 0))
            continue

        if rec["run_id"] in tagged_run_ids:
            # Per-task attribution
            key = (rec["run_id"], rec["task_id"])
            updates.append((rec["id"], run_task_tokens.get(key, 0)))
        else:
            # Run-level fallback (post-#28-fix divides by n_records, not n_distinct_tasks)
            summary = run_summaries.get(rec["run_id"])
            if summary and summary["n_records"] > 0:
                per_task = summary["total_tokens"] // summary["n_records"]
                updates.append((rec["id"], per_task))
            else:
                updates.append((rec["id"], 0))

    return updates
```

Caller signature change: `compute_per_task_costs` now takes `run_summaries` so it can fall back per-run.

### Regression test

```python
def test_per_task_mode_falls_back_to_run_level_for_untagged_runs(monkeypatch):
    """Records in untagged runs get run-level fallback, not 0."""
    # Seed two runs: r1 tagged, r2 untagged. Assert r2 records get run-level values, not 0.
    ...
```

### Recommendation

**Bundle with #30 — same fix.** ~30 LOC + 2 regression tests. ~45 min combined.

---

## Issue #38 — `analyze_results.py` pools every non-pilot row across every run in the DuckDB; prior-run contamination (P0)

### Summary

The Chapter 6 analyzer filters only by `WHERE is_pilot = FALSE`. No `run_id`, no timestamp window, no config-hash. If `data/eval/metrics.duckdb` accumulates rows across multiple runs (re-runs after code changes, different LLM providers, different seeds, exploratory runs), **all** non-pilot rows are pooled into the condition-level and per-task aggregates. The DB is append-only; run_pilot.py at line 47 hardcodes the same path and has no reset step.

### Where

- **DB path:** `scripts/run_pilot.py:47` — `DB_PATH = str(DATA_DIR / "eval" / "metrics.duckdb")`. Same path in `scripts/run_evaluation.py`, `scripts/run_longitudinal.py`. No rotation, no per-run subdir.
- **Condition-level query:** `scripts/analyze_results.py:132` — `WHERE is_pilot = FALSE AND ({task_filter})`.
- **Per-task query:** `scripts/analyze_results.py:160` — `WHERE is_pilot = FALSE`.
- **Sibling aggregates:** `print_classification_accuracy`, `print_gini_summary` in the same file — audit for the same pattern.

### Reproduction

Trivial — run `run_pilot.py` twice with different configs, then run `analyze_results.py`:

```bash
# First run: 200 rows across 4 conditions
uv run scripts/run_pilot.py --n-runs 10
# Code change; re-run
uv run scripts/run_pilot.py --n-runs 10
# DB now has 400 rows, spanning two different code versions
uv run scripts/analyze_results.py
# Report aggregates all 400 rows as one experiment
```

Any metrics.duckdb with non-pilot rows from any prior run is affected.

### Threat model / impact

- **Chapter 6 M1/M2/M3/M4/M5/M6 tables mix experiments.** Two runs with different code produce two populations of rows; the analyzer reports the mean of the mixture, not either population.
- **Direction of bias: unpredictable.** Depends on what prior runs are in the DB. A run with a known-broken pipeline that was re-run after the fix would drag the "fixed" numbers back down.
- **Silent.** No row count printed at the start of the analyzer; no "you have 400 rows from 2 runs" warning.
- **Compounds with #31 (dirty-baseline).** #31 mixes seeded+evolved inside one run's registry; #38 mixes multiple runs' records in the DuckDB. Together they make Chapter 6 stats unreproducible at two layers.
- **#24 is the longitudinal analog.** #24 handles the same design flaw for `analyze_longitudinal.py`. #38 is the equivalent for the primary per-condition analyzer.

### Remediation

**A. Require `--run-id` or auto-select the most recent run.**

```python
# scripts/analyze_results.py:
parser.add_argument(
    "--run-ids", nargs="+", default=None,
    help="Specific run_ids to include (default: all rows from most recent run-set)",
)
parser.add_argument(
    "--since", type=str, default=None,
    help="Only include rows with timestamp >= SINCE (ISO format)",
)

def query_condition_summary(conn, task_filter, run_ids=None, since=None):
    where = ["is_pilot = FALSE", task_filter]
    params = []
    if run_ids:
        where.append(f"run_id IN ({','.join('?' * len(run_ids))})")
        params.extend(run_ids)
    if since:
        where.append("timestamp >= ?")
        params.append(since)
    sql = f"SELECT ... FROM evaluation_runs WHERE {' AND '.join(where)} GROUP BY condition ..."
    ...
```

**B. Auto-select the most recent run-set when `--run-ids` not provided + WARN.**

```python
if not args.run_ids and not args.since:
    latest = conn.execute(
        "SELECT DISTINCT run_id, MAX(timestamp) AS t "
        "FROM evaluation_runs WHERE is_pilot = FALSE GROUP BY run_id "
        "ORDER BY t DESC LIMIT 20"
    ).fetchall()
    if len(latest) > 4:
        print(f"WARNING: DB contains {len(latest)} distinct non-pilot run_ids. "
              f"Auto-selecting the 4 most recent (one per condition).")
        args.run_ids = [r[0] for r in latest[:4]]
```

4 because there are 4 baseline conditions — one run_id per condition is the canonical eval shape.

**C. Per-run config hash stored alongside rows.**

Add `config_hash` column to `evaluation_runs`. Populated at write time from `EvaluationConfig`'s hash. Analyzer filters by config_hash to ensure only rows from the same config participate in one aggregate.

**D. Analyzer dry-run output of what rows it's aggregating.**

Every query should print a line like "Aggregating 200 rows from run_ids={...}, conditions={...}". Loud by default.

### Regression test

```python
def test_analyze_results_scopes_to_specified_run_ids(tmp_path):
    # Seed DB with 200 rows from run A + 200 rows from run B (different condition rates).
    # Invoke analyze with --run-ids A only.
    # Assert condition summary counts come from A alone, not A+B.
    ...
```

### Recommendation

**Land before any Chapter 6 M1/M2/etc table is generated.** Bundles structurally with #24 (same fix pattern, different analyzer). Estimated ~1-1.5 hours for per-run scoping + "auto-select latest" + warnings + tests.

### Legacy data

Any pre-fix Chapter 6 output is a mixture of whatever was in the DB at analysis time. Re-run post-fix against a scoped run-set. No DB-level migration needed.

---

## Issue #39 — `analyze_context_utilization.py` (Row 13) fetches LangFuse traces by name + time window only; no env, session, or run filter (P0)

### Summary

The Chapter 6 Row 13 T3 "naive-verbose" evidence analyzer pulls traces from LangFuse's `/api/public/traces` endpoint filtered only by `name` and client-side timestamp check. It has no `environment` filter (so smoke-test runs from `PYTHON_ENV=dev` or `staging` enter the pool), no `session_id` filter (so prior campaigns in the same time window are included), and no CLI switch to narrow to a specific eval campaign. Any trace with a matching name landing in the last `--days` window is evidence, regardless of its origin.

### Where

- **Fetch (no env/session params):** `scripts/analyze_context_utilization.py:93-96` — `client.get("/api/public/traces", params={"name": name, "limit": 100, "page": page})`.
- **Main / CLI (no run-scope arg):** `scripts/analyze_context_utilization.py:406-444` — only `--days` (default 14) and `--detail` / `--csv` switches.
- **Fetch invocation:** `scripts/analyze_context_utilization.py:451` — `fetch_traces(client, agent_name, start, end)` with no additional filter.
- **Row 13 context:** `docs/run_sheet.md:59` names this script as the Row 13 T3 evidence source.

### Reproduction

Any LangFuse instance with traces from multiple environments or multiple eval campaigns in the same 14-day window produces a mixed result. Direct inspection of the code path confirms the REST query carries no scoping parameter beyond trace name.

### Threat model / impact

- **Chapter 6 Row 13 (T3 naive-verbose) evidence is not reproducible per-campaign.** The same analyzer invocation on the same DB at different moments returns different numbers depending on what's fresh in LangFuse.
- **Smoke-test contamination is the biggest risk.** Developers run smoke tests continuously (per CLAUDE.md's "test before commit" pattern); those traces share names with production eval traces and flood the 14-day window.
- **Direction of bias: unpredictable.** Smoke tests tend to use short prompts (low context) and succeed fast; old exploratory runs tend to have long prompts (high context). Mixture weights both, producing a misleading "average."
- **Same structural family as #24, #38.** Three analyzers, one design flaw: aggregate-everything-in-a-window with no run-scope.

### Remediation

**A. Add `--environment` CLI arg + thread into LangFuse query.**

```python
parser.add_argument("--environment", default="production",
                    help="LangFuse environment tag (default: production)")
# ...
params = {"name": name, "limit": 100, "page": page, "environment": args.environment}
```

LangFuse's REST API supports `environment` on `/api/public/traces`. Setting it to `"production"` by default excludes smoke-test and dev runs.

**B. Add `--session-ids` CLI arg + client-side filter.**

```python
parser.add_argument("--session-ids", nargs="+", default=None,
                    help="Restrict to these LangFuse session_ids (e.g., 'eval_2026-04-20_full')")
# ...
if args.session_ids and t.get("sessionId") not in args.session_ids:
    continue
```

Thesis runs already tag session_id per-condition per `run_sheet.md`; the analyzer just has to consume it.

**C. Print the scope at the top of the report.**

```
Scope: environment=production, session_ids=['eval_2026-04-20_full'],
       window=2026-04-06 → 2026-04-20, N traces fetched=427.
```

Loud signal if the user forgot to narrow scope.

### Recommendation

**Bundle with #24 + #38 + #40 + #41 + #42 as one "analyzer-scoping" batch.** Same fix pattern across six analyzers. Estimated ~30 min per analyzer; ~3h for the batch. Must land before Chapter 6 evidence tables.

---

## Issue #40 — `analyze_compression_loss.py` (Row 14) not run-isolated (P0)

### Summary

Same structural flaw as #38, different analyzer. `load_failures` at `analyze_compression_loss.py:213-232` filters only by `NOT is_pilot` + optional `--condition`. No `run_id` scoping. Any DuckDB with failure rows from prior runs pools them into the current compression-loss report.

### Where

- `scripts/analyze_compression_loss.py:217` — `clauses = ["NOT is_pilot", "(NOT gate_passed OR NOT output_in_range)"]` — the full WHERE. Run-scope not included.
- `scripts/analyze_compression_loss.py:229` — `WHERE {where}` — the query.
- `src/registry/evaluation_store.py:192` — `record_run` inserts the row; `run_id` column exists but is never referenced by the analyzer.
- **Row 14 context:** `docs/run_sheet.md:60`.

### Reproduction

```python
# Toy DB with one old failing run + one new failing run:
eval_store.record_run(EvaluationRecord(run_id="old", gate_passed=False, ...))
eval_store.record_run(EvaluationRecord(run_id="new", gate_passed=False, ...))
rows = load_failures(conn)
assert len(rows) == 2  # Both pooled; no way to scope to "new" alone.
```

User's repro matches: `n_rows_loaded = 2` when one was old and one was new.

### Threat model / impact

- **Row 14 compression-loss numbers mix experiments.** Same risk surface as #38 but for a distinct Chapter 6 section.
- **Cross-cutting pattern.** #24 (longitudinal), #38 (results), #39 (context utilization), this, #41, #42 — six instances of the same analyzer-scoping flaw across six scripts.

### Remediation

Mirror #38's remediation:
```python
parser.add_argument("--run-ids", nargs="+", default=None)
parser.add_argument("--since", type=str, default=None)

def load_failures(conn, *, condition=None, run_ids=None, since=None):
    clauses = ["NOT is_pilot", "(NOT gate_passed OR NOT output_in_range)"]
    params = []
    if condition is not None:
        clauses.append("condition = ?"); params.append(condition)
    if run_ids:
        clauses.append(f"run_id IN ({','.join('?' * len(run_ids))})")
        params.extend(run_ids)
    if since:
        clauses.append("timestamp >= ?"); params.append(since)
    ...
```

Auto-select latest run-set when neither `--run-ids` nor `--since` provided, with WARN listing the selected set.

### Recommendation

**Bundle with #24 + #38 + #39 + #41 + #42** as the analyzer-scoping batch.

---

## Issue #41 — `audit_contamination.py` (Row 16) not run-isolated; M2 manual labels sampled from mixed-campaign pool (P0)

### Summary

The M2 contamination audit tool samples unlabeled rows for manual human labeling. Its sampling WHERE clause filters by `human_label IS NULL`, `NOT is_pilot`, `condition IN (...)`, `capability_type IN (...)`, and `generated_artifact_count > 0` — but not by `run_id`. An expert labeler is shown rows from every campaign that ever wrote to the DuckDB, not just the current campaign.

### Where

- `scripts/audit_contamination.py:129` — `WHERE human_label IS NULL`.
- `scripts/audit_contamination.py:130-134` — remaining filters (`NOT is_pilot`, `condition IN`, `capability_type IN`, `generated_artifact_count > 0`). No `run_id`.
- **Row 16 context:** `docs/run_sheet.md:62`.

### Reproduction

Direct toy-DB replay confirms rows from multiple `run_id` values enter the sample whenever they share the other filter values.

### Threat model / impact

- **M2 methodology integrity compromised.** M2 is a manual label; its sampling pool needs to match the campaign it's characterizing. Cross-campaign sampling means the labels describe the DB's history, not the current experiment.
- **Labeled rows leak across campaigns.** A row labeled during campaign A remains labeled (`human_label IS NOT NULL`) and is excluded from campaign B's sample. If row budgets are tight, the labeling budget for campaign B is effectively consumed by whatever was labeled for A.
- **Most dangerous of the six analyzer-scoping issues.** M2 is expensive (human time); mixed-campaign sampling wastes that time silently.

### Remediation

Same pattern as #38/#40 — add `--run-ids` / `--since` CLI args; thread into the WHERE clause. Additionally, consider adding a `campaign_id` column that a campaign can set at the top of a labeling session so rows labeled under that session are clearly attributed.

### Recommendation

**Bundle with #24/#38/#39/#40/#42.** M2 is Chapter 6 headline methodology; this must ship before any M2 labeling pass begins.

---

## Issue #42 — `analyze_tier2_probes.py --environment default` (Row 15) not session-isolated (P1)

### Summary

`analyze_tier2_probes.py` aggregates LangFuse EVENT-type observations by name + optional environment + time window. The `--environment` flag (when set to `production`) excludes dev/staging, but probes within the SAME environment from CONCURRENT or PRIOR eval campaigns still enter the aggregate. Session-level isolation is not available.

### Where

- `scripts/analyze_tier2_probes.py:160-168` — the REST query params: `name`, `type=EVENT`, `limit`, `page`, optional `environment`. No `session_id`, no run correlation.
- **Row 15 context:** `docs/run_sheet.md:61`.

### Reproduction

Running two `run_evaluation.py` invocations in the same production environment within the analyzer's time window pools both campaigns' Tier 2 probe-type counts.

### Threat model / impact

- **Lower severity than #39/#40/#41.** The `--environment` filter does reduce contamination vs #39 (which has no env filter at all). Session-level isolation is the residual gap — smaller surface, but still present.
- **Chapter 6 Row 15 probe-type distribution skewed whenever two prod-env runs overlap.** Likely smallest factual impact of the six analyzer-scoping issues, but same fix pattern.

### Remediation

Add `--session-ids` CLI arg and filter client-side (same shape as #39's remediation B). Bundle.

### Recommendation

**Bundle with #24/#38/#39/#40/#41.**

---

## Issue #43 — `scripts/test_allow_network.py` smoke gate returns pass-by-skip when host has no network (P1)

### Summary

Two smoke tests in the Row 4 network-plumbing gate check `if not _host_has_network(): return True`. A CI runner, a developer's offline laptop, or a sandboxed environment without internet can run the gate and get a green pass — without ever validating that the `--network` flag propagates correctly through Docker. The gate's purpose is to catch plumbing regressions; skipping on no-host-network converts the gate into a no-op on exactly the environments where plumbing is most likely to differ.

### Where

- `scripts/test_allow_network.py:196-200` — inside `test_docker_network_flag_wired()`: `if not _host_has_network(): print("⚠ Skipping..."); return True`.
- `scripts/test_allow_network.py:225-227` — inside `test_network_allowed_succeeds()`: same pattern.

### Reproduction

```bash
# Disable host network
sudo ifconfig en0 down   # or equivalent for your NIC
uv run scripts/test_allow_network.py
# Output: two ⚠ Skipping messages, exit 0 with "gate pass".
```

The smoke runner then moves on as if the plumbing were validated.

### Threat model / impact

- **Silent CI regression.** A CI pipeline on an offline runner (air-gapped CI, outbound-firewalled CI) reports the gate green even after plumbing breaks. The next eval run produces incorrect network-differential behavior but the gate gave no warning.
- **Ext4/5/6 finding #14 remediation undermined.** Issue #14 locks the "no workspace drop" invariant in `sandbox_execute_in_workspace`; if this smoke gate is the only runtime check for that invariant and the gate no-ops on offline hosts, the lock is effectively off.
- **Direction of bias: masks plumbing regressions.** Cannot differentiate "plumbing works" from "can't test plumbing."

### Remediation

**A. Exit code 2 ("cannot validate") instead of True ("validated").**

```python
if not _host_has_network():
    print("✗ Cannot validate — host has no network. Plumbing check SKIPPED, not PASSED.")
    sys.exit(2)
```

Smoke runner aggregator differentiates exit 0 (validated) from exit 2 (couldn't validate) from exit 1 (failed validation). Red/yellow/green instead of red/green.

**B. Docker-only fallback test.**

Even offline, `docker run --network bridge` vs `--network none` should behave differently at the DNS layer (nameserver available vs not). A lightweight test that doesn't require real network connectivity — just Docker's network flag enforcement — can validate plumbing without internet:

```python
def test_network_flag_enforcement_offline():
    """Even without internet: bridge mode has a DNS resolver; none mode doesn't."""
    code = "import socket; print(socket.gethostbyname('localhost') if False else 'NONET')"
    # Better: try to resolve a local hostname via /etc/hosts only — should differ per mode.
    ...
```

Keeps the gate useful on offline hosts.

**C. At minimum: a loud CI-readable marker.**

```python
if not _host_has_network():
    print("::warning::Host has no network — plumbing gate skipped.", file=sys.stderr)
    return True  # Still exit green but emit GitHub Actions' ::warning:: so the CI UI flags it.
```

### Recommendation

**Land with the analyzer-scoping batch.** Small file; single purpose; 5-10 LOC change. ~15 min.

---

## Current open set (ext6 only — all ext4/ext5 findings are ✅ FIXED)

The parent audit closed #1-#18 on 2026-04-20; the twenty-five ext6 findings below are the only remaining eval-blocking security/robustness issues:

| # | Title | Direction of bias | Severity |
|---|---|---|---|
| 19 | Tier 2 network differential unreachable | Pro-artifact (F2 detection dead) | **P0** |
| 20 | Behavioral schema targets wrong function | Pro-artifact (Tier 2 probes wrong inputs) | **P0** |
| 21 | Behavioral sandbox parser fragile | Anti-artifact (false inconclusive) | P1 |
| 22 | Coverage replay parser fragility | Anti-optimizer (compounds with fixed #16) | P1 |
| 23 | Longitudinal flush duplicates events | Inflates Gini + KL | **P0** |
| 24 | Analyzer mixes run_ids | Cross-run contamination | **P0** |
| 25 | Gate + adversarial fail-open | Pro-artifact (most dangerous) | **P0** |
| 26 | Retry-loop name-only identity | Pro-artifact (latent: changed code reuses PASS) | P1 |
| 27 | Longitudinal runner metrics wiring | Anti-data (longitudinal invocation table empty) | **P0** |
| 28 | Token-cost backfill 2× inflation | Anti-JIT-in-longitudinal (M4 inflated) | **P0** |
| 29 | Lead-cache phantom invocations | Inflates Gini / invocation counts per lead-reuse | **P0** |
| 30 | Backfill per-task attribution probe | Compounds #28 (force run-level mode in mixed-era DB) | P1 |
| 31 | Longitudinal snapshot cleanliness | Dirty baseline contaminates every Pillar 3 longitudinal conclusion | **P0** |
| 32 | Success predicate ignores shape_mismatch | Inflates success rate on all file-output tasks (T07/T11/T16) | **P0** |
| 33 | T11 xlsx validator fail-open | Compounds #32 on T11 specifically | P1 |
| 34 | Non-deterministic seed derivation | Reproducibility contract broken across processes | P1 |
| 35 | Permissive task validators (9 tasks) | Pro-all-conditions (hallucinated outputs pass on 9/20 testbed tasks) | **P0** — ✅ pragmatic subset fixed 2026-04-20 (T05/T09/T10/T13 tightened + Chapter 4.5 disclosure for T02/T08/T12/T15/T16 via one-time WARN) |
| 36 | Backfill LangFuse-missing fail-open | Silent M4=0 when LangFuse empty; exit code 0 hides the failure | **P0** |
| 37 | Backfill per-task untagged-run zeros | Inverse of #30 — untagged runs/records silently get 0 tokens | **P0** |
| 38 | Eval analyzer not run-isolated | Pools every non-pilot row; prior-run contamination of Chapter 6 stats | **P0** |
| 39 | `analyze_context_utilization.py` not scoped | Row 13 T3 evidence mixes smoke/test/old runs | **P0** |
| 40 | `analyze_compression_loss.py` not run-isolated | Row 14 compression-loss contamination | **P0** |
| 41 | `audit_contamination.py` not run-isolated | M2 labels sampled from mixed-campaign pool (worst waste-of-human-time) | **P0** |
| 42 | `analyze_tier2_probes.py` session-isolation gap | Row 15 concurrent-prod-run contamination (lower surface) | P1 |
| 43 | `test_allow_network.py` offline fail-open | Row 4 smoke gate no-ops on offline hosts (masks plumbing regressions) | P1 |

**Recommended batch order:** #27 first — prerequisite for any longitudinal data to exist at all; running longitudinals before #27 produces empty invocation tables that #23/#24 and ext5 #18 fixes silently operate on. **#29 must land with #27** — fixing #27 alone flips longitudinal telemetry from "empty" to "inflated by lead-reuse factor." **#31 must also land before any longitudinal run** — the baseline contamination invalidates Pillar 3 RQs #1/#2/#3 regardless of downstream fixes. **#35 is the single biggest effort** (~1-1.5 days full audit; or ~4-6h pragmatic subset + disclosure) and must land before any M1 success-rate table in Chapter 6. #32 + #33 together (same success-metric integrity pair). #25 next (smallest Pillar-2-contract diff, largest bias direction). **#28 + #30 + #37 as one backfill bundle** — all three live in `backfill_token_costs.py`, #30 and #37 share a single remediation (per-run tag detection), #28 is the inflation math in the same function chain. **#36 alongside #28/#30/#37** — same file, trivial fix, must ship together. **Analyzer-scoping batch** = #24 + #38 + #39 + #40 + #41 + #42 — six analyzers, same fix pattern (`--run-ids` / `--session-ids` / `--environment` / `--since` + auto-select-latest WARN). ~3-4h for the batch; must land before any Chapter 6 evidence table. **#41 is highest priority in this batch** — M2 labeling is human-time-expensive and cross-campaign sampling wastes that budget silently. **#43 alongside the analyzer batch** — smoke-gate file, trivial fix. #34 before any Phase 5 kickoff (cheap, one-file). #19/#20 Pillar-2 behavioral cluster. #21/#22 shared-parser helper. #23/#24 longitudinal-telemetry cluster. #26 alongside #25 as a Pillar-2 contract-integrity pair. Total estimated effort: ~21-27 hours focused work (was ~17-22h before #39-#43 added — analyzer batch shares remediation pattern so marginal effort is ~3-4h for all six; #43 ~15min).

---

## Parent-document integration (still pending)

When ext6 is folded back into `security_audit_2026_04_19.md`:

1. Update header: "Eighteen findings now → Forty-three findings; #19-#43 open as of 2026-04-20."
2. Insert TL;DR rows #19-#43 after #18 (which is already ✅ FIXED).
3. Add a Tranche 3 section listing #19-#43 as the eval-blocking set that remains after the 2026-04-20 sweep.
4. Extend "Behavioral regressions expected" with: (#19) CRITICAL network_silent_fallback findings now fire; (#20) `extract_parameter_schema` takes an optional func_name; (#21/#22) sandbox/replay parsers tolerate debug prints via a shared helper; (#23) flush filters by `since=task_events_cutoff`; (#24/#38/#39/#40/#41/#42) analyzer CLIs gain `--run-ids` / `--session-ids` / `--environment` / `--since` with auto-select-latest warnings (six analyzers); (#25) gate exceptions + adversarial errors flip `overall_passed=False` with CRITICAL severity; (#26) `VerificationReport` gains `artifact_hash` field, skip logic compares by hash; (#27) `_extract_result`'s `metrics_store` becomes required kwarg, longitudinal runners updated; (#28) token-cost backfill divisor switches from distinct-task to total-record count, per-task mode splits across cycles; (#29) lead-cache hit skips `_record_step_invocation`; (#30/#37) backfill probes tag-availability per-run + falls back to run-level for untagged-run records instead of 0; (#31) longitudinal runners clean directories before `take_clean_snapshot`; (#32) success-rate analyzer predicate adds `AND NOT output_shape_mismatch`; (#33) T07 + T11 file validators return False on missing/corrupt file; (#34) seed derivation switches from `hash()` to `hashlib.sha256`; (#35) `TaskDefinition` gains `content_validator`; per-task content checks land for 9 permissive validators (M1 numbers shift); (#36) backfill exits with non-zero code when LangFuse returns no generations; (#43) `test_allow_network.py` exits with code 2 ("unable to validate") instead of pass-by-skip when host is offline.
5. End-of-audit note: append "Extended 2026-04-20 (sixth extension) with findings #19-#43 (detail sections in `security_audit_2026_04_19_ext6.md`). All #1-#18 fixed in the same session; #19-#43 open."

---

**End of extension.**
