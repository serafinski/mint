# Security / Robustness Audit — 2026-04-19, Fourth Extension (#12-#14)

**Parent document:** [`security_audit_2026_04_19.md`](./security_audit_2026_04_19.md).

**Why this file exists.** The parent audit's TL;DR table was extended 2026-04-20 with rows #12 (compositional DAG fail-open), #13 (output-persistence path traversal), and #14 (`sandbox_execute_in_workspace` workspace-mount drop), but the detail sections, Tranche ordering, "Behavioral regressions expected" list, and end-of-audit note were not. This extension file carries the missing detail without rewriting the parent.

**Status at time of write (2026-04-20):**
- #12 — **open**, new **P0**
- #13 — **open**, new **P0**
- #14 — **likely closed as a side-effect of the Issue #6 fix** (see §Issue #14 below); needs a one-line confirmation test.

**Line numbers.** All line references below are against the current `iter/6-execution-backends` tip, not the audit author's in-session numbering. The user-reported line numbers (`composition.py:706`, `runner.py:1341/1367/1379`, `sandbox_tool.py:354`) are approximately correct but drifted by ~5-15 lines after the Issue #6/#9/#11 fixes landed.

---

## Issue #12 — Compositional dependency graph is fail-open — invalid `depends_on` indices pass through, cycles silently linearize, phantom steps report success (P0)

### Summary

Three layers of the compositional executor each assume the decomposition-layer validator has already rejected pathological `depends_on` values. None of them does. A decomposition with `depends_on=[99]`, `depends_on=[-1]`, or a cyclic pair `[0↔1]` reaches `CompositionalExecutor.run()` and each of the three shapes fails differently — crash, phantom execution, or silent linearization — all while the chain-level success flag stays `True` for two of the three shapes.

### Where

- **Layer 1 — decomposition coherence validator:** `src/orchestrator/decomposition.py:156` (`_validate_chain_coherence`) only checks `requires_upstream_keys` against transitive `depends_on` predecessors' `output_schema`. It never validates that `depends_on` indices are in-range or that the DAG is acyclic. The inner BFS at line 144 (`if i < 0 or i >= len(subtasks): continue`) silently skips invalid indices instead of surfacing them.
- **Layer 2 — execution-order builder:** `src/orchestrator/composition.py:177` (`build_execution_order`). Line 192 catches `graphlib.CycleError` and logs a warning but returns `list(range(len(self.subtasks)))` — linear index order — and the chain continues as if the cycle were an ordering hint, not a correctness failure. Out-of-range indices in the DAG input (`graph[i] = set(st.depends_on)` at line 187) are **not** caught by `graphlib.TopologicalSorter`; they simply appear in the sorted output because `TopologicalSorter` treats every value in a dependency set as a node regardless of whether it's a key.
- **Layer 3 — run loop:** `src/orchestrator/composition.py:717` (`subtask = self.subtasks[step_idx]`). No bounds check. `step_idx=99` raises `IndexError`; `step_idx=-1` succeeds via Python's negative-index semantics and executes `self.subtasks[-1]` as a phantom step.

### Reproduction

Direct `graphlib` replay against a three-step chain (indices `0`, `1`, `2`):

**(a) Out-of-range positive index — crash:**
```python
subtasks[2].depends_on = [99]
# build_execution_order() → [0, 1, 99, 2]  (99 appears in static_order output)
# run loop at step_idx=99 → IndexError: list index out of range
```

**(b) Out-of-range negative index — phantom step, reports success:**
```python
subtasks[2].depends_on = [-1]
# build_execution_order() → [-1, 0, 1, 2]  (-1 appears in static_order output)
# run loop at step_idx=-1 → self.subtasks[-1] = subtasks[2] re-executed as a phantom
# Observed: chain_length=3, chain_success=True, but 4 step invocations with only 3 subtasks
```

**(c) Cycle between two subtasks — silent linearization, reports success:**
```python
subtasks[0].depends_on = [1]
subtasks[1].depends_on = [0]
# build_execution_order() → graphlib.CycleError → caught → returns [0, 1, 2]
# chain runs in index order with no dependency-satisfaction guarantee
# Observed: warning log line, chain_success=True
```

All three confirmed by direct replay on tip of `iter/6-execution-backends`.

### Why the three-tier gate does not cover this

The gate verifies individual **artifacts** (one MCP tool, one skill, one agent). The dependency graph is built at **decomposition** time from LLM-emitted `SubTask.depends_on` lists and assembled into the execution DAG at **composition** time. No artifact-level check can catch a bad cross-artifact reference. The §12 finding #14/#15/#16 strict-gate pattern already names three LLM-dependent stages that must fail closed on retry exhaustion (decomposition, classification, verification). **Dependency-index validity is a decomposition sub-concern that the current decomposition sticky-flag does not cover** — `decomposition_succeeded=True` even when the emitted DAG is malformed.

