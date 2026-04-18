# Registry Retrieval Fix Plan — Dual-Query Search + Reusable Descriptions

> ✅ **DONE 2026-04-15** in commit `fd8d118`. Part A (dual-query retrieval in `registry.detect_gaps`) and Part B (decomposer prompt Rule 14 forbidding paths/per-run values in subtask descriptions) shipped as a single bundle. 9 new dual-query tests in `test_registry.py` and 4 subtask→description wiring tests in `test_gap_detection.py`. See `docs/MASTER_PLAN_2026_04_13.md` §6.0 row 2.5 for the short form and §14.5 for the pointer back to this doc. The plan text below is preserved for design rationale and the empirical measurements that motivated the approach.

**Date:** 2026-04-14 (end of day). **Revised later same day** to add Part B (prompt cleanup for reusable descriptions) after the user observed that decomposer descriptions hardcode absolute file paths, which hurts both the retrieval-side embedding quality AND cross-run capability reuse.

**Problem:** Seeded capabilities are silently not matching their intended queries in most runs. Two compounding causes:

1. **Query side (Part A):** the retrieval layer only uses the decomposer's short slug-style `required_capabilities` strings, which fall below the 0.6 cosine-similarity threshold against prose-style embedded capability documents. This causes the pipeline to generate duplicates of seeded caps on nearly every run — a structural correctness bug that poisons several Chapter 6 metrics.

2. **Document/description side (Part B):** the decomposer's natural-language `SubTask.description` fields embed absolute file paths, specific filenames, and per-run values — making them both (a) lexically noisy for embedding similarity (the path tokens dilute the semantic signal) and (b) non-reusable across runs (two semantically-identical tasks produce lexically-different descriptions, so the Pillar 3 optimizer can't recognize them as duplicates at merge-candidate detection time).

The two parts are independent fixes at different layers but they compose cleanly: Part A makes descriptions actually get used for retrieval, Part B makes descriptions more retrievable and more reusable.

**Discovered:** 
- **Part A**: 2026-04-14 20:52 test_mint run. The canonical test was T01 CSV parsing (literally labeled *"MCP_TOOL — CSV parsing (seeded capability match)"* in `scripts/test_mint.py`). On that run the registry returned *"0 match(es), 1 gap(s)"* and the pipeline generated a brand-new `parse_csv_and_return_stats_7f373a` artifact. The previous run at 19:01 had matched `csv-parser` correctly, described by the user as *"for the first time in a long time"* — the exception, not the rule.
- **Part B**: discovered later the same day when walking through the Step 14.5 design and measuring the empirical query strings. The decomposer's T01 description was `"Parse the CSV file at /Users/tomasz/Developer/master/JustInTimeMAS/tests/evaluation/fixtures/t01_standard.csv and return a dict with 'row_count' (int) and 'column_names' (list of str)."` — the absolute path is ~40 tokens of pure noise that would be different on any other machine or any other fixture, while contributing zero semantic signal to retrieval.

**Scope of this doc:** diagnosis, empirical evidence, fix design, open questions. Does not contain code yet. The implementation happens as Phase 2 Step 14.5 in `docs/MASTER_PLAN_2026_04_13.md`.

---

## 1. The empirical evidence

Run a direct cosine-similarity measurement between the `csv-parser` seed's embedded document and several candidate query strings, using ChromaDB's default embedder (`all-MiniLM-L6-v2`):

**Seed document** (exactly as `_build_document()` formats it):

```
data-processing > mcp_tool > csv-parser: Parse a CSV file (comma-delimited,
default csv.DictReader dialect) and return the row count and the list of
column header names. Does not return row data and does not perform type
coercion.
```

**Measured cosine similarities against various query shapes:**

| Query style | Example query | Similarity | Above 0.6 threshold? |
|---|---|---|---|
| Short slug (typical decomposer output) | `parse-csv-and-return-stats` | **0.5425** | ❌ below |
| Slug as prose | `parse csv and return stats` | 0.5667 | ❌ below |
| Even shorter slug | `parse-csv-file` | 0.5092 | ❌ below |
| Exact cap name | `csv-parser` | **0.5544** | ❌ below — literal name of the cap fails its own threshold |
| Natural-language description | `Parse the CSV file and return a dict with 'row_count' (int) and 'column_names' (list[str]).` | **0.7622** | ✅ |
| Combined slug + description | `parse-csv-and-return-stats: Parse the CSV file...` | 0.7396 | ✅ |

