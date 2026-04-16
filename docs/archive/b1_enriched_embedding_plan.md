# B1 — Schema-Aware Merge Implementation Plan

**Date:** 2026-04-13 (pivoted from embedding-enrichment variant same day)
**Status:** Implementation pending. Seeds + schema fields done; AST extractor, loading-node wiring, migration, Jaccard check pending.

**Purpose:** Make Pillar 3 Merge honor the second conjunct of its proposal criterion — *"high similarity (measured by embedding distance of their descriptions **and** overlapping input schemas)"* — which has been latent since Iteration 4 because the generator never populated `input_schema`/`output_schema` on generated capabilities and the candidate selector in `loop.py` never checked schema overlap.

**Scope:** Pillar 3 Merge refinement + schema population at registration time. No changes to generation, classification, verification, gap detection, or the embedding format.

---

## TL;DR — What changed from the original plan

This document started life as an **embedding enrichment plan**: add I/O schema as a new tier in the ChromaDB embedding document so that Pillar 1 gap detection and Pillar 3 Merge both transparently benefit via `registry.search()`. The original plan is preserved in full below as historical design exploration (see "Historical: embedding enrichment plan").

**Two things happened on 2026-04-13 that caused the pivot:**

1. **Embedding enrichment measurably hurt recall.** I tried three document format variants (multi-line labeled, single-line with `||` delimiter, natural-prose `Parameters:`/`Returns:`) and all regressed Phase A recall from 13/16 (81%) to 11-12/16 (69-75%). Root cause is fundamental to single-document embedding: any added tokens dilute the prose signal for borderline-threshold probes, even when the added tokens are semantically aligned with the query. The two probes most affected were `autonomous-exploratory-data-analysis-on-dataset` (0.615 → 0.492 under `:type` format, 0.583 under `Parameters:` format) and `fibonacci-sequence-value-at-index-n` (0.608 → 0.580).

2. **A missing proposal conjunct was found in `src/optimizer/loop.py`.** When the pivot turned to "store schemas in metadata but don't embed them", I went to verify how Pillar 3 Merge would consume the metadata-stored schemas — and discovered that **merge candidate selection already operates on description-embedding only, ignoring the proposal's "overlapping input schemas" clause entirely.** `identify_candidates_node` calls `registry.search(cap.description)` and filters on cosine ≥ 0.5, but nowhere does it look at `capability.input_schema` or `capability.output_schema`. The proposal's criterion has two conjuncts joined by "AND", and only the first is implemented. This drift was missed by `docs/proposal_vs_implementation_v2.md`, which currently marks Merge as ✅ Faithful.

**Net effect on the plan:**
- The embedding never needed to be enriched to serve Merge — Merge just needed the metadata schemas and a Jaccard helper to consume them directly
- The original plan's "the existing merge code will transparently benefit" was resting on a broken substrate
- The pivot is **strictly less invasive** (zero embedding change, no recall regression) and **strictly more proposal-aligned** (fixes a latent drift rather than adding new machinery)

---

## Iter-4 optimizer gaps discovered on 2026-04-13 (additional fixes now bundled into B1)

While reading `src/optimizer/loop.py` and `operations.py` to verify how Merge would consume the new schemas, three pre-existing Iteration-4 gaps surfaced that are not about B1 per se but **block the Phase 5 eval** if left unfixed. All three are now bundled into the B1 implementation session because they share a code surface and a proposal-alignment theme.

### Gap 1: Merge candidate selection missing the "overlapping input schemas" conjunct

**Symptom:** `loop.py` `identify_candidates_node` calls `registry.search(cap.description)` and filters on cosine ≥ 0.5. It never inspects `input_schema` or `output_schema`. The proposal's criterion has two conjuncts joined by "AND" — only the first is implemented.

**Why missed:** `docs/proposal_vs_implementation_v2.md` marked Merge as ✅ Faithful. The audit checked that the threshold was correct and the type-scoping was right, but did not verify that BOTH conjuncts of the similarity check were wired.

**Compounding factor:** generated capabilities had empty `input_schema`/`output_schema` because of an Iteration-3 TODO at `src/orchestrator/tool_loader.py:310`. Even if the second conjunct had been wired, it would have been vacuous.

**Fix (part of B1):** populate schemas (already done for seeds; AST extractor done but not yet wired for generated caps) + add Jaccard helper + add second conjunct to `identify_candidates_node`.

### Gap 2: `merge()` produces metadata-only stubs

**Symptom:** `operations.py:304-311` creates the merged `CapabilityDescriptor` with only `name`, `description`, `capability_type`, `domain`, `tags`. **No source_code, no skill_content, no agent_config, no metadata dict, no schemas.** Both parents get deactivated via `registry.deactivate()`. The merged capability is a descriptor with nothing to execute.

**Proposal says:** *"The merged version inherits the strongest behavioral patterns from each parent."* Not honored in any form — the merged version inherits nothing executable.

**Eval impact if unfixed:** `full` baseline runs optimizer, merge fires on a low-reuse pair, parents deactivated, subsequent task matches the stub via gap detection, compositional executor tries to load it, `tool_loader.load_tool()` fails (no source code), **task fails**. The `full` baseline would show MORE failures than `no_optimization` — a misleading measurement artifact.

