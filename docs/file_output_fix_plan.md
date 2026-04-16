# File Output Fix Plan — SKILL/AGENT Last Steps

**Date:** 2026-04-12 (original), **2026-04-16 (pivoted to Option B after thesis-alignment review — see §Resolution at the bottom)**
**Problem:** When the last step of a chain is a SKILL or AGENT, file outputs (CSV, Excel, Markdown) are not produced. The LLM ignores prompt instructions to embed file content in the output dict.
**Discovered:** test_mint 2026-04-12 — T11 requested Excel output but SKILL last step returned plain JSON.
**Resolution:** **Option B landed 2026-04-16.** The first-pass implementation used Option A (executor-level synthesis in `composition.py`) and was reverted mid-session after a proposal re-read surfaced a five-pillar drift. The final fix is Option B: decomposer Rule 15 + `_ensure_file_writer_last_step` validator append a deterministic MCP_TOOL writer as the last subtask when the `OutputContract.required_files` is non-empty and the decomposer didn't already produce one. See §Resolution and master plan §12 finding #10.

---

## Root cause

MCP_TOOL steps run in Docker with the workspace mounted at `/workspace`. They can write files to `/workspace/output.xlsx` and the executor collects them via `_collect_output_files()`.

SKILL/AGENT steps have **no workspace mount**. They receive upstream data via `fixture_contents` injection (JSON string in the LLM message). The prompt tells them to embed file content via `_output_file_content` + `_output_file_name` keys in the output dict, but:
- The LLM has no motivation to produce base64-encoded binary content
- The instruction competes with the main task instruction
- LLM compliance is near-zero in practice

This is a **structural limitation**, not a prompt engineering problem.

---

## Affected tasks

- **T07** — requests CSV output (last step could be MCP_TOOL or SKILL depending on decomposition)
- **T11** — requests Excel output (last step was SKILL in the 2026-04-12 run)
- Any future task requesting non-JSON file output where the decomposer puts a SKILL/AGENT as the last step

---

## Proposed fixes (ranked by reliability)

### Option A: Executor-level post-chain conversion (recommended)

**Approach:** After the chain completes successfully, if the task requested a file output format AND no `output.*` file was collected from the workspace, the executor converts `step_results[-1]` (the final JSON dict) to the requested format mechanically using Python libraries.

```python
# In CompositionalExecutor.run(), after chain success:
if requested_format and not collected_files:
    collected_files = _convert_output_to_file(
        step_results[-1], requested_format
    )
```

**Conversion logic (no LLM):**
- CSV: `pandas.DataFrame(data).to_csv()` or `csv.writer`
- Excel: `openpyxl` workbook from dict/list data
- Markdown: template-based formatting of dict keys

**Pros:**
- 100% reliable — no LLM compliance needed
- Fast — pure Python, no sandbox/Docker
- Deterministic — same JSON always produces same file

**Cons:**
- Needs to know the requested format (must be passed from task prompt or decomposer)
- Conversion logic is generic — won't produce custom formatting (e.g., multi-sheet Excel with charts)

**Effort:** ~1h. Add `requested_output_format` field to decomposer output or detect from task prompt keywords. Implement `_convert_output_to_file()` in `composition.py`.

### Option B: Force MCP_TOOL "file writer" as last step

**Approach:** When the task requests file output, the decomposer adds a final MCP_TOOL step: "Convert the upstream JSON to [format] and write to /workspace/output.[ext]". This step runs in Docker with workspace mount and can write the file.

**Pros:**
- Uses the existing MCP_TOOL workspace write mechanism
- The "file writer" tool is simple enough that LLM generation is reliable

**Cons:**
- Adds an extra step to every file-output chain
- Extra generation + verification overhead (~60-120s)
- Depends on decomposer compliance to add the step

**Effort:** ~30min prompt change + testing. Add a decomposer rule: "If the task requests a file output format, the last subtask must be a tool that writes the file."

### Option C: Post-chain one-shot MCP_TOOL generation

**Approach:** After the chain completes without a file, generate a one-shot MCP_TOOL that takes the final JSON and writes it as the requested format. No decomposer involvement.

**Pros:**
- Doesn't affect the decomposition or chain structure
- Works retroactively on any chain