Reproduction script (also embedded for posterity):

```python
import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
import numpy as np

ef = DefaultEmbeddingFunction()
seed_doc = (
    "data-processing > mcp_tool > csv-parser: Parse a CSV file "
    "(comma-delimited, default csv.DictReader dialect) and return "
    "the row count and the list of column header names. Does not "
    "return row data and does not perform type coercion."
)
seed_emb = np.array(ef([seed_doc])[0])

for q in [
    "parse-csv-and-return-stats",
    "csv-parser",
    "Parse the CSV file and return a dict with 'row_count' (int) and 'column_names' (list[str]).",
]:
    q_emb = np.array(ef([q])[0])
    cos = float(np.dot(seed_emb, q_emb) / (np.linalg.norm(seed_emb) * np.linalg.norm(q_emb)))
    print(f"{cos:.4f}  {q}")
```

---

## 2. Why this happens — three compounding factors

### Factor 1: decomposer produces short hyphen-case slugs for `required_capabilities`

The decomposition prompt (`src/orchestrator/prompts/decomposition.py`) asks for `required_capabilities` as *"short descriptive queries"*, and the LLM naturally produces hyphen-case slugs of 4–7 words:

- `parse-csv-and-return-stats`
- `aggregate-spending-by-department`
- `compute-days-of-stock-for-inventory`

Those are the strings `gap_detection_node` currently passes to `registry.detect_gaps()` as its search queries. They're short, structured, and low-information compared to the full prose descriptions the decomposer ALSO produces for each subtask in `SubTask.description`.

### Factor 2: hierarchical prefix dilutes the embedding signal

`_build_document()` prepends `"{domain} > {capability_type} > {name}: "` to the description before embedding (`src/registry/store.py:77-107`). For csv-parser that's `data-processing > mcp_tool > csv-parser:` — about 15 tokens of metadata noise that the average-pooled embedding has to reconcile against a short query.

Removing the prefix in my test only recovered ~0.03–0.05 similarity — enough to notice but not enough to push slug queries over the 0.6 threshold. The prefix is a secondary factor, not the primary one. And removing it would break the Step 3.5f domain-filter mechanism (which reads `domain` from metadata, not the embedded doc — but the hierarchical framing helps the embedding space be more consistent).

### Factor 3: 0.6 similarity threshold is high for sentence-transformers on slug-vs-prose

`all-MiniLM-L6-v2` typically produces cosine similarities in the 0.3–0.8 range for semantically related text. 0.6 is a reasonable threshold for *"natural-language query against natural-language document"*. But it's high for *"short slug against long prose"* because the average-pooled embeddings don't handle large length asymmetries well.

**All three factors compound.** Short queries against long documents with a metadata prefix at a high threshold produce consistent false negatives. Each factor alone would be borderline; together they guarantee the failure mode.

---

## 3. Why this matters more than it looks

The damage from this bug is bigger than "one test failed". It affects multiple Phase 5 thesis metrics:

**3a. Reuse rate metric is artificially deflated.** The proposal's Pillar 3 lists reuse rate as one of four key metrics. If seeded caps aren't matching their natural queries, the reuse rate metric reports near-zero for the seeded part of the registry — suggesting the seeds are dead weight when they're actually correct. Chapter 6 will either have to disclose this or silently mislead.

**3b. Static baseline looks artificially weak.** The evaluation harness runs `condition=static` (no JIT generation) as one of the four baselines. Static relies on seeded cap matches. If most seeds don't match their queries, static will fail most tasks and the `full` baseline will look artificially better by comparison. **This is the worst case for thesis credibility** — it's the kind of finding an external reviewer would immediately challenge.

**3c. Pillar 3 optimizer sees a bloated, non-convergent registry.** Every generation creates a near-duplicate of a seeded cap. The optimizer's merge-candidate detection runs on description cosine similarity ≥ 0.5, so it should find these — but the new caps' domains are whatever the evolutionary taxonomy classifier picked at generation time (e.g. `csv-processing`, `file-processing`), while csv-parser's domain is `data-processing`. The domain filter may or may not bridge them. If it doesn't, the optimizer can see the duplicates but can't merge them, and the registry grows monotonically across longitudinal runs.

