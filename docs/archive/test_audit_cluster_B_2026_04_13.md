# Test Audit: Generators Cluster (MCP_TOOL / SKILL / AGENT)

**Date:** 2026-04-13  
**Files Covered:**
- `tests/unit/test_mcp_tool_generator.py` (1037 LOC)
- `tests/unit/test_skill_generator.py` (647 LOC)
- `tests/unit/test_agent_generator.py` (944 LOC)

**Source References:**
- `src/orchestrator/generators/mcp_tool.py`, `skill.py`, `agent.py`
- `src/orchestrator/generators/utils.py` (build_task_context, slugify_gap, slugify_gap_hyphen)
- `src/schemas/task.py` (GeneratedArtifact, LeadWithSubagentsResult, SubAgentConfig)

---

## Top 10 Concrete Findings

### 1. **Fix 5 (forced_name) — MCP_TOOL enforcement**
- **File:** `tests/unit/test_mcp_tool_generator.py`
- **Line:** 759–775, function `test_generate_single_tool_uses_forced_name`
- **Classification:** VITAL
- **Reason:** Verifies artifact.capability_name uses slugify_gap(gap.query), not LLM-chosen tool_name. Post-Fix-5 critical invariant.
- **Recommendation:** keep

### 2. **Fix 5 (forced_name) — SKILL enforcement**
- **File:** `tests/unit/test_skill_generator.py`
- **Line:** 531–559, function `test_skill_name_derived_from_query_when_frontmatter_has_no_name`
- **Classification:** VITAL
- **Reason:** Verifies artifact.capability_name uses slugify_gap_hyphen(gap.query), enforcing deterministic naming post-Fix-5.
- **Recommendation:** keep

### 3. **Fix 5 (forced_name) — AGENT enforcement**
- **File:** `tests/unit/test_agent_generator.py`
- **Line:** 535–552, function `test_first_attempt_succeeds_plain_agent`
- **Classification:** VITAL
- **Reason:** Verifies artifact.capability_name uses slugify_gap_hyphen(gap.query), not result.agent_name.
- **Recommendation:** keep

### 4. **Phase 3.5b — Lead-with-subagents mode selection**
- **File:** `tests/unit/test_agent_generator.py`
- **Line:** 788–863, functions `test_single_agent_gap_uses_standalone_mode` and `test_multiple_agent_gaps_uses_lead_mode`
- **Classification:** VITAL
- **Reason:** Verifies that 1 AGENT gap → _generate_single_agent, ≥2 AGENT gaps → _generate_lead_with_subagents. Critical Phase 3.5b routing.
- **Recommendation:** keep

### 5. **Phase 3.5c — schema_context parameter NOT EXERCISED**
- **File:** `tests/unit/test_mcp_tool_generator.py`, `test_skill_generator.py`, `test_agent_generator.py`
- **Line:** All `TestGenerateSingleTool/Skill/Agent` classes
- **Classification:** WEAK
- **Reason:** Generators call build_schema_context(gap, subtasks) in source, but all unit tests mock without passing schema_context arg. Phase 3.5c data-flow contract is untested at unit level.
- **Recommendation:** augment — add one test per generator that passes non-empty schema_context and verifies it flows to LLM prompt

### 6. **pip_packages field — not asserted**
- **File:** `tests/unit/test_mcp_tool_generator.py`
- **Line:** 582–587 (_VALID_CODE fixture), 617–631 (test_first_attempt_succeeds)
- **Classification:** WEAK
- **Reason:** GeneratedToolCode.pip_packages defaults to []. Tests never assert or populate pip_packages; they pass even if real LLM output is ignored.
- **Recommendation:** augment — mock GeneratedToolCode with non-empty pip_packages list and assert it survives to GeneratedArtifact

### 7. **Prompt context forwarding — OVER-SPECIFIED**
- **File:** `tests/unit/test_mcp_tool_generator.py`, `test_skill_generator.py`, `test_agent_generator.py`
- **Line:** MCP 717–736 (`test_task_context_forwarded`), SKILL 561–583, AGENT 612–631
- **Classification:** OVER-SPECIFIED
- **Reason:** Tests capture and assert the exact custom_context string appears in the prompt. This is brittle to prompt rewording; semantic intent is just "context gets forwarded".
- **Recommendation:** rewrite — replace substring assertions with "prompt length increased" or "prompt contains key terms from context" (looser coupling to exact wording)