**Fix (partial, ~1 hour):** in `merge()`, after the LLM call produces the merged description, pick the highest-reuse parent, copy its `source_code`/`skill_content`/`agent_config`/`input_schema`/`output_schema` onto the merged descriptor. This is not a full "inherits strongest behavioral patterns" implementation (that requires behavioral differential — ~4 hours, Chapter 7 future work), but it produces an **executable** merged capability — strictly better than stubs.

**Trade-off:** the losing parent's unique behavior is lost. Combined with the B1 Jaccard `< 1.0` safety rule below, this bounds the damage: only non-identical-schema pairs get merged, and the higher-reuse parent's implementation (which is empirically preferred) survives.

### Gap 3: `split()` also produces metadata-only stubs

**Symptom:** same pattern. `operations.py:190-198` creates each variant `CapabilityDescriptor` without source_code/skill_content/agent_config, then deactivates the parent. Variants are unexecutable.

**Proposal says:** *"The optimizer agent analyzes failure cases to identify the input subspaces where the capability breaks, then generates targeted variants for each subspace."* The key word is **generates** — variants should have new, narrower code. The current implementation skips this entirely.

**Why it's harder to fix than merge:** the proper fix requires calling the MCP_TOOL/SKILL generator from inside the optimizer to produce narrower code for each variant. That is ~4-8 hours of work and introduces new LLM calls in the optimizer loop — significant scope creep. Any "quick fix" that inherits the parent's code unchanged defeats the purpose of split (variants are supposed to specialize).

**Fix (for B1 / Phase 5 eval):** **disable `split()` for the eval.** Set `split_reuse_threshold=1.01` in the pipeline factory so no capability ever qualifies. Document as explicit limitation in Chapter 6. Proper fix deferred to Chapter 7.

### The `< 1.0` Jaccard safety rule (user-requested)

**Concern:** two capabilities with identical input/output schemas (Jaccard = 1.0) may have different internal logic — e.g., a strict CSV parser that drops malformed rows vs. a lenient one that keeps them. Jaccard alone cannot distinguish "true duplicate" from "different logic, same contract" without running the code. Under gap 2 (merge() produces stubs), an incorrect merge in this case destroys both working capabilities.