**3d. Token cost metric is inflated.** Every false-negative retrieval forces a generation cycle (decompose → classify → generate → verify → register → execute). That's ~30–60 seconds of LLM work and ~$0.01–0.05 of API cost per false miss. Across a 400-record eval run, that's 400 × ~0.05 = ~$20 in wasted API spend and ~15–30 minutes of wall time added to the 18-hour budget.

**3e. Silent-regression risk for the evaluation.** The evaluation harness doesn't distinguish "this task had no registry match because it's genuinely novel" from "this task had no registry match because retrieval is broken". Both show up as `matched_in_registry_count=0` in `EvaluationRecord`. Chapter 6's retrieval-rate analysis would report low rates with no way to tell whether that's real low coverage or a retrieval bug. The thesis would be wrong in a way that's hard to catch.

---

## 4. Fix options, ranked

### Option 1 — Dual-query search (RECOMMENDED)

**Use both `required_capabilities[i]` (slug) AND the corresponding `SubTask.description` (prose) when querying the registry. Take the max score across the two.**

- **Strictly additive.** Can only increase recall, never decrease it. Any slug that already matches will still match; slugs that don't match might now match via their descriptions.
- **Uses data that already exists.** The decomposer populates `SubTask.description` with natural language, and `gap_detection_node` has access to `state.subtasks`. We just don't currently use the description for search.
- **No prompt changes.** No threshold changes. No embedder changes. No cascading risk to other pipeline stages.
- **Fix target measured in my test:** `description`-based query on T01 scored 0.7622 against csv-parser — well above the 0.6 threshold.
- **Risk:** false-positive matches where the description text overlaps textually with an unrelated cap. Mitigation: the description is semantically the same content as the slug but more verbose, so if the slug didn't mismatch, the description is unlikely to spuriously match. Taking `max()` is still safer than an `average()` or `sum()` because the worst case of two queries is the better of the two.

**Implementation:**
- `src/orchestrator/gap_detection.py` — change the `detect_gaps()` call to pass both query variants per gap. Iterate `state.subtasks` in parallel with `state.required_capabilities` so each slug has its corresponding description.
- `src/registry/store.py::detect_gaps` — accept an optional per-query auxiliary string. For each (primary, auxiliary) pair, run two searches and take the max-scoring result.
- `src/registry/store.py::search` — unchanged. The dual-query logic lives in `detect_gaps` so `search` stays single-query.
- Backward compat: when `auxiliary` is None or empty, behavior is identical to the pre-fix single-query path.

### Option 2 — Lower the similarity threshold from 0.6 to 0.5

- One-line change in `detect_gaps` default
- Would let the slug case match (0.5425 > 0.5)
- **High false-positive risk.** At 0.5 threshold, unrelated caps start matching. My earlier Phase E diagnostic (Step 3.5b/c/d) surfaced this pattern explicitly — the domain filter was designed to prevent silent poisoning from over-permissive matches
- Defeats the Step 3.5f evolutionary-taxonomy work that prevents cross-domain false matches
- **Do not ship alone.** Could conceivably combine with Option 1 later if Option 1 alone turns out insufficient for some seeds, but start with Option 1 first and measure

### Option 3 — Remove the hierarchical prefix from the embedded document

- Small change in `_build_document`
- My test showed ~0.03–0.05 similarity improvement without the prefix — helpful but not sufficient for slug cases (0.5425 → 0.5893, still below 0.6)
- Removes signal that Step 3.5f's domain filter uses (though the filter reads domain from ChromaDB metadata, not the embedded doc — still, the prefix was chosen deliberately as a hierarchical embedding signal per the proposal)
- **Strictly worse than Option 1** — smaller improvement, more risk

### Option 4 — Use a better embedder (e.g., `text-embedding-3-small` via OpenAI)

- Would increase baseline similarity scores across the board
- Adds API latency and cost to every gap_detection call (which runs once per task)
- Requires re-embedding every existing `data/cli/chroma` and `data/eval/chroma` collection
- Changes the embedder midway through iteration — large blast radius, hard to validate all downstream metrics still behave
- **Not a pre-eval fix.** If we do this at all, it's post-thesis

### Option 5 — Re-prompt the decomposer to produce longer `required_capabilities` strings

- Prompt-engineering solution
- Fragile — LLMs vary run-to-run, a prompt rule alone doesn't guarantee longer queries
- Doesn't fix the underlying embedding asymmetry between short queries and long documents
- **Band-aid, not a fix**