### 8. **Agent repair prompt validation feedback — WEAK**
- **File:** `tests/unit/test_agent_generator.py`
- **Line:** 633–660, function `test_repair_prompt_includes_validation_errors`
- **Classification:** WEAK / OVER-SPECIFIED
- **Reason:** Checks if "agent_name" or "InvalidName" string appears in repair prompt. Brittle to prompt template wording; doesn't verify semantic structure (that errors are formatted correctly).
- **Recommendation:** rewrite — check that repair prompt contains formatted list of errors in a stable pattern (e.g., "- " prefix) rather than exact field names

### 9. **Lead-with-subagents _generate_lead_with_subagents() — indirect coverage only**
- **File:** `tests/unit/test_agent_generator.py`
- **Line:** 820–863 (test_multiple_agent_gaps_uses_lead_mode), 865–905 (test_lead_failure_falls_back)
- **Classification:** VITAL (for what it covers), but incomplete
- **Reason:** Tests invoke _generate_lead_with_subagents via the node, but do not test it in isolation. No direct unit tests for _generate_lead_with_subagents(gaps, task_context) return value shape, subagent_gap_queries merging, or subagent frontmatter rewriting.
- **Recommendation:** augment — add TestGenerateLeadWithSubagents class with:
  - Happy path: verify artifact.agent_config["subagents"] has N entries, artifact.agent_config["subagent_gap_queries"] matches gap queries
  - Verify frontmatter name rewrite to forced_name
  - Repair pass coverage (invalid first, valid second)

### 10. **input_schema / output_schema fields — untested**
- **File:** `tests/unit/test_mcp_tool_generator.py`, `test_skill_generator.py`, `test_agent_generator.py`
- **Line:** All GeneratedArtifact construction in tests
- **Classification:** WEAK
- **Reason:** GeneratedArtifact.input_schema and output_schema added 2026-04-13, default to {}. Tests construct artifacts without these fields; B1 will wire AST extractors but unit tests provide no protection against regressions.
- **Recommendation:** wait-for-B1 (AST extraction logic will come in B1; mark as deferred)

---

## Per-File Briefs

### `test_mcp_tool_generator.py` (1037 LOC)

**What it protects:**
- Schema validation for GeneratedToolCode (lines 69–95)
- Code syntax and structural checks (_validate_code, lines 102–275)
- Tool name validation (_validate_tool_name, lines 304–362)
- Node routing: filters MCP_TOOL gaps, preserves state, routes to generate_skill (lines 369–562)
- Repair path: first attempt success/fail, repair success/fail, LLM exception handling (lines 570–776)
- Skip-on-retry logic: gaps with matching gap_query are not regenerated (lines 783–843)
- Parallel generation: multiple gaps are processed concurrently (lines 920–1037)

**Whether protection is live:**
- VITAL tests (filtering, repair paths, Fix 5 forced_name) are all **active and passing the current source**.
- Validation helpers are comprehensive (async def counting via AST, import scanning, multiple errors collected).

**Structural issues:**
- **schema_context not exercised:** node calls build_schema_context(gap, subtasks) but tests never pass schema_context argument. Phase 3.5c contract untested.
- **Prompt over-specification:** test_task_context_forwarded (lines 717–736) and test_default_task_context (lines 738–757) assert substring presence; brittle to prompt rewording.

---

### `test_skill_generator.py` (647 LOC)

**What it protects:**
- YAML frontmatter parsing and skill name extraction (lines 135–148)
- Content validation: frontmatter, body presence, name format (lines 150–198)
- Node routing: filters SKILL gaps, preserves state, routes to generate_agent (lines 205–416)
- Repair path: first attempt success/fail, repair success/fail, LLM exception handling (lines 424–529)
- Fix 5 forced_name enforcement with edge cases (all-special-char queries, lines 585–611)
- Lazy import caching for validate_skill module (lines 619–647)

**Whether protection is live:**
- All VITAL tests pass the current source. Repair paths are well-covered.
- Edge case: skill_name fallback to forced_name when frontmatter lacks name (lines 531–559) is properly tested.

**Structural issues:**
- **schema_context not exercised:** same as MCP_TOOL.
- **Prompt over-specification:** test_task_context_forwarded (lines 561–583) asserts custom_context string in prompt.
- **pip_packages not tested:** skill generator does not output pip_packages (only description), but MCP_TOOL tests should cover this for that generator.