**Rule:** require schema Jaccard ∈ [0.5, 1.0). Merge candidates with Jaccard = 1.0 are **blocked**, not merged. Preserves valuable diversity at the cost of some registry bloat (true duplicates won't be cleaned up).

**Verification on the 2026-04-13 CLI registry:** neither of the two genuine merge candidate clusters we found has Jaccard = 1.0 (they're 0.600 and 0.500), so the rule doesn't exclude any legitimate merges we've observed. Also none of the chain-adjacent pairs had Jaccard = 1.0 (they're 0.20-0.29). The rule is a safety net against a failure mode we haven't yet observed but user's concern is valid — it could emerge on larger registries.

### Combined fix list for the next implementation session

| # | Fix | Effort | Priority |
|---|---|---|---|
| 1 | Populate generated-cap schemas via AST extraction (task #8, wire `loading_node`) | 45 min | **Eval-blocking** (needed for Gap 1 fix to be functional) |
| 2 | Migration script: populate schemas on existing 11 generated caps in `data/cli/chroma` (task #11) | 45 min | Experiment-enabling |
| 3 | `_schema_jaccard()` helper + integrate into `identify_candidates_node` with `[0.5, 1.0)` gate (task #16, closes Gap 1) | 30 min | **Eval-blocking** |
| 4 | Fix `merge()` to retain highest-reuse parent's implementation (Gap 2 partial fix) | 1 hour | **Eval-blocking** — produces misleading artifacts otherwise |
| 5 | Disable `split()` in pipeline factory by passing `split_reuse_threshold=1.01` (Gap 3 deferral) | 10 min | **Eval-blocking** — produces misleading artifacts otherwise |
| 6 | Verify Pillar 3 Merge finds clusters on migrated registry (task #14) | 30 min | Produces Chapter 6 number |
| 7 | Unit tests for new code (task #15) | 45 min | Hygiene |
| 8 | Update `docs/proposal_vs_implementation_v2.md` (task #17) — DONE 2026-04-13 | — | ✅ |

**Total remaining: ~4-5 hours.** Three of the fixes are eval-blocking; all five together produce a clean implementation ready for Phase 4 smoke tests → Phase 5 eval.

### What is NOT fixed in B1 (Chapter 7 future work)

- **Behavioral differential** for merge candidate gating — run both parents on test inputs, compare outputs, distinguish true duplicates from divergent behavior. ~4 hours. Uses existing Pillar 2 Tier 2 infrastructure.
- **Principled `split()`** with LLM code generation — produce actually-narrower variants rather than metadata stubs. ~4-8 hours. Needs a generator call from within the optimizer loop.
- **Full "inherits the strongest behavioral patterns"** — combine two parents' implementations via behavioral voting or LLM code synthesis. ~8+ hours. The merge code-retention fix is a minimal partial implementation.

---

## The actual plan (post-pivot)

### Goal

Make Pillar 3 Merge honor both proposal conjuncts:

> *"When two or more capabilities show very low reuse (<5%) but high similarity (measured by embedding distance of their descriptions **and** overlapping input schemas), they are merged into a single, broader capability."*

Currently: ✅ reuse < 5% + ✅ description cosine ≥ 0.5 + ❌ overlapping input schemas (not checked).
After B1: ✅ all three.

### Strategy

Three deliverables, in dependency order:

**1. Populate `input_schema`/`output_schema` on every registered capability.** Currently these fields exist on `CapabilityDescriptor` (persisted in ChromaDB metadata as JSON) but are empty for generated capabilities because `src/orchestrator/tool_loader.py:310` has a stale TODO from Iteration 3:

```python
# NOTE: input_schema and output_schema are left as empty dicts (defaults).
# The Tier 2 behavioral tester (Iteration 3) will need input_schema to
```

Seeds are now populated directly in `seed_registry()` (done 2026-04-13). Generated capabilities need two paths:

- **MCP_TOOL**: extract from the generated function signature via AST (walks the `@mcp.tool()` decorated async def, unwraps `Annotated[...]` in parameter annotations, reads return annotation if present). Falls back to empty dict on parse failure.
- **SKILL / AGENT**: propagate the decomposer's `SubTask.output_schema` (already produced in Phase 3.5c) from the subtask through the generator onto `GeneratedArtifact`, then onto `CapabilityDescriptor` at registration. Input schema for SKILL/AGENT is harder — use `{"upstream_data": "dict"}` as a generic placeholder when the capability is a middle/last step in a chain, or empty when standalone.

**2. Add a Jaccard schema-overlap helper** and integrate it into `identify_candidates_node` in `src/optimizer/loop.py` as the second conjunct of merge candidate selection. Simplest form: Jaccard over the union of `input_schema.keys() ∪ output_schema.keys()` for the two candidate capabilities. Threshold starts at 0.5 (same as the description-cosine threshold already in use) and is documented as tunable.

**3. Migrate existing persistent registries.** `data/cli/chroma` has 19 capabilities, 11 of which are generated and have empty schema fields. A one-off migration script (`scripts/migrate_registry_to_b1.py`) walks the registry, extracts schemas from `source_code` via the AST helper, and upserts back in place. Idempotent, dry-run mode first, tarball snapshot before running. For `data/eval/chroma`, no migration needed — the evaluation harness re-seeds per run and will automatically pick up schemas via the updated `seed_registry()`.

### Changes required, by file

#### `src/schemas/task.py` — DONE
Added `input_schema: dict[str, str]` and `output_schema: dict[str, str]` fields to `GeneratedArtifact` with `default_factory=dict`. Backward-compatible because the old `extra="forbid"` config is preserved and defaults are empty.

#### `src/registry/store.py` — DONE
- `seed_registry()` updated: `_cap()` accepts `input_schema`/`output_schema` kwargs and all 8 seeds pass concrete schemas
- `json-validator` description corrected to describe the actual `{"errors": [...]}` dict return (was "return a list" which was my own earlier fix error)
- `_build_document()` **reverted to prose-only format** (empirically validated at 81% Phase A recall; any enrichment regressed this)
- `_format_signature()` helper exists but is now **dead code** — consider removing or leaving as a breadcrumb for future experiments

#### `src/schemas/task.py` — GeneratedArtifact already done

#### `scripts/test_registry_matching.py` — DONE
- `_get_implementation()` truncation bumped 2500 → 8000 chars to eliminate the deterministic hallucination vector where the judge saw `f"Row {i` (mid-identifier truncation of `{idx}`) and invented NameError bugs

#### `src/orchestrator/loading_node.py` or new helper — PENDING (task #7 + #8)
Add MCP_TOOL AST signature extraction. At registration time, for each `GeneratedArtifact`:

```python
if artifact.capability_type == CapabilityType.MCP_TOOL and artifact.source_code:
    input_schema, output_schema = extract_mcp_tool_signature(artifact.source_code)
    artifact.input_schema = input_schema
    artifact.output_schema = output_schema
elif artifact.capability_type in (CapabilityType.SKILL, CapabilityType.AGENT):
    # Propagate from the decomposer's SubTask.output_schema
    subtask = _find_subtask_for_gap(artifact.gap_query, state.subtasks)
    if subtask and subtask.output_schema:
        artifact.output_schema = subtask.output_schema
    # Input schema: empty for standalone, {"upstream_data": "dict"} for chain middle/last
```

The AST helper lives in a new module or extends `src/orchestrator/tool_loader.py`. Signature:

```python
def extract_mcp_tool_signature(source_code: str) -> tuple[dict[str, str], dict[str, str]]:
    """Parse MCP tool source and return (input_schema, output_schema).

    Walks the AST finding the @mcp.tool() decorated async def, extracts
    parameter names and annotations (unwrapping Annotated[...] wrappers),
    and reads the return annotation if present. Never raises — returns
    ({}, {}) on parse failure.
    """
```

Input schema keys are parameter names; values are the annotation text (stripped of `Annotated[...]` wrappers). Output schema keys are derived from the return annotation if it's a `dict` type literal or a `TypedDict`; otherwise empty.

#### `src/optimizer/loop.py` — PENDING (task #16)

In `identify_candidates_node`, extend the existing loop that pairs low-reuse SKILLs:

```python
# ...existing code through `similar = registry.search(cap.description, n_results=n_search)`

for similar_cap, score in similar:
    partner_id = similar_cap.name
    if (
        partner_id != skill_id
        and partner_id in low_reuse_set
        and partner_id not in paired
        and similar_cap.is_active
        and score >= 0.5                                    # first conjunct (existing)
        and _schema_jaccard(cap, similar_cap) >= 0.5        # second conjunct (new)
    ):
        merge_cands.append((skill_id, partner_id))
        paired.add(skill_id)
        paired.add(partner_id)
        break
```

The `_schema_jaccard` helper:

```python
def _schema_jaccard(a: CapabilityDescriptor, b: CapabilityDescriptor) -> float:
    """Jaccard similarity over the union of input+output schema key names.

    Computes |A ∩ B| / |A ∪ B| where A and B are the sets of parameter
    and output key names from each capability's schemas. Returns 0.0
    if either side has no schema. The proposal's "overlapping input
    schemas" is interpreted as key-name overlap (not type overlap)
    because type tokens like 'int'/'dict' carry little semantic
    signal for clustering decisions.
    """
    a_keys = set((a.input_schema or {}).keys()) | set((a.output_schema or {}).keys())
    b_keys = set((b.input_schema or {}).keys()) | set((b.output_schema or {}).keys())
    if not a_keys or not b_keys:
        return 0.0
    intersection = a_keys & b_keys
    union = a_keys | b_keys
    return len(intersection) / len(union)
```

Note: this interpretation of "overlapping input schemas" is a **choice**. Alternatives include (a) Jaccard on inputs only (stricter), (b) Jaccard on (key, type) tuples (stricter, type-aware), (c) structural subset check. Key-name union over inputs+outputs was chosen for simplicity and because the decomposer's output_schema is already the strongest structural signal available.

#### `scripts/migrate_registry_to_b1.py` — PENDING (task #11)

One-off script. Walks a persistent registry, extracts schemas from `source_code` where empty, upserts. Idempotent (skips capabilities that already have non-empty schemas). Dry-run mode prints proposed changes without writing. Tarball snapshot created before any writes.

#### `tests/unit/` — PENDING (task #15)
- Unit tests for `extract_mcp_tool_signature()` covering: bare async def, `@mcp.tool()` decorated, `Annotated[int, Field(...)]` parameters, missing return annotation, parse failure cases
- Unit tests for `_schema_jaccard()` covering: both-empty → 0.0, one-empty → 0.0, full-overlap → 1.0, partial overlap, symmetric

#### `docs/proposal_vs_implementation_v2.md` — PENDING (task #17)
Update the Pillar 3 Merge entry from ✅ Faithful to a narrative that acknowledges the discovered drift (missing second conjunct) and its resolution. This is a minor doc revision.

### Verification experiment

**Task #14: run merge candidate selection against the migrated `data/cli/chroma` registry and count clusters.**

Expected outcome: **at least one** merge candidate pair emerges that has both high description cosine AND high schema Jaccard. The ~3 "parse CSV + XLSX/YAML + CSV → structured tables" tools (`parse_inventory_csv_and_reorder_rules_ya`, `parse_sales_csv_and_products_xlsx_and_ta`, `aggregate_spending_per_department_from_c`) are the strongest candidates — they differ in business vocabulary but have structurally identical input/output shapes once the schemas are populated.

If zero clusters emerge, the Jaccard threshold may need tuning (try 0.3), or the schemas may be too sparsely populated (check migration output). Either is a tractable follow-up.

**Minimum success criterion**: merge candidate count on `data/cli/chroma` goes from **0 (current)** to **≥1**.

### Out of scope

Explicitly **not** doing any of:

- ❌ Embedding document enrichment (attempted, regressed recall, reverted)
- ❌ Gap-detection post-filter using schema overlap (would add a new mechanism not specified in the proposal; deferred to Chapter 7 future work)
- ❌ Changes to the generator prompts (monolith risk, vetoed earlier)
- ❌ Changes to the verification gate (Pillar 2 is untouched)
- ❌ Changes to `merge()` function itself in `operations.py` (still takes list of IDs, still does LLM description synthesis; only the candidate *selector* in `loop.py` changes)
- ❌ Phase D pre/post re-run with probe cache (without embedding change, firing rates don't change; Phase D remains motivation, not a delta experiment)

### Effort estimate (post-pivot)

| Task | Estimate | Status |
|---|---|---|
| Seeds + schema fields on `GeneratedArtifact` | 45 min | ✅ done |
| json-validator desc re-fix | 5 min | ✅ done |
| Truncation cap bump in test_registry_matching | 5 min | ✅ done |
| `extract_mcp_tool_signature()` AST helper + tests | 60 min | pending |
| `loading_node` wiring for all three types | 45 min | pending |
| `_schema_jaccard()` helper + integration into `loop.py` | 30 min | pending |
| Migration script (dry-run + real run) | 45 min | pending |
| Snapshot `data/cli/chroma` tarball | 5 min | pending |
| Manual merge verification experiment | 30 min | pending |
| Unit tests for new code | 30 min | pending |
| Update `proposal_vs_implementation_v2.md` | 15 min | pending |

**Total remaining: ~4.5 hours.** About 1 hour's worth of setup is already done.

### Thesis narrative after B1 lands

> *"The proposal's Pillar 3 Merge clause reads: 'When two or more capabilities show very low reuse (<5%) but high similarity (measured by embedding distance of their descriptions and overlapping input schemas), they are merged into a single, broader capability.' Our initial implementation honored only the first conjunct (description cosine ≥ 0.5 via ChromaDB), and generated capabilities had empty `input_schema`/`output_schema` fields because the generator did not populate them at registration time — a known TODO from Iteration 3 that rendered the second conjunct vacuous. We closed both gaps: (a) input/output schemas are populated on every registered capability — seeds via direct specification, generated MCP tools via AST extraction of the decorated function signature, generated skills/agents via propagation from the decomposer's SubTask output_schema — and (b) the merge candidate selector requires Jaccard similarity ≥ 0.5 on the union of schema key names as a second conjunct. Empirically on the 2026-04-13 CLI registry (19 capabilities, 11 generated), this raised the merge candidate count from 0 to N, including the cluster of three 'parse + transform + aggregate' tools that differ only in business vocabulary (inventory/reorder, sales/products, departments/transactions) but share structurally identical input-file-plus-config-plus-output-records shapes."*

### What the thesis does NOT claim

B1 is a fix, not an advance. It does not:

- Reduce the rate at which scenario-bound capabilities are generated (that would require touching the generator, which risks the anti-monolith regression)
- Improve gap detection precision at match time (that would require the post-filter, deferred to Chapter 7)
- Change the specificity distribution (still 73% scenario-bound as of Phase D 2026-04-13)

Phase D's 91% tight / 9% mid / 0% cross reuse gradient remains the thesis's quantitative finding about tension #2. B1 is the infrastructure that lets Pillar 3 Merge operate on that finding; the finding itself is unchanged.

---

## Historical: embedding enrichment plan

The remainder of this document is the original plan from earlier 2026-04-13, preserved as a record of the design exploration that led to the pivot. The approach described below was implemented and then reverted after Phase A recall testing. Read it for context on why the direct-metadata path was chosen instead.

---

## Problem

Phase D reusability audit on 11 JIT-generated capabilities (persistent CLI registry, 2026-04-13) produced the following gradient:

| Probe level | Firing rate | Reuse rate (fired + compat judge OK) |
|---|---|---|
| tight (same domain reworded) | 91% (10/11) | 91% (10/11) |
| mid (same shape, different domain) | 18% (2/11) | 9% (1/11) |
| **cross (different vertical)** | **0% (0/11)** | **0% (0/11)** |

**Specificity distribution:** 73% scenario-bound, 18% domain-specific, 9% generic.

**Confirmed silent-poisoning case:** `parse_sales_csv_and_products_xlsx_and_ta_a39982` (scenario-bound, hardcodes `order_id/product_id/quantity/unit_price/region` column names) fires at cosine 0.634 on the mid-level probe `parse-transactions-csv-and-catalog-xlsx-and-goals-csv-to-tables` — above the 0.6 match threshold — but Phase B's compatibility judge correctly flags the match as INCOMPAT because the hardcoded columns would fail at execution time on a transactions-domain dataset. **Naive matching at threshold 0.6 admits this silent failure; only the LLM compatibility audit catches it.**

**Root cause:** the current `_build_document()` in `src/registry/store.py` embeds only:

```
"{domain} > {capability_type} > {name}: {summary_of_description}"
```

This shallow-prefix hierarchy gives the embedding exactly one matching axis (description prose). Two failure modes emerge:

1. **False positives across domains:** structurally identical operations with different business vocabulary cluster weakly in prose-space, but a lexically-adjacent query can still fire above threshold on the wrong cap.
2. **Pillar 3 Merge cannot function:** the proposal says Merge triggers on *"embedding distance of descriptions AND overlapping input schemas"*. The implementation stores `input_schema`/`output_schema` as ChromaDB metadata JSON strings, but they are invisible to the similarity function. Merge's input-schema clause is currently dead code.

## Proposal alignment

The proposal's Pillar 1 stage 2 states:

> *"The registry uses hierarchical embeddings (domain → capability → specific skill) to prevent both false positives (superficial keyword matches) and false negatives (semantically equivalent but differently named skills)."*

The *"(domain → capability → specific skill)"* tier list is illustrative (`e.g.`-style framing); the load-bearing statement is the goal (prevent false positives/negatives). Phase D is direct evidence that the current three-tier-as-prefix instantiation does not meet the goal.

The proposal's Pillar 3 Merge clause:

> *"When two or more capabilities show very low reuse (<5%) but high similarity (measured by embedding distance of their descriptions and overlapping input schemas), they are merged."*

Requires input-schema access inside the similarity function. B1 is the minimum implementation that makes this clause actionable.

**Verdict:** B1 delivers on both clauses. See `docs/proposal_vs_implementation_v2.md` — this closes the gap flagged as *"Hierarchy via text prefix, not nested collections — proposal silent on implementation."*

## Design

### New embedding document format

Four-tier hierarchy instantiated as a multi-line document per capability:

```
{domain} > {capability_type} > {name}
inputs: {input_signature}
outputs: {output_signature}
summary: {description_summary}
```

Example for `csv-parser`:

```
data-processing > mcp_tool > csv-parser
inputs: file_path:str
outputs: row_count:int, column_names:list[str]
summary: Parse a CSV file (comma-delimited, default csv.DictReader dialect) and return the row count and the list of column header names.
```

Example for `parse_sales_csv_and_products_xlsx_and_ta_a39982`:

```
data-processing > mcp_tool > parse_sales_csv_and_products_xlsx_and_ta_a39982
inputs: sales_csv_path:str, products_xlsx_path:str, targets_csv_path:str
outputs: sales:list[dict], products:list[dict], targets:list[dict]
summary: Parse three input files and return JSON-serializable lists for sales, products, and targets…
```

Embedding newline-separated multi-line documents is supported by all-MiniLM-L6-v2 (the ChromaDB default embedder). The embedding function tokenizes whitespace/punctuation uniformly, so the structural markers (`inputs:`, `outputs:`, `summary:`) act as lightweight field labels in the vector space.

### Signature formatting

`_format_signature(schema: dict) -> str` produces compact comma-separated `name:type` pairs:

- `{"file_path": "str"}` → `"file_path:str"`
- `{"row_count": "int", "column_names": "list[str]"}` → `"row_count:int, column_names:list[str]"`
- Empty dict or `None` → `""` (omitted from document)
- Primitive return types (e.g., Fibonacci returns `int`): `"n:int"` / outputs line omitted
- `dict` or `list[dict]` without further schema detail: `"result:dict"` or `"items:list[dict]"`

### Schema field already exists on `CapabilityDescriptor`

Reading `src/registry/store.py:62-82` (`_build_metadata`) and `store.py:105-119` (`_descriptor_from_metadata`), `CapabilityDescriptor` already has `input_schema` and `output_schema` fields, persisted as JSON strings in ChromaDB metadata. **No schema change required** — the fields are present but never populated, and the embedding function ignores them.

## Changes required

### 1. `src/registry/store.py`

**`_build_document()`** — new multi-line format with conditional inputs/outputs lines:

```python
@staticmethod
def _build_document(capability: CapabilityDescriptor) -> str:
    domain = getattr(capability, "domain", "general")
    summary = SkillRegistry._summarize_description(capability.description)
    input_sig = SkillRegistry._format_signature(
        getattr(capability, "input_schema", None) or {}
    )
    output_sig = SkillRegistry._format_signature(
        getattr(capability, "output_schema", None) or {}
    )
    parts: list[str] = [
        f"{domain} > {capability.capability_type.value} > {capability.name}"
    ]
    if input_sig:
        parts.append(f"inputs: {input_sig}")
    if output_sig:
        parts.append(f"outputs: {output_sig}")
    parts.append(f"summary: {summary}")
    return "\n".join(parts)
```

**`_format_signature()`** — new static helper:

```python
@staticmethod
def _format_signature(schema: dict) -> str:
    """Format a schema dict as 'name:type, name:type' for embedding."""
    if not schema:
        return ""
    parts: list[str] = []
    for name, spec in schema.items():
        if isinstance(spec, str):
            type_str = spec
        elif isinstance(spec, dict) and "type" in spec:
            type_str = str(spec["type"])
        else:
            type_str = "any"
        parts.append(f"{name}:{type_str}")
    return ", ".join(parts)
```

**`seed_registry()`** — add `input_schema` / `output_schema` to every seeded `_cap()` call. Concrete per-seed values:

| Seed | input_schema | output_schema |
|---|---|---|
| `fibonacci` | `{"n": "int"}` | `{"result": "int"}` |
| `csv-parser` | `{"file_path": "str"}` | `{"row_count": "int", "column_names": "list[str]"}` |
| `date-formatter` | `{"date_string": "str", "input_format": "str", "output_format": "str"}` | `{"formatted_date": "str"}` |
| `json-validator` | `{"config_path": "str", "schema_path": "str"}` | `{"errors": "list[str]"}` |
| `text-statistics` | `{"file_path": "str"}` | `{"word_count": "int", "readability_score": "float"}` |
| `data-cleaning-workflow` | `{"data": "list[dict]"}` | `{"clean_row_count": "int", "removed_nulls": "int", "removed_duplicates": "int", "outliers_flagged": "int"}` |
| `report-generation` | `{"data": "list[dict]"}` | `{"summary": "str", "stats_table": "dict", "anomalies": "list[dict]"}` |
| `data-analysis-agent` | `{"dataset_path": "str"}` | `{"insights": "list[str]", "profile": "dict"}` |

Also extend `_cap()` signature to accept and pass through `input_schema` / `output_schema` kwargs.

### 2. `src/orchestrator/loading_node.py`

At registration time, populate `input_schema` / `output_schema` on the `CapabilityDescriptor` from the `GeneratedArtifact`. Two sources:

- **MCP_TOOL:** parse the generated `source_code` via AST to extract the function signature → input_schema. For output_schema, use the decomposer's per-subtask `output_schema` (already produced in Phase 3.5c) — propagate it from `SubTask` through the generator into `GeneratedArtifact`, then into the descriptor at registration.
- **SKILL / AGENT:** use the decomposer's `output_schema` for outputs; inputs are harder (no function signature) — use `{"upstream_data": "dict"}` as a generic placeholder or parse from the skill's declared parameters if present.

**Verification step before writing code:** confirm that `GeneratedArtifact` has or can be extended with `input_schema` / `output_schema` fields. If not present, add them as optional fields (default empty dict) in `src/schemas/task.py`. Backward-compatible.

### 3. AST helper for extracting MCP_TOOL signatures

New helper (location TBD, probably `src/orchestrator/loading_node.py` or a new `src/orchestrator/schema_extraction.py`):

```python
def extract_mcp_tool_signature(source_code: str) -> tuple[dict, dict]:
    """Parse an MCP tool's source and return (input_schema, output_schema).

    Looks for the @mcp.tool() decorated async function, reads its parameter
    annotations (stripping Annotated[...] wrappers), and reads its return
    annotation if present.

    Returns ({}, {}) on parse failure — never raises.
    """
```

This enables retroactive schema extraction for the 11 existing generated caps in `data/cli/chroma`.

### 4. Migration: re-embed existing persistent registries

For each persistent ChromaDB registry (`data/cli/chroma`, `data/eval/chroma`):

1. List all capabilities via `registry.list_all(is_active=None)`
2. For each, if `input_schema`/`output_schema` is empty AND `source_code` is present, run `extract_mcp_tool_signature()` and populate the fields
3. Call `registry.add(cap)` to re-embed with the new document format (upsert semantics leave other capabilities untouched)

One-off script: `scripts/migrate_registry_to_b1.py`. Idempotent. ~15 min to write and test.

### 5. `src/optimizer/operations.py` — verify Merge picks up the new signal

The `merge()` function should automatically benefit because it uses `registry.search()` which uses the new document format. **Verify by inspection** — no code change expected, but confirm:

- Merge candidate pairs are drawn from `registry.search(query, ...)` results
- The similarity threshold for merge candidacy (currently `>= 0.5` in `loop.py`'s merge-pair logic) is still sensible under the new embedding

If the threshold needs adjustment, document the change here with the measured similarity distribution.

### 6. `scripts/test_registry_matching.py` — fix the truncation hallucination vector

Pre-B1 cleanup (not strictly part of B1, but blocks trustworthy Phase D re-runs):

- Bump `_get_implementation()` truncation from 2500 → 8000 chars (covers all existing generated caps, the largest is ~4100 chars)
- Add explicit labeling in `_SELF_AUDIT_PROMPT` disambiguating `registry_description` (the field under audit) from any docstrings embedded in source

Not blocking for B1 implementation but should land before we re-cite any Phase C numbers.

## Migration plan for existing data

**`data/cli/chroma` (19 capabilities):**

1. Run `scripts/migrate_registry_to_b1.py` to re-extract signatures and re-embed
2. Run `uv run scripts/test_registry_matching.py --generalization-audit --skip-self-audit --registry-path data/cli/chroma` to produce the **post-B1 Phase D numbers**
3. Compare to the baseline (91/9/0)

**`data/eval/chroma`:**

- The evaluation harness re-seeds from `seed_registry()` per run via `tests/evaluation/snapshots.py`
- With `seed_registry()` updated to pass input/output schemas, new eval runs automatically get B1
- **No existing data to migrate** — eval registries are ephemeral relative to run invocations
- Any cached evaluation snapshot should be invalidated once before the next eval run

## Experiment: pre/post Phase D delta

The thesis claim is that B1 improves cross-domain matchability and enables Pillar 3 Merge. We prove this by running Phase D twice on the same registry.

### Baseline (already measured, 2026-04-13):

```
Phase D on data/cli/chroma (pre-B1):
  Specificity: 73% scenario-bound, 18% domain-specific, 9% generic
  Firing rate:  tight 91%, mid 18%, cross 0%
  Reuse rate:   tight 91%, mid  9%, cross 0%
```

### Post-B1 measurement protocol:

1. **Freeze the probe set.** Phase D currently generates probes via LLM per run. For a clean before/after, save the baseline probe set to a JSON file during the first run, load it in subsequent runs. Add `--probe-cache PATH` flag.
2. **Re-run Phase D** using the cached probes against the migrated registry.
3. **Report deltas** in each cell of the 2×3 firing/reuse table.

### Expected outcomes and their interpretation:

| Scenario | Expected numbers | Interpretation |
|---|---|---|
| **Best case** | mid 40-60%, cross 20-40% | B1 works. Ship it. Chapter 6 has a clean positive finding. |
| **Modest case** | mid 25-40%, cross 5-20% | B1 helps but generation-time specificity is still the dominant limit. Chapter 6 reports partial improvement; Chapter 7 future work: post-generation description rewriting. |
| **Null case** | mid <20%, cross <5% | B1 does not close the gap — the bottleneck is in the code, not the embedding. Chapter 6 reports the negative result honestly. Fallback option: make Pillar 3 Merge operate on I/O schema similarity only (not description), measured separately. |

**Secondary experiment — verify Pillar 3 Merge:**

Post-migration, manually invoke `split_merge_node` against `data/cli/chroma` (or run `scripts/test_split_merge.py` against the migrated registry) and count:

- Merge candidate pairs found
- Merge candidate pairs that share >50% input/output schema keys
- Whether the 3 "parse CSV + XLSX/YAML + CSV → structured tables" caps (`parse_sales_csv_...`, `parse_inventory_csv_...`, `aggregate_spending_...`) cluster as candidates — they should

## Risks

1. **Re-embedding might hurt recall on previously-matching queries.** Adding content to the document shifts the vector space. A query that matched `csv-parser @ 0.7` pre-B1 might drop to `@ 0.6` post-B1 because new tokens in the document dilute the summary signal.
   - **Mitigation:** re-run Phase A on the seeded ephemeral registry with the 16 positive probes pre- and post-B1. If recall drops below 75%, tune the document format (reduce summary length, elevate inputs/outputs).

2. **Embedding token budget.** `all-MiniLM-L6-v2` has a 256-token input limit. A verbose multi-line document could truncate.
   - **Mitigation:** cap summary at 30 words (down from 50) when the signature lines are present. Enforce in `_build_document()`.

3. **Agents have weak signatures.** `data-analysis-agent` doesn't have a clean function signature. The `{"dataset_path": "str"}` / `{"insights": "list[str]"}` guess above is illustrative, not canonical.
   - **Mitigation:** for AGENT-type capabilities, allow empty signature lines — fall back to the description-only embedding. Not all types need all tiers.

4. **`_format_signature()` with free-form dict values.** Decomposer output_schemas use natural-language type descriptions (e.g., `"list[dict] — each dict has product_id, product_name, current_stock"`). Tokenizing the full description into the embedding is noisy.
   - **Mitigation:** truncate each type string to its first 5 words, dropping anything after an em-dash. Keep the short type prefix in the embedding.

5. **MCP_TOOL AST extraction fails on Annotated pydantic patterns.** Generator-produced code uses `Annotated[int, Field(description=..., ge=0, le=1000)]` — non-trivial to parse.
   - **Mitigation:** walk the AST finding the decorated `async def`, extract arg names and annotation source text, regex for the inner type (`Annotated[(\w+),` → `int`). Fall back to `any` on parse failure. Acceptable — we get partial signatures even when Annotated chains are complex.

6. **Migration script corrupts registry.** Bug in `extract_mcp_tool_signature()` or `_build_document()` could write malformed documents.
   - **Mitigation:** dry-run mode first (`--dry-run` prints proposed new document per capability without writing). Snapshot ChromaDB to tarball before migration. Back out by restoring the tarball.

## Success criteria

**B1 is complete when all of the following hold:**

1. ✅ `_build_document()` produces the four-tier format with conditional `inputs:` / `outputs:` lines
2. ✅ `seed_registry()` passes input/output schemas for all 8 seeds; Phase A recall on the 16 positive probes stays ≥ 85% (within ~5pp of the pre-B1 baseline)
3. ✅ `loading_node` populates `input_schema` / `output_schema` on new `CapabilityDescriptor` instances for all three types (MCP_TOOL via AST, SKILL/AGENT via decomposer output_schema)
4. ✅ Migration script runs cleanly on `data/cli/chroma`, re-embeds all 19 capabilities, no document truncation warnings
5. ✅ Post-B1 Phase D numbers are reported alongside the baseline in a single table
6. ✅ `scripts/test_split_merge.py` (or manual optimizer invocation) finds at least one merge candidate pair with >50% schema overlap on the migrated CLI registry — empirical proof that Merge now functions

**B1 is NOT expected to deliver:**

- Higher success rate on the 20-task eval (B1 is a matching refinement, not a generation refinement)
- Automatic reduction in `scenario-bound` classification (B1 doesn't change the code)
- Fewer capabilities in the registry (B1 enables Merge to reduce count — measuring this is a separate experiment)

## Effort estimate

| Task | Estimate |
|---|---|
| `_build_document()` + `_format_signature()` | 30 min |
| `seed_registry()` schema additions | 30 min |
| `extract_mcp_tool_signature()` AST helper + unit tests | 60 min |
| `loading_node` wiring for all three types | 60 min |
| Migration script (`scripts/migrate_registry_to_b1.py`) | 45 min |
| Phase D probe caching (`--probe-cache PATH`) | 30 min |
| Truncation fix in `test_registry_matching.py` (pre-B1 cleanup) | 10 min |
| Manual merge verification experiment | 30 min |
| Unit test updates | 30 min |
| Document results in `docs/proposal_vs_implementation_v2.md` | 15 min |

**Total: ~5.5 hours of focused work.** One working session.

## Open questions

1. **Should AGENT capabilities embed any signature?** The 3 AGENT caps in the CLI registry have no clean function signature. Proposal is silent on how AGENT-type capabilities should be indexed. Tentative answer: empty signature lines, description-only embedding for AGENT. Revisit if AGENT reuse is pathologically low post-B1.

2. **How to handle the `upstream_data: dict` parameter added in Phase 3.5c?** MCP_TOOL middle/last steps in chains take a single `upstream_data: dict` argument. Extracting this literal from the signature gives `{"upstream_data": "dict"}` — uninformative. Better: synthesize the effective inputs from the decomposer's upstream step output_schemas (already available at generation time). Requires passing the upstream context through to the registration step.

3. **Should we compress or preserve the decomposer's natural-language type descriptions?** The decomposer produces `"list[dict] — each dict has product_id, product_name, current_stock"`. Embedding the full description is noisy; embedding just `list[dict]` loses information. Tentative answer: keep the full string, truncated at 5 words per type.

4. **Is the existing `snapshots.py` mechanism aware of the embedding document format?** If it snapshots raw ChromaDB files (including pre-computed vectors), a post-B1 snapshot can't be restored on a pre-B1 codebase. Tentative answer: document as a one-way migration; note in `snapshots.py` that B1-era snapshots are incompatible with older code.

5. **Do we need pre-B1 vs post-B1 probe stability for Phase D?** Phase D probes are LLM-generated per-capability per-run. Running twice produces different probe sets. We need the `--probe-cache PATH` flag to freeze the probe set, otherwise pre/post comparison is confounded.

---

## Sign-off checklist before coding

- [ ] `GeneratedArtifact` schema extended with `input_schema`/`output_schema` if not already present
- [ ] `CapabilityDescriptor` has input/output schema fields populated and persisted correctly (already verified via `_build_metadata` / `_descriptor_from_metadata` — confirmed present)
- [ ] `data/cli/chroma` snapshot tarball created before migration
- [ ] `scripts/test_registry_matching.py` gains `--probe-cache PATH` flag (or we accept noisy before/after)
- [ ] Pre-B1 Phase D baseline frozen in a saved file: `docs/phase_d_baseline_2026-04-13.json`
- [ ] Unit tests updated: `tests/unit/test_store.py` covers the new document format and format_signature helper