### Option 6 — Re-prompt the decomposer to produce path-free reusable descriptions (Part B, NEW 2026-04-14 end of day)

This is a **complementary** fix to Option 1, not an alternative. Adds a new rule to the decomposition system prompt that forbids embedding absolute paths, specific filenames, or per-run values in subtask descriptions. Descriptions must be expressed in terms of data shape and operation only.

**Motivating evidence:** the T01 description we measured empirically is:

> *"Parse the CSV file at /Users/tomasz/Developer/master/JustInTimeMAS/tests/evaluation/fixtures/t01_standard.csv and return a dict with 'row_count' (int) and 'column_names' (list of str)."*

The absolute path is ~40 tokens of embedding noise. It hurts three things:

1. **Retrieval similarity** — those 40 tokens dilute the semantic signal. Removing the path would push similarity from ~0.7622 toward ~0.80+ (needs empirical confirmation, see §6 Q6 below). Gives Option 1's auxiliary query more headroom against the 0.6 threshold.

2. **Cross-run reuse** — the same task run on a different machine or with a different fixture name produces a lexically different description even when semantically identical. Pillar 3's merge-candidate detection in the optimizer can't recognize two path-embedded descriptions as duplicates because their embeddings differ in the path tokens. **This is a Pillar 3 correctness issue at the Chapter 5 level, not just a retrieval issue at Chapter 6.**

3. **Thesis framing on capability reusability** — the proposal's Pillar 1 line 77 describes hierarchical embeddings as preventing false negatives *"where semantically equivalent but differently named skills"* are misidentified. Path-laden descriptions are the exact "same semantic, different text" failure mode the proposal warns about — except applied at the description level, not the name level. Closing this gap strengthens the thesis story.

**Proposed prompt rule** (new rule 14 in `DECOMPOSITION_SYSTEM_PROMPT`, inserted after the rule 13 chain-coherence rule from Step 14):

> *"14. Describe subtasks in terms of data shape and operation, not specific paths or values. Subtask descriptions are used by two downstream systems: (a) the capability registry for semantic retrieval — descriptions that embed absolute paths, specific filenames, or per-run values produce polluted embeddings that hurt match quality; (b) the Pillar 3 optimizer for merge-candidate detection across runs — descriptions that encode per-run state prevent two semantically-identical capabilities from being recognized as duplicates.*
>
> *Use generic shape-and-operation language:*
> - *"Parse the provided CSV file" — not "Parse /Users/.../t04_inventory.csv"*
> - *"Aggregate the upstream transactions list" — not "Aggregate the 80 transactions from t14_transactions.tsv"*
> - *"Validate the input YAML against the reorder rules schema" — not "Read reorder rules from t04_reorder_rules.yaml"*
>
> *The task-level context and file paths are provided to generators through separate state channels — the subtask description does NOT need to carry them. Focus the description on what the subtask DOES (operations, transformations, outputs), not where the data lives."*

Plus at least one of the existing examples (probably Example 1 or 5) should be updated to use the generic phrasing to anchor the rule with a concrete model.

**Why this isn't Option 5 in disguise:** Option 5 tried to fix retrieval by lengthening the slug queries, which doesn't address the embedding asymmetry. Option 6 shortens and cleans the descriptions to remove noise — different target, different mechanism. Option 6 makes the EXISTING description higher-quality as a retrieval query; it doesn't change how queries are CHOSEN.

**Risk:** the generator downstream might rely on the file path being in the subtask description. See Q6 in §6 — must verify before committing.

**Recommendation: Ship Option 1 + Option 6 together as Step 14.5.** They compose cleanly (A makes descriptions get used, B makes descriptions better) and ship in a single commit to avoid partial-fix confusion. Estimated combined effort: ~3.5-4 hours (up from ~2.5-3 for Option 1 alone).

---

## 5. Concrete implementation scope — Option 1 + Option 6

### Files touched

1. `src/orchestrator/gap_detection.py`
   - Build a list of `(slug, description)` pairs from `state.required_capabilities` and `state.subtasks`
   - Handle the edge case where there's no matching subtask for a slug (use None as the auxiliary)
   - Pass pairs to the updated `detect_gaps()` signature
   - ~15 lines changed