---

### `test_agent_generator.py` (944 LOC)

**What it protects:**
- Validation logic for plain agents and deep agents (lines 76–217)
- Validation logic for lead-with-subagents (lines 744–777)
- Node routing: filters AGENT gaps, selects mode (single → standalone, multi → lead), fallback on lead failure (lines 225–407)
- Repair path for single agents (lines 478–610)
- Lead-with-subagents mode selection (lines 785–944) ✓ **Phase 3.5b well covered**
- Deep agent skill disk write and frontmatter rewriting (lines 662–698)

**Whether protection is live:**
- All VITAL tests pass. Phase 3.5b lead-with-subagents routing is **thoroughly tested**: mode selection (1 vs ≥2 gaps), fallback on lead failure, subagent_gap_queries merging.

**Structural issues:**
- **_generate_lead_with_subagents() not directly unit-tested:** only tested via node routing. Return value shape (subagents list, frontmatter rewrite) is not verified in isolation.
- **schema_context not exercised:** same as other generators.
- **Prompt over-specification:** test_task_context_forwarded (lines 612–631), test_repair_prompt_includes_validation (lines 633–660).

---

## Aggregate Counts by Classification

| Classification  | Count | Examples |
|-----------------|-------|----------|
| **VITAL**       | 22    | Routing filters, repair paths, mode selection, Fix 5 enforcement |
| **OK**          | 28    | State preservation, edge cases, error preservation, parallelization |
| **WEAK**        | 7     | schema_context, pip_packages, prompt assertions without semantic meaning |
| **OVER-SPECIFIED** | 4  | Prompt substring checks, exact validation error text |
| **VESTIGIAL**   | 0     | No removed features detected |
| **DUPLICATE**   | 0     | No redundant test scenarios |

**Total:** ~61 meaningful tests (helper/schema tests not counted).

---

## Recommendations

### Immediate (Pre-B1)

1. **Augment schema_context coverage:**
   - Add one test per generator that passes non-empty schema_context to the generator function and verifies it appears in the LLM prompt.
   - File: All three test files.
   - Impact: Verify Phase 3.5c data-flow contract is not broken by future prompt changes.

2. **Direct _generate_lead_with_subagents() unit tests:**
   - Create `TestGenerateLeadWithSubagents` class (parallel to TestGenerateSingleAgent).
   - Test: happy path, repair pass, error handling, subagent_gap_queries merging, frontmatter name rewrite.
   - File: `test_agent_generator.py`.
   - Impact: Phase 3.5b logic is currently only tested indirectly via node routing.

3. **Loosen prompt assertions:**
   - Replace substring checks in test_task_context_forwarded (MCP 717–736, SKILL 561–583, AGENT 612–631) with looser assertions (e.g., prompt length increase, presence of key tokens rather than exact strings).
   - Impact: Tests remain VITAL but less brittle to prompt rewording.

### Deferred to B1

4. **input_schema / output_schema verification:**
   - B1 will wire AST extraction; defer unit test coverage until then.
   - Mark as "wait-for-B1" in test comments if added now.

5. **pip_packages assertion:**
   - MCP_TOOL tests should assert non-empty pip_packages from mock LLM response is preserved in artifact.
   - Can be added now with a simple mock update.

### Optional Cleanup

6. **Consolidate mode-selection tests:**
   - TestLeadWithSubagentsModeSelection (AGENT 785–944) could be condensed, but current tests are clear and valuable. Leave as-is.

---

## Summary

**Overall Health:** GOOD ✓

- All critical paths (routing, repair, mode selection, Fix 5 forced_name, Phase 3.5b lead-with-subagents) are **actively tested** and pass current source.
- **No regressions detected** from Phase 3.5a–3.5c drift.
- **Main gaps:** Phase 3.5c schema_context is untested; _generate_lead_with_subagents is only tested indirectly; prompt assertions are over-specified.

**Risk Summary:**
- **Low:** Fix 5 and Phase 3.5b are well protected.
- **Medium:** schema_context and lead-with-subagents internal logic (subagent merging, frontmatter rewrite) lack direct coverage.
- **Low-Medium:** Prompt assertions are brittle but not broken; can be tightened incrementally.

**Block B1?** No. Tests provide sufficient confidence for production use. Augmentations recommended but not blocking.