### Threat model / impact

- **Chapter 6 telemetry contamination (primary concern).** Phantom-step (b) and silent-linearization (c) both emit `chain_success=True` into `EvaluationRecord.chain_success`. The 400-record eval aggregates this as the primary compositional-execution metric. An LLM that produces cyclic decompositions (observed empirically on at least T16 AGENT during §12 finding #17 debugging) silently inflates the reported success rate.
- **§12 finding #15 commitment violated.** Finding #15 claims decomposition is strict-gate with 3x retry + sticky-flag + fail-closed. Invalid `depends_on` indices and cycles are a decomposition-output pathology; the current validator passes them through.
- **Silent semantic drift in Pillar 3 merge/split feedback.** Split generates new variants from parent failure cases (`src/verification/coverage.py`). When a "failure case" is actually a phantom-step success mis-reported as a real success, Split has nothing to learn from and the optimizer loop wastes cycles.
- **IndexError crash on (a) is the loud failure** and is likely covered by the CLI's top-level try/except in `execute_task`, but the crash surfaces as a generic execution error (exit code 6 after #10's fix), not as a decomposition-layer attribution. Pillar 2 failure-mode attribution is wrong for this case.

### Remediation

Three-layer strict-gate, mirroring §12 finding #14/#15/#16:

**A. Decomposition-layer validator — new check in `_validate_chain_coherence` or sibling:**

```python
def _validate_depends_on_indices(subtasks: list[SubTask]) -> list[str]:
    violations: list[str] = []
    n = len(subtasks)
    for i, st in enumerate(subtasks):
        for dep in st.depends_on:
            if not isinstance(dep, int) or dep < 0 or dep >= n:
                violations.append(f"Subtask {i} has out-of-range depends_on={dep} (valid: 0..{n-1})")
            if dep == i:
                violations.append(f"Subtask {i} depends on itself")
    # Cycle check via graphlib
    try:
        graph = {i: set(st.depends_on) for i, st in enumerate(subtasks)}
        list(graphlib.TopologicalSorter(graph).static_order())
    except graphlib.CycleError as exc:
        violations.append(f"Subtask dependency cycle: {exc.args}")
    return violations
```

Violations flip `decomposition_succeeded=False` on the sticky flag, trigger the 3x retry budget, and on exhaustion surface as CLI exit **2** (existing decomposition exit code) with a specific error category (`invalid_depends_on` / `cycle_detected`).

**B. Fail-closed `build_execution_order`:**

```python
def build_execution_order(self) -> list[int] | None:
    if len(self.subtasks) <= 1:
        return list(range(len(self.subtasks)))
    n = len(self.subtasks)
    graph: dict[int, set[int]] = {}
    for i, st in enumerate(self.subtasks):
        for dep in st.depends_on:
            if dep < 0 or dep >= n:
                logger.error("Subtask %d has out-of-range depends_on=%d; refusing to build order", i, dep)
                return None
        graph[i] = set(st.depends_on)
    try:
        return list(graphlib.TopologicalSorter(graph).static_order())
    except graphlib.CycleError:
        logger.error("Cycle detected in subtask dependencies; refusing linear fallback")
        return None
```

Return `None` is the strict-gate signal; `run()` checks for `None` and fails the chain immediately with `failed_step_reason="invalid_dag"` instead of silently linearizing. Drop the linear-fallback path entirely — it has no legitimate caller once (A) lands.

**C. Bounds check at the run loop (defence-in-depth):**

```python
for position, step_idx in enumerate(execution_order):
    if not (0 <= step_idx < len(self.subtasks)):
        logger.error("Execution order contains out-of-range step_idx=%d; failing chain", step_idx)
        return ChainResult(
            chain_length=len(execution_order),
            chain_success=False,
            step_results=step_results,
            step_timings=step_timings,
            failed_step_index=step_idx,
            failed_step_name=f"<out-of-range: {step_idx}>",
        )
    subtask = self.subtasks[step_idx]
    ...
```

Layer C exists so a future regression at (A) or (B) cannot silently execute the negative-index phantom step.

### Test coverage