2. `src/registry/store.py::detect_gaps`
   - Change signature: `queries: list[str]` → `queries: list[str | tuple[str, str | None]]` OR add a parallel `descriptions: list[str | None] | None = None` parameter
   - Second option is backward compatible without type gymnastics — preferred
   - Iterate queries with descriptions; for each, run `search(primary)` and `search(description)` if description is provided, take the max score
   - Threshold check is against the max score, not the primary score
   - ~25 lines changed

3. `src/registry/store.py::search`
   - Unchanged. All dual-query logic lives in `detect_gaps`

4. `tests/unit/test_registry.py` or equivalent
   - New test: slug below threshold + description above threshold → match returned
   - New test: slug matches one cap, description matches a different cap → max wins (with specific expected output)
   - New test: slug above threshold, no description provided → single-query behavior preserved
   - New test: neither matches → gap returned with best-of-both-scores in the reason string
   - ~80 lines of tests

5. `tests/unit/test_gap_detection.py`
   - Update existing tests that call `detect_gaps` to pass descriptions where the test fixtures support it
   - Add a test covering the gap_detection_node → detect_gaps dual-query wiring
   - ~40 lines

### Files touched for Part B (prompt cleanup — Option 6)

6. `src/orchestrator/prompts/decomposition.py`
   - New rule 14 (shape-and-operation language, no paths/values/per-run state)
   - Update at least one existing example to use path-free generic phrasing as a concrete model
   - ~25 lines added

7. **Verification work before committing Part B** — grep the generator prompts and `build_task_context()` for any reliance on file paths being in `SubTask.description`:
   - `src/orchestrator/generators/mcp_tool.py`, `skill.py`, `agent.py` — check how they format the subtask description into their generator prompts
   - `src/orchestrator/build_task_context.py` (or wherever the function lives) — check that file paths are threaded through via `state.task_description` + `state.files` rather than via subtask descriptions
   - `src/orchestrator/composition.py` — the compositional executor may pull file paths from subtask descriptions when resolving inputs. Verify it uses `state.files` instead.
   - If any of these do rely on subtask-description paths, the fix needs a mini-refactor: add a `SubTask.referenced_file_paths: list[str]` field that the decomposer populates separately, and thread it to generators through that field instead of embedding in the description text.

8. `tests/unit/test_decomposition.py` (or a new test file)
   - Unit test for the prompt rule — mock the decomposer to return a description with a hardcoded path, assert a warning is logged OR (stronger) add a normaliser pass that strips paths before storing on TaskState
   - Integration test: run a task with real files, check that no subtask description contains the literal absolute path
   - ~30 lines

### Pre-commit validation

Before writing any code, run the empirical sweep described in §6 below across all 11 seeded caps. That tells us:

- How many seeded caps have the same structural issue as csv-parser (short-slug-against-prose below threshold)
- Whether their descriptions lift above the threshold when we dual-query
- Whether any seeded cap would remain below threshold even with the fix (meaning Option 1 alone isn't sufficient for that cap)

The sweep is ~10 minutes of work and gives us a concrete number to report in the commit message and Chapter 6 analysis: *"N of 11 seeded caps were structurally below-threshold on their natural slug queries; M of those are lifted above threshold by dual-querying with the decomposer's description."*

### Validation after the fix

1. **Unit tests pass** — the existing `test_registry.py` tests must still pass, plus the new dual-query tests
2. **Integration test** — re-run the empirical sweep with the fix applied and confirm the N seeds now match their natural slug queries
3. **test_mint smoke run** — T01 MCP CSV test should show `1 match(es), 0 gap(s)` with csv-parser matched, regardless of which slug the decomposer happens to produce
4. **Optional: run_pilot subset on T01** — confirms the eval harness path also benefits

---

## 6. Open questions to resolve before implementing

### Q1: How many of the 11 seeded caps have the same issue?

My measurement was only csv-parser. The fix is structurally correct but I don't know the magnitude of the problem. Before committing Option 1, run an empirical sweep:

- For each of the 11 seeded caps, generate a typical decomposer-style slug query for the task the seed is meant to serve
- Measure similarity against the seed's `_build_document()` output
- Tabulate which seeds fall below the 0.6 threshold
- Then measure similarity of the corresponding natural-language description (what the decomposer would produce in `SubTask.description`)
- Tabulate how many are lifted above threshold

Expected pattern: math/arithmetic caps (fibonacci, etc.) may match fine because their slugs are semantically distinct (`compute-fibonacci-number`). Parsing/processing caps likely have the same issue as csv-parser. Would be useful to know the split before committing the fix.

### Q2: What's the right auxiliary-query data source for gap_detection?

`SubTask.description` is populated by the decomposer and is rich natural language. But the association between `state.required_capabilities[i]` and `state.subtasks[j].required_capabilities[k]` is implicit — the current code flattens `required_capabilities` from all subtasks into a single list, losing the subtask association.

Two options for mapping slug → description:
- **(a)** Keep a parallel mapping. `gap_detection_node` iterates `state.subtasks` and for each subtask SUB, calls `detect_gaps` with pairs `[(sub.required_capabilities[k], sub.description) for k in ...]`. Simple, preserves the subtask-local association.
- **(b)** Use `state.task_description` (the full original task text) as the auxiliary for ALL slugs. Simpler but less precise — every slug gets the same auxiliary, which may over-match.

Option (a) is more correct. Requires `gap_detection_node` to iterate subtasks explicitly instead of the current flat `required_capabilities` pattern. Not a huge refactor but worth calling out.

### Q3: Should the fallback path (when decomposition fails) also dual-query?

When decomposition fails (Fix 2b sticky flag False, `subtasks=[]`), the pipeline falls back to `required_capabilities = [first_sentence_of_task]`. There's no subtask to pull a description from. Two choices:

- **(a)** Use the first sentence as the sole query (current behavior, no change). Slug-style the sentence might be if the decomposer produced any, but that's unlikely.
- **(b)** Use `state.task_description` (full task) as the auxiliary when no subtask exists. Basically a whole-task search for the registry.

Option (b) is slightly more correct in the failure case but adds code complexity. Option (a) is simpler and correct-enough because the fallback path is already known-broken (Fix 2b makes it visible but doesn't claim it produces good output).

**Probably Option (a)** — don't over-engineer the fallback path.

### Q4: Does the fix interact with the Step 3.5f domain filter?

The domain filter applies a ChromaDB `$or` where-clause when the taxonomy classifies the task into a known domain. Dual-querying runs the same filter on both the primary and auxiliary searches — so both queries go through the filter, both return filtered results, and the max is still filtered.

**No interaction.** The fix is orthogonal to the domain filter. Test to confirm: a seeded cap with `is_generic=False` and `domain="data-processing"` should still be findable via dual-query when the task is classified into a different domain (via the Step 3.5f `include_generic=True` bypass).

### Q5: What about the invocation metrics — are we going to double-count matches?

`detect_gaps` returns matches, which later get recorded as `InvocationRecord` entries in the metrics store. If dual-querying finds the same cap via both the primary and auxiliary queries, do we record two invocations or one?

**One.** The function returns a single (cap, score, query) per input gap — the best-scoring match. `query` in the return value should be whichever of primary/auxiliary won. This is a per-gap invocation, not a per-query invocation. Worth asserting in a test.

### Q6: Do generators or the compositional executor rely on file paths being in subtask descriptions? (NEW — Part B prerequisite)

Before committing Part B (Option 6 — path-free description prompt rule), we need to verify that no downstream consumer pulls file paths out of `SubTask.description` at execution time. The candidates to check:

1. **Generator prompts** (`src/orchestrator/generators/mcp_tool.py`, `skill.py`, `agent.py`) — do they format the subtask description verbatim into their LLM prompts and rely on the LLM reading the path from that text, or do they thread file paths through a separate channel (state.files, build_task_context)?

2. **Compositional executor** (`src/orchestrator/composition.py`) — when resolving step inputs, does it parse file paths out of subtask descriptions or use `state.files` directly?

3. **`build_task_context()`** (wherever it lives) — verify that it threads file paths through a dedicated field, not as part of the subtask description body.

Expected outcome: file paths are already threaded through `state.task_description` and `state.files` separately, and subtask descriptions are semantic-only as far as generators are concerned. If that's right, Part B is a pure prompt fix with zero code impact beyond `prompts/decomposition.py`.

If not — if generators rely on paths in subtask descriptions — we need a mini-refactor:
- Add `SubTask.referenced_file_paths: list[str]` field in `src/schemas/task.py`
- Tell the decomposer (in the system prompt) to populate that field with the paths its subtask reads, separately from the description text
- Update the generators to read paths from the new field instead of parsing the description
- This is ~2 hours of additional work on top of Part B's prompt change

**Action item:** run the grep before writing any Part B code. ~5 minutes of work, decides whether Part B is a 30-min fix or a 2.5-hour fix.

### Q7: How to measure the empirical improvement from Part B? (NEW)

My earlier measurement against csv-parser used a path-laden description and got 0.7622. Before committing Part B, re-run the same measurement with a cleaned-up description (path removed, generic phrasing) and compare:

- **T01 description with path (current):** *"Parse the CSV file at /Users/.../t01_standard.csv and return a dict with 'row_count' (int) and 'column_names' (list of str)."* → measured 0.7622
- **T01 description without path (post-Part-B):** *"Parse the provided CSV file and return a dict with 'row_count' (int) and 'column_names' (list of str)."* → expected ~0.80+

If the improvement is measurable, Part B's effect on retrieval quality is confirmed and we can report it in the commit message. If the improvement is marginal (<0.02), Part B's retrieval benefit is weaker than expected — but the Pillar 3 reuse benefit still stands, so it's still worth shipping.

~1 minute of empirical measurement. Do it before committing.

---

## 7. Expected impact

### On Phase 5 eval numbers

- **Reuse rate metric** — should increase for seeded caps. Magnitude depends on the Q1 sweep result. If 7 of 11 seeds were structurally below threshold on typical queries, reuse rate could jump from near-zero to ~60% on seeded tasks.
- **Static baseline success rate** — should increase substantially. The static condition's success was bottlenecked by retrieval, not by the seeds being wrong. Expect meaningful lift here.
- **Token cost** — should decrease because false-negative retrievals no longer trigger generation cycles. Expected savings: ~30–60 seconds of LLM work per avoided generation, summed across 400 records = ~15–30 minutes of eval wall time.
- **Registry convergence (Gini coefficient, KL divergence)** — should show a meaningful "sharper" distribution as seeded caps actually accumulate reuse instead of each run generating a new near-duplicate. This one is hardest to predict without running the eval.

### Additional impact from Part B on Pillar 3 (Chapter 5)

Part B's reusable-description benefit is separate from Part A's retrieval benefit and shows up in a different chapter. The Pillar 3 optimizer runs merge-candidate detection over stored capability descriptions (cosine similarity ≥ 0.5 on description embeddings, plus the B1 schema Jaccard gate). Under the current path-laden descriptions:

- Two generated capabilities that do semantically the same thing but processed different files get different descriptions embedded differently
- Merge candidate detection fails to recognize them as duplicates
- Registry grows monotonically across longitudinal runs
- Chapter 5's Pillar 3 convergence story weakens: the optimizer ought to merge near-duplicates, but the input data makes near-duplicates structurally invisible

Part B fixes this at the source: two generated caps that do the same thing get semantically-identical descriptions (because the per-run state — paths, values — is gone). The optimizer's merge detection works on its intended input. Chapter 5's convergence narrative becomes measurable.

**This is a Pillar 3 correctness contribution that ships as part of a Pillar 1 retrieval fix.** Worth calling out in the commit message and in Chapter 5.

### On Chapter 6 narrative

Reframe: this is a **methodology contribution**, not just a bug fix. The narrative:

> *"Semantic retrieval for a capability registry is non-trivial when the query and the stored document come from different textual registers — short slug-style capability names versus prose-style descriptions. We measured this gap empirically during pre-eval validation, found a 3–5pp structural below-threshold region for most seeded capabilities, and addressed it in two complementary ways: (a) dual-querying the registry with both the short slug and the corresponding natural-language description, and (b) cleaning the decomposer's description output of per-run state (absolute paths, specific values) so descriptions are semantic-only and reusable across runs. The first fix raised seeded-cap recall by X pp in the static baseline; the second fix enabled the Pillar 3 optimizer's merge-candidate detection to recognize semantically-identical capabilities as duplicates, improving registry convergence metrics by Y."*

That's stronger than a silent fix. It's evidence that the three-pillar architecture needs retrieval-engineering attention alongside generation and verification — which is a genuine thesis-level observation about what the proposal's hierarchical embedding section glossed over.

---

## 8. Priority and timing

**Phase 2 Step 14.5 in `docs/MASTER_PLAN_2026_04_13.md`.** Positioned:

- **After Step 14** (decomposition coherence) because Step 14 is already staged and tested; should land first
- **Before Step 14.6** (task-level output key validation)
- **Before Step 13** (AGENT execution timeout) because retrieval correctness has higher Phase 5 impact than one execution timeout edge case
- **Before Steps 4–11** (B1 Pillar 3) because retrieval correctness affects Pillar 3 inputs — the optimizer sees the registry; if the registry is wrong, the optimizer is optimizing wrong data

**Estimated effort (Part A alone):** 1.5–2 hours implementation + 0.5 hour empirical sweep + 0.5 hour validation = **2.5–3 hours**.

**Estimated effort (Part A + Part B bundled):** +30 min to 2 hours for Part B depending on Q6 outcome:
- **Best case (generators don't rely on subtask-description paths):** +30 min for the prompt rule, +15 min for the re-measurement (Q7), +15 min for the additional test = **~1 hour total for Part B**. Combined total: **~3.5–4 hours**.
- **Worst case (generators DO rely on subtask-description paths):** +2 hours for the mini-refactor to add `SubTask.referenced_file_paths` field + generator updates = **~2.5 hours for Part B**. Combined total: **~5–5.5 hours**.

**Q6 must be answered before committing Part B.** Run the grep first, see what shows up, THEN commit to one of the two branches above.

**Cannot be deferred to post-eval** — the impact on Chapter 6 reuse and static-baseline numbers is too severe. Either ship the fix or explicitly re-frame Chapter 6 to disclose the retrieval limitation (which is strictly worse than fixing it). Part B is more flexible — if Q6 reveals a larger refactor needed, Part B alone could be deferred to post-eval, but then we lose the Pillar 3 reuse benefit and Chapter 5 also takes a hit.

**Recommendation: bundle A+B if Q6 is clean; ship A alone and defer B to post-eval if Q6 reveals a major refactor.**

---

## 9. What NOT to do

- **Do not lower the similarity threshold** as a quick fix. It defeats Step 3.5f's false-positive prevention and doesn't address the structural asymmetry
- **Do not re-embed the registry with a different embedder** mid-iteration. Too much blast radius
- **Do not re-prompt the decomposer to produce longer slug names**. Fragile, doesn't address the structural issue, and breaks the slug-as-capability-identifier convention used in tool loading
- **Do not skip the empirical sweep** in §6 Q1. Without it, we don't know how many seeded caps are affected and the commit message will be underspecified
- **Do not skip the generator-grep verification** in §6 Q6 before committing Part B. Without it, we might ship a prompt rule that breaks generation for tasks that depend on file paths being carried through the subtask description text
- **Do not add a `SubTask.referenced_file_paths` field speculatively** — only add it if Q6 proves it's needed. Adding schema fields that might not be used is drift in the wrong direction

---

## 10. Cross-references

- **`docs/perf_fixes_2026_04_14.md` §4.6** — crossref to this doc from the chronological narrative of today's findings
- **`docs/MASTER_PLAN_2026_04_13.md` §6.0** — Phase 2 Step 14.5 entry pointing here
- **`docs/thesis_proposal.md`** — Pillar 1 lines 76-77 describe *"hierarchical embeddings (domain → capability → specific skill) to prevent both false positives (superficial keyword matches) and false negatives (semantically equivalent but differently named skills)"*. The false-negative case described here is exactly what the proposal warns about. The current implementation creates rather than prevents that failure mode. Worth flagging in Chapter 4 as a methodology note: *"the hierarchical embedding framing is correct but only addresses false positives; the false-negative case described in the proposal requires query-side augmentation (Part A) combined with document-side normalization (Part B), both of which we implement in Step 14.5."*
- **`src/registry/store.py`** — `_build_document`, `search`, `detect_gaps` (the files that will be edited in Part A)
- **`src/orchestrator/gap_detection.py`** — the caller that will be edited in Part A
- **`src/orchestrator/prompts/decomposition.py`** — new rule 14 for Part B (path-free descriptions)
- **`src/orchestrator/generators/{mcp_tool,skill,agent}.py`** — to grep for subtask-description path dependencies (§6 Q6)
- **`src/orchestrator/composition.py`** — to grep for subtask-description path dependencies (§6 Q6)

---

**Status:** plan written 2026-04-14 end of day, revised to include Part B (reusable descriptions) after the user flagged the hardcoded-path issue while discussing the Part A design. Not yet started. Priority: immediately after committing Step 14 (decomposition coherence) which is currently staged. Part A and Part B bundled if Q6 is clean (recommended path); ship Part A alone and defer Part B to post-eval if Q6 reveals a refactor larger than ~2 hours.