**Cons:**
- Adds ~60-120s for generation + verification of the conversion tool
- Still depends on LLM generating correct conversion code

**Effort:** ~1h.

---

## Recommendation (original, 2026-04-12 — **superseded 2026-04-16**)

**This section is preserved for historical context.** The original recommendation was Option A (executor-level conversion). That recommendation was **reversed on 2026-04-16** after a proposal-alignment review — see §Resolution below. The original Option-A rationale, preserved verbatim:

> *"Option A (executor-level conversion) is the right fix. It's deterministic, fast, and doesn't depend on any LLM. The final JSON dict has all the data — converting `{"region_revenues": {"North": 19031.32, ...}}` to an Excel sheet with a 'Region' and 'Revenue' column is mechanical."*

The concern with Option A that the original plan weighted too low: **it short-circuits Pillars 1-3 of the thesis proposal**. Executor-level synthesis is not a generated capability — it's not classified (Pillar 1.3), not generated (Pillar 1.4), not verified (Pillar 2), not registered (can't reuse), not eligible for Pillar 3 (split/merge/prune). Installing a parallel hardcoded-format codepath in the executor is the same drift pattern as the Tier 3 white-box/black-box drift in `perf_fixes_2026_04_14.md` §3e.

**Option A was implemented first, then reverted in the same session.** The revert commit is part of the Option B landing commit. Option A's shipped code (synthesis helpers `_find_tabular`, `_synthesize_csv`, `_synthesize_xlsx`, `_synthesize_markdown`, `_synthesize_file_from_dict` + 18 unit tests + executor plumbing) was deleted; `output_contract` was dropped from `CompositionalExecutor.__init__`.

---

## Resolution — Option B landed 2026-04-16

**Decision:** force the decomposer to produce a deterministic file-writer subtask as the LAST step whenever the task requires file output. The writer is classified as MCP_TOOL (file writing is deterministic + stateless per Pillar 1.3) and flows through Pillar 1-3 like any other capability.

### Implementation

**Layer 1 — Decomposition (ensures a writer subtask exists):**

1. **Decomposer prompt Rule 15** (`src/orchestrator/prompts/decomposition.py`):
   > *"File outputs require a dedicated deterministic writer step as the LAST subtask. When the task prompt asks the pipeline to produce a file (e.g., 'write output.csv', 'save to report.md'), the final subtask MUST be a deterministic file-writing step whose description explicitly mentions the filename and format."*
   Includes the pattern for `required_capabilities` (`["write-xlsx-file"]`, `["write-csv-file"]`, etc.) — short names optimized for registry reuse.
2. **Example 8 in the decomposer prompt** showing a canonical three-step decomposition with the writer as step 2.
3. **`_ensure_file_writer_last_step(subtasks, required_files)`** in `src/orchestrator/decomposition.py` — post-decomposition validator that:
   - No-ops when `required_files` is empty (most tasks).
   - No-ops when the last subtask's description mentions any required filename (Rule 15 compliance — happy path).
   - **Appends** a synthetic MCP_TOOL writer subtask when the decomposer didn't produce one (safety net for non-compliance). One writer appended per missing required file; writers chain off each other for multi-file asks.
4. **Wiring in `decomposition_node`** — the validator fires after `_validate_chain_coherence` runs, gated on `state.output_contract.required_files` being non-empty. Logged as `"decomposition.writer_appended"` LangFuse event and recorded in `required_caps`.

**Layer 2 — Classification (ensures the writer is classified as MCP_TOOL):**