```python
def test_depends_on_out_of_range_positive_flips_decomposition_sticky_flag():
    subtasks = [SubTask(...) for _ in range(3)]
    subtasks[2].depends_on = [99]
    violations = _validate_depends_on_indices(subtasks)
    assert any("out-of-range" in v for v in violations)

def test_depends_on_negative_rejected_at_validator():
    subtasks = [SubTask(...) for _ in range(3)]
    subtasks[2].depends_on = [-1]
    assert _validate_depends_on_indices(subtasks)

def test_cycle_between_two_subtasks_rejected():
    subtasks = [SubTask(...) for _ in range(2)]
    subtasks[0].depends_on = [1]
    subtasks[1].depends_on = [0]
    assert _validate_depends_on_indices(subtasks)

def test_build_execution_order_returns_none_on_cycle():
    executor = CompositionalExecutor(subtasks=[<cyclic pair>], ...)
    assert executor.build_execution_order() is None

def test_build_execution_order_returns_none_on_out_of_range():
    executor = CompositionalExecutor(subtasks=[SubTask(depends_on=[99])], ...)
    assert executor.build_execution_order() is None

def test_run_fails_chain_when_execution_order_is_none():
    # Bypass validator (simulate a post-validator regression); assert run() fails clean.
    ...
```

### Recommendation

**Land all three layers before the 400-record Phase 5 eval.** (A) alone closes the common case; (B) + (C) are the defence-in-depth that matches the §12 finding #14/#15/#16 strict-gate pattern used elsewhere. Estimated effort: ~60 LOC + ~8 unit tests, ~1.5-2 hours.

---

## Issue #13 — Output persistence writes outside the run directory via three unsanitized path joins (P0)

### Summary

`_save_output` in `src/cli/runner.py` constructs filesystem paths by concatenating three LLM- or LLM-metadata-derived strings with the user-supplied `output_dir`:

1. `cap_name` (from `artifact.capability_name`) → drives `run_dir = output_dir / f"{cap_name}_{ts}"`
2. Output dict keys → drive `file_path = run_dir / f"{key}{ext}"`
3. `output_files` dict keys (file names from the compositional executor's workspace scan) → drive `file_dest = run_dir / fname`

None of the three paths are sanitized. `cap_name='../escape'`, `key='../escape-key'`, or `fname='../escape.bin'` all escape `mint_output/<run>/` via `pathlib`'s path-join semantics (`Path('/a/b') / '../escape'` → `/a/escape`).

### Where

- `src/cli/runner.py:1346` — `run_dir = output_dir / f"{cap_name}_{ts}"` (join with `cap_name`)
- `src/cli/runner.py:1381` — `file_path = run_dir / f"{key}{ext}"` (join with output dict key)
- `src/cli/runner.py:1393` — `file_dest = run_dir / fname` (join with output_files dict key)

### Reproduction

```python
from pathlib import Path
base = Path("/tmp/mint_output")

# Site 1: cap_name
cap_name = "../escape"
ts = "20260420_120000"
run_dir = base / f"{cap_name}_{ts}"
# → PosixPath('/tmp/mint_output/../escape_20260420_120000')
# → resolved: /tmp/escape_20260420_120000  (outside mint_output)

# Site 2: output key
run_dir = base / "good_run"
key = "../pwn"
ext = ".csv"
file_path = run_dir / f"{key}{ext}"
# → PosixPath('/tmp/mint_output/good_run/../pwn.csv')
# → resolved: /tmp/mint_output/pwn.csv  (outside run_dir)

# Site 3: output_files filename
fname = "../escape.bin"
file_dest = run_dir / fname
# → PosixPath('/tmp/mint_output/good_run/../escape.bin')
# → resolved: /tmp/mint_output/escape.bin  (outside run_dir)
```

Confirmed by direct `pathlib` replay.

### Threat model / impact

- **Source of the untrusted string at site 1 (`cap_name`).** `GeneratedArtifact.capability_name` flows from the MCP-tool / skill / agent generator's LLM output. The generator's `_validate_code` enforces function-name constraints but not capability-name constraints. A prompt-injection or honest misunderstanding in the generator LLM produces a capability name the executor then treats as a path segment. The skill-name regex (`^[a-z][a-z0-9-]{0,63}$`) used by `tools/init_skill.py` post-Issue-#2-fix is exactly the right whitelist to reuse here.
- **Source at site 2 (output dict keys).** LLM-generated tool code returns a JSON dict; the key names are LLM-chosen. A malicious or confused generator could emit `{"../pwn": "contents"}` and write to the parent of `run_dir`.
- **Source at site 3 (output_files keys).** File names scanned from the Docker workspace by `CompositionalExecutor._collect_output_files`. Inside Docker the generated code runs under `--network none` with the `/workspace` bind mount; it can call `Path("../foo.bin").write_bytes(...)` and the scanner will pick up `../foo.bin` as a candidate key, which then escapes when written back to the host `run_dir`.
- **Realistic worst case.** Arbitrary-path writes confined to the user who invoked `mint` (CLI is not setuid). An attacker who can influence generator output can clobber files the CLI user can write to. On a shared dev machine with multiple checkouts, this includes other checkouts' `data/`, `.git/`, or `~/.claude/` directories. In the eval harness running 400 tasks unattended, a single generated capability that writes `../../.../some/path` overwrites host files silently.
- **Telemetry side-effect.** Files written outside `run_dir` are never picked up by the per-run manifest (`<cap_name>_<ts>.run.json`) or the `_save_output` saved-list output. The eval harness's post-run fixture collection looks inside `run_dir`; escaped files are invisible to the harness and to Chapter 6 analysis. This is a **telemetry integrity issue** in addition to the filesystem-safety issue.
- **CLAUDE.md claim drift.** CLAUDE.md's "Three Distinct Execution Backends" section claims Docker isolation of generated MCP code. The **output path of the isolated code** is still host-filesystem and is not isolated. The claim is narrowly true (execution) but misleading (persistence).

### Remediation

**A. Shared containment helper.**

```python
# src/cli/path_safety.py
from pathlib import Path

class PathEscapeError(ValueError):
    pass

def contain(base: Path, *parts: str) -> Path:
    """Join ``parts`` onto ``base`` and raise if the result escapes ``base``."""
    base = base.resolve(strict=False)
    candidate = base
    for p in parts:
        candidate = candidate / p
    candidate = candidate.resolve(strict=False)
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise PathEscapeError(f"{candidate} escapes {base} (parts={parts})") from exc
    return candidate
```

**B. Apply at all three sites.**

```python
# runner.py:1346
try:
    run_dir = contain(output_dir, f"{_sanitize_cap_name(cap_name)}_{ts}")
except PathEscapeError as exc:
    logger.error("Refusing to create run_dir: %s", exc)
    return

# runner.py:1381
try:
    file_path = contain(run_dir, f"{key}{ext}")
except PathEscapeError as exc:
    logger.warning("Refusing to write output key %r: %s", key, exc)
    continue  # Skip this key; keep processing others.

# runner.py:1393
try:
    file_dest = contain(run_dir, fname)
except PathEscapeError as exc:
    logger.warning("Refusing to write output_files entry %r: %s", fname, exc)
    continue
```

**C. Whitelist `cap_name`.**

```python
import re
_CAP_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")

def _sanitize_cap_name(name: str) -> str:
    if not _CAP_NAME_RE.match(name):
        raise ValueError(f"cap_name {name!r} does not match {_CAP_NAME_RE.pattern}")
    return name
```

Matches the Issue #2 remediation's whitelist, so the generator-output boundary and the skill-name boundary share one policy.

### Test coverage

```python
@pytest.mark.parametrize("bad", ["../escape", "..", "./../x", "a/../b", "/absolute/path"])
def test_save_output_rejects_path_escape_in_cap_name(tmp_path, bad):
    artifact = SimpleNamespace(capability_name=bad)
    _save_output(tmp_path, {"k": "v"}, artifact=artifact, ...)
    # Assert nothing outside tmp_path was created
    assert list(tmp_path.parent.iterdir()) == [tmp_path]

@pytest.mark.parametrize("bad_key", ["../pwn", "..", "/absolute"])
def test_save_output_rejects_path_escape_in_output_keys(tmp_path, bad_key):
    long_value = "a" * 600  # triggers the >500-char per-key scan
    _save_output(tmp_path, {bad_key: long_value}, artifact=good_artifact, ...)
    # Assert the scan skipped the bad key and nothing escaped
    for p in tmp_path.rglob("*"):
        assert p.is_relative_to(tmp_path)

@pytest.mark.parametrize("bad_fname", ["../escape.bin", "..", "/etc/passwd"])
def test_save_output_rejects_path_escape_in_output_files(tmp_path, bad_fname):
    _save_output(tmp_path, {}, output_files={bad_fname: b"x"}, artifact=good_artifact, ...)
    for p in tmp_path.rglob("*"):
        assert p.is_relative_to(tmp_path)
```

### Recommendation

**Land before the 400-record Phase 5 eval.** The eval runs 400 unattended tasks; a single generator LLM emitting a cap-name or output key with `..` in it corrupts data outside the eval's own scratch directory and contaminates subsequent tasks or the checkout itself. Estimated effort: ~40 LOC (helper + sanitizer + three site updates) + ~9 parametrized tests, ~45 min.

---

## Issue #14 — `sandbox_execute_in_workspace` drops the workspace mount in its regex-reject fallback (P1 — likely closed by Issue #6 fix)

### Summary

The originally-filed shape of #14: when `sandbox_execute_in_workspace` was called with `extra_packages=['pandas>=2.0,<3']` (a valid PEP 508 requirement Issue #6's narrow regex rejected), `_sandbox_execute_with_install` took a no-install fallback that called `sandbox_execute(code, timeout=timeout, allow_network=allow_network)` — **without** a `workspace_path` argument. The declared workspace mount was silently dropped. Any code path that depends on `/workspace` then failed inside the sandbox with `FileNotFoundError` and was mis-attributed to the generated artifact instead of to the fallback path.

### Current state (post-Issue-#6 fix, 2026-04-20)

The Issue #6 remediation rewrote the `if not packages:` branch at `src/verification/sandbox_tool.py:373-390`. The branch **no longer calls `sandbox_execute`**; it returns a `pip_requirement_rejected` loud-fail dict directly. There is no longer a path in `_sandbox_execute_with_install` that demotes the caller to a no-mount `sandbox_execute` call. **Issue #14 is therefore closed as a side-effect of the #6 fix.**

### Where the residual check belongs

- Entry point: `src/verification/sandbox_tool.py:277` (`sandbox_execute_in_workspace` → `_sandbox_execute_with_install`).
- Former fallback site: `src/verification/sandbox_tool.py:349` (pre-#6). Post-#6 the loud-fail replaces it at lines 373-390.

### Confirmation plan

One regression test to lock the fix in:

```python
def test_sandbox_execute_in_workspace_all_invalid_packages_loud_fails(tmp_path):
    """Post-#6: all-invalid extra_packages must NOT fall through to a no-workspace sandbox_execute."""
    result = sandbox_execute_in_workspace(
        code="import pandas; print('should never run')",
        workspace_path=tmp_path,
        extra_packages=["this is not a valid requirement!!!"],  # all rejected by PEP 508 parse
    )
    assert result.get("pip_requirement_rejected"), (
        "Pre-#6 this silently called sandbox_execute(..., <no workspace>). "
        "Post-#6 it must return a pip_requirement_rejected loud-fail dict."
    )
    assert result["success"] is False
```

Add to `tests/unit/test_sandbox_tool.py`. Pairs naturally with the Issue #6 test bundle already there.

### Recommendation

Land the one confirmation test and mark #14 ✅ as "closed by the #6 bundle (no dedicated fix)". Estimated effort: ~15 min.

If the team prefers strict separation, the alternative is a hardened entry-point contract: in `sandbox_execute_in_workspace`, **refuse to return any result that didn't go through the workspace mount**. That is, wrap the entire body in an assertion that the eventual Docker `docker run` invocation included `-v <workspace>:/workspace`. This is a larger refactor (~30 min) and only worth it if future changes re-introduce a no-mount code path — today there isn't one.

---

## Parent-document updates still pending

The following edits to `security_audit_2026_04_19.md` have NOT been made by this extension file and should land together with a future commit that integrates #12-#14 into the parent audit proper:

1. **Line 1281.** "Eleven findings now, grouped into two tranches" → "Fourteen findings now, grouped into two tranches."
2. **Tranche 1 (lines 1283-1294).** Insert #12 and #13 ahead of #6 (both are P0 and eval-blocking; #12 invalidates `chain_success` telemetry, #13 corrupts the eval scratch dir).
3. **Tranche 2 (lines 1296-1303).** Add #14 as a one-line item: "#14 — confirmation test for the #6 fix (~15 min)."
4. **Behavioral regressions expected (lines 1307-1314).** Add:
   - `CompositionalExecutor.build_execution_order()` returns `Optional[list[int]]` post-#12 (was `list[int]`); `run()` handles `None` as a fail-closed signal.
   - Decomposition validator's sticky flag now flips on invalid `depends_on` / cycle; CLI exit 2 on retry exhaustion.
   - `_save_output` silently skips output dict keys and `output_files` entries that would escape `run_dir`; a warning log is emitted per skip.
5. **Ordering rationale (line 1316-1318).** Extend: "#12 and #13 land at the head of Tranche 1 because they distort `chain_success` reporting and the eval scratch-dir respectively; both must be in before any longitudinal or 400-record run."
6. **End-of-audit note (line 1322).** Append: "Extended 2026-04-20 (fourth extension) with findings #12, #13, #14 (detail sections in `security_audit_2026_04_19_ext4.md`)."

---

**End of extension.**
