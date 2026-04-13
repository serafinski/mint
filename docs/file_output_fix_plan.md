# File Output Fix Plan — SKILL/AGENT Last Steps

**Date:** 2026-04-12
**Problem:** When the last step of a chain is a SKILL or AGENT, file outputs (CSV, Excel, Markdown) are not produced. The LLM ignores prompt instructions to embed file content in the output dict.
**Discovered:** test_mint 2026-04-12 — T11 requested Excel output but SKILL last step returned plain JSON.

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

## Recommendation

**Option A (executor-level conversion)** is the right fix. It's deterministic, fast, and doesn't depend on any LLM. The final JSON dict has all the data — converting `{"region_revenues": {"North": 19031.32, ...}}` to an Excel sheet with a "Region" and "Revenue" column is mechanical.

**Implementation plan:**

1. Add `requested_output_format: str | None` to `SubTaskItem`/`SubTask` (or detect from task prompt keywords like "output as CSV", "write Excel")
2. In `CompositionalExecutor.run()`, after collecting output files, if none found and format is requested:
   ```python
   if not collected_files and requested_format:
       collected_files = _convert_dict_to_file(step_results[-1], requested_format)
   ```
3. `_convert_dict_to_file()` handles CSV (csv module), Excel (openpyxl), Markdown (string templates)
4. The converted file goes into `ChainResult.output_files` — same path as workspace-collected files

**What NOT to do:** Don't try to fix this via prompt engineering on the SKILL/AGENT generator. LLM compliance for embedded binary file content is fundamentally unreliable.

---

## Interim workaround

Until this is fixed, the system gracefully degrades:
- JSON dict output is always produced and correct
- `file_validator` falls back to `validator` (dict-only) when no files are present
- `run.json` captures all metrics regardless of file output
- The eval harness validates dict output — file output is a bonus