Discovered empirically via T16 test_mint run 2026-04-16 (see master plan §12 finding #11). The decomposer correctly added Rule 15 writer subtasks, but the classifier's *"Parent-Task First, Then Step-in-Context"* decision rule biased AGENT-parent tasks to fold writer subtasks into lead+subagents agents. Writers folded into AGENT backends cannot write to the Docker workspace, so files silently failed to materialize.

5. **Classifier prompt file-writer exception** (`src/orchestrator/prompts/classifier.py`) — new section in the Decision Process immediately after the parent-task-first rule. Explicit carve-out: *"A subtask whose job is to serialize upstream data to a file is ALWAYS `mcp_tool`, regardless of the parent task's type."* Signals: filename + format in description, `write-<ext>-file` in `required_capabilities`, `output_file` in `output_schema`.
6. **Example G in the classifier prompt** — agent-task with a file-writer tail correctly classified as AGENT investigator → MCP_TOOL writer (NOT a collapsed lead+subagents agent).
7. **`_enforce_file_writer_mcp_tool(classified_gaps, required_files)`** in `src/orchestrator/classifier.py` — post-classification structural override:
   - No-ops when `required_files` is empty or no gaps look like writers.
   - Identifies writer gaps by the `write-` query prefix (matches Rule 15 naming convention).
   - Overrides `classified_type` from SKILL/AGENT to MCP_TOOL when needed, preserving the original rationale in an audit-trail note.
   - Warnings surfaced in `state.errors` + logged at WARNING level.
8. **Wiring in `classifier_node`** — the override fires at the tail of classification, gated on `state.output_contract.required_files` non-empty.

### Why Option B is proposal-aligned

| Proposal claim | Option B |
|---|---|
| Pillar 1.3: *"Deterministic + stateless → MCP tool"* | ✅ writer step is classified as MCP_TOOL |
| Pillar 1.4: *"code-generation agent produces the artifact"* | ✅ generated by the MCP_TOOL generator |
| Pillar 2: *"every generated capability must pass all three tiers"* | ✅ writer goes through static + behavioral + adversarial |
| Pillar 3: split/merge/prune operate across the full registry | ✅ writers are registered and eligible for all three operations; e.g. `write-csv-file` and `write-xlsx-file` are natural merge candidates |
| Gap-to-capability semantics | ✅ "write X to output.csv" is a gap, matched (registry) or generated (JIT) |

### Format scope — arbitrary, not hardcoded

The writer is generated per-task by the MCP_TOOL generator. Any format the generator can write in Python (CSV, Excel, Markdown, JSON, TXT, PDF via reportlab, DOCX via python-docx, Parquet via pyarrow, …) works automatically — the generator picks the right library. Unsupported formats fail loudly at Tier 2/3 (behavioral test crashes, adversarial probe catches hallucination) rather than silently via an executor fallthrough.

### Cost profile

- **Cold cache (no writer registered):** +60-120s for writer generation + verification. This is one-time per format — subsequent tasks asking for the same format match the registered writer (semantic retrieval) and skip generation entirely.
- **Warm cache (writer already registered):** ~0s overhead — registry match, no LLM calls.
- **Decomposer non-compliance:** the validator's appended subtask still goes through the full pipeline; same 60-120s one-time cost.

Over a longitudinal run, amortized cost trends toward zero as the registry accumulates writers for each format the task distribution requests.

### Test coverage

- **8 unit tests** for `_ensure_file_writer_last_step`: empty-args no-ops, decomposer-compliance no-op, non-compliance append, multi-file chain, case-insensitive match, format-label mapping, unknown-extension fallback.
- **Decomposer prompt Rule 15** exercised by Example 8 (informational; LLM behavior not unit-tested).
- **Integration smoke** via `scripts/test_mint.py` with modified prompts that request file output (pending).

### Chapter 4 disclosure text

> *"File outputs are produced by a deterministic MCP_TOOL writer subtask appended to the chain's final position whenever the task's declared OutputContract requires files. This writer is generated JIT by the MCP_TOOL generator, verified by the three-tier gate, and registered for reuse. A post-decomposition validator (`_ensure_file_writer_last_step`) enforces this structurally as a safety net against decomposer non-compliance on prompt Rule 15. This keeps file production inside the Pillar 1-2-3 pipeline (JIT generation + verification + evolutionary optimization) rather than short-circuiting it with executor-level synthesis — an Option A implementation was reverted after a proposal-alignment review surfaced a five-pillar drift."*

### Interim workaround (now historical)

Before Option B landed, the system gracefully degraded:
- JSON dict output was always produced and correct
- `file_validator` fell back to `validator` (dict-only) when no files were present
- `run.json` captured all metrics regardless of file output
- The eval harness validated dict output — file output was a bonus

With Option B landed, file output is a first-class capability. Tasks that declare `required_files` now produce those files through the standard pipeline.
