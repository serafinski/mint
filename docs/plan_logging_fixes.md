# Logging and Silent Failure Fixes

**Date:** 2026-03-29
**Status:** All issues DONE (2026-03-29).
**Context:** Audit of pipeline for silent failures and misleading log messages. No behavioral bugs — all issues are logging/registration improvements.

---

## Issues

### Issue 1: loading_node skips registration when importlib fails (MEDIUM)

**File:** `src/orchestrator/loading_node.py:61-66`

**Problem:** When `loader.load_tool()` raises (e.g., `No module named 'pandas'`), the tool is skipped entirely — no registration in ChromaDB. The log says "Loading complete: 0 capabilities deployed" which looks like a pipeline failure, but the harness still executes the tool via `state.generated_artifacts` + `sandbox_execute`.

**Impact:** Tools with external dependencies (pandas, numpy, sklearn) are never registered, so they can't be reused by subsequent tasks in the same run. Single-task success is unaffected. Longitudinal optimizer and CLI reuse are broken for these tools.

**Fix:** Register the capability in ChromaDB with `source_code` in metadata even when `importlib` loading fails. Separate "loadable on host" from "registered in registry":

```python
# Current: load fails → skip registration
try:
    loaded = loader.load_tool(artifact)
except Exception:
    errors.append(...)
    continue  # ← skips registration

# Fixed: load fails → still register with source_code
try:
    loaded = loader.load_tool(artifact)
except Exception as exc:
    logger.warning("Host import failed for '%s': %s (registering with source_code only)", ...)
    # Register without loadable module — sandbox can still execute
    try:
        loader.register_tool(artifact, registry)
        registered += 1
    except Exception:
        errors.append(...)
    continue
```

Also change the summary log to distinguish "registered" from "host-loadable":

```python
logger.info(
    "Loading complete: %d registered (%d host-loadable), %d skill/agent on disk, %d errors",
    registered, host_loadable, len(non_mcp_artifacts), new_errors,
)
```

### Issue 2: Verification gate exception = silent pass (MEDIUM)

**File:** `src/verification/gate.py:95-109`

**Problem:** When `_verify_inner()` raises any exception, the gate catches it, logs "Unexpected error in verify()", but returns `overall_passed=True`. Reading the logs, this looks like a failure, but the artifact passes.

**Impact:** Confuses debugging. A user investigating why a bad artifact passed verification sees the error log and assumes it was rejected — but it wasn't.

**Fix:** Change the log level and message to make the fail-open behavior explicit:

```python
except Exception as exc:
    logger.warning(
        "Verification gate error for '%s': %s — PASSING (fail-open policy). "
        "The artifact was NOT verified; it passed by default due to a gate bug.",
        artifact.capability_name, exc,
    )
```

### Issue 3: LLM package resolution loses context on failure (MEDIUM)

**File:** `src/verification/sandbox_tool.py:75-78`

**Problem:** When `_resolve_imports_via_llm()` fails, the warning log doesn't include which imports needed resolution. The fallback to identity mapping means `sklearn` stays as `sklearn` (instead of `scikit-learn`), causing `pip install sklearn` to fail in the sandbox — but the connection between the LLM resolution warning and the pip failure is invisible.

**Impact:** Hard to debug sandbox execution failures. The user sees "pip install failed" without knowing the LLM resolution step failed first.

**Fix:** Include the import list in the warning:

```python
except Exception as exc:
    logger.warning(
        "LLM package resolution failed for imports %s: %s. "
        "Falling back to identity mapping (import name = pip name).",
        non_stdlib_imports, exc,
    )
```

### Issue 4: ChromaDB cache clear silently swallowed (MEDIUM)

**File:** `tests/evaluation/harness.py:118-121, 232`

**Problem:** `SharedSystemClient.clear_system_cache()` failure is caught with `except Exception: pass`. If ChromaDB is misconfigured or has a stale lock, this silently ignores it — potentially causing the next `SkillRegistry` init to use a stale client.

**Impact:** Could hide configuration issues. The restore might appear to work but actually use a cached client pointing to the old data.

**Fix:** Log at DEBUG level:

```python
try:
    chromadb.api.shared_system_client.SharedSystemClient.clear_system_cache()
except Exception as exc:
    logger.debug("ChromaDB cache clear failed (non-critical): %s", exc)
```

### Issue 5: LangFuse handler creation silently swallowed (LOW, 5 locations)

**Files:**
- `src/verification/adversarial.py:283-288`
- `src/verification/behavioral.py:286-288`
- `src/orchestrator/decomposition.py:84-88`
- `tests/evaluation/execution.py:185-189, 291-295`

**Problem:** LangFuse `CallbackHandler()` creation fails with bare `except: pass`. If LangFuse env vars are set but the service is down, no log indicates tracing is disabled for that invocation.

**Impact:** Low — tracing is optional. But makes it hard to tell if traces are missing due to LangFuse being down vs the code not being instrumented.

**Fix:** Log at DEBUG:

```python
except Exception as exc:
    logger.debug("LangFuse handler creation failed (tracing disabled): %s", exc)
```

### Issue 6: Generator retry exhaustion not summarized (LOW)

**Files:**
- `src/orchestrator/generators/mcp_tool.py`
- `src/orchestrator/generators/skill.py`
- `src/orchestrator/generators/agent.py`

**Problem:** When a generator retries (LLM call + repair pass) and gives up, each failure is logged individually, but there's no summary like "Gave up generating 'csv-parser' after 2 attempts (initial + repair)."

**Impact:** Low — each attempt is logged. But in a run with 17 tasks, scrolling through individual warnings to figure out which gaps were abandoned is tedious.

**Fix:** Add a summary log after the generation loop:

```python
if failed_gaps:
    logger.warning(
        "generate_mcp_tool: %d/%d gaps failed generation: %s",
        len(failed_gaps), total_gaps, [g.query for g in failed_gaps],
    )
```

---

## Priority

| Issue | Severity | Effort | Fix after eval? |
|---|---|---|---|
| 1. loading_node registration | MEDIUM | 30 min | Yes — improves reuse |
| 2. Gate fail-open message | MEDIUM | 5 min | Yes — log clarity |
| 3. Package resolution context | MEDIUM | 5 min | Yes — log clarity |
| 4. ChromaDB cache clear | MEDIUM | 5 min | Yes — log clarity |
| 5. LangFuse handler debug logs | LOW | 10 min | Optional |
| 6. Generator retry summary | LOW | 15 min | Optional |

Total: ~1 hour for all, ~45 min for MEDIUM only.
