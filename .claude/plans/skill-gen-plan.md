# Skill Generator Implementation Plan

## Overview

Implement `src/orchestrator/generators/skill.py` — a plain LangGraph node that generates DeepAgents SKILL.md files from classified gaps, validates them with `quick_validate.py`, and writes them to `.deepagents/skills/<name>/`.

## Files to Create

1. `src/orchestrator/prompts/skill_generator.py` — Prompt template with few-shot example
2. `src/orchestrator/generators/skill.py` — Generator node implementation
3. `tests/unit/test_skill_generator.py` — Unit tests with mocked LLM

## Design

### Prompt (`src/orchestrator/prompts/skill_generator.py`)

- `SKILL_GENERATOR_PROMPT` — system-level prompt instructing the LLM to generate a SKILL.md
- Includes all format rules from `quick_validate.py` (frontmatter format, allowed keys, naming rules, length limits)
- Contains a **complete few-shot example** based on the `pydantic-schemas` skill from `.claude/skills/` (compact, demonstrates the pattern well)
- Takes `{description}`, `{query}`, `{task_context}` placeholders

### Generator Node (`src/orchestrator/generators/skill.py`)

Structure follows `classifier.py` pattern:

```
_skill_llm = None  (lazy singleton)

def _get_llm() -> ChatModel
def _validate_skill_content(content: str, skill_name: str, base_path: Path) -> tuple[bool, str]
def _generate_single_skill(gap: ClassifiedGap, task_context: str) -> GeneratedArtifact | None
def generate_skill(state: TaskState) -> Command
```

**`_validate_skill_content`**:
- Writes content to a temp directory matching the expected structure (`<tmp>/<name>/SKILL.md`)
- Calls `validate_skill()` from `tools/quick_validate.py` (import it directly as a Python function, not subprocess)
- Returns (is_valid, message)

**`_generate_single_skill`**:
1. Call LLM with the prompt
2. Extract SKILL.md content from response (strip markdown code fences if present)
3. Derive `skill_name` from frontmatter `name` field
4. Validate via `_validate_skill_content`
5. If invalid: **repair pass** — send original content + validation error back to LLM once
6. If still invalid: log to LangFuse, return None
7. If valid: write to `.deepagents/skills/<name>/SKILL.md`, return `GeneratedArtifact`

**`generate_skill`** (the LangGraph node):
1. Filter `state.classified_gaps` for `CapabilityType.SKILL`
2. For each skill gap, call `_generate_single_skill`
3. Collect artifacts and errors
4. Return `Command(update={"generated_artifacts": artifacts, "errors": errors}, goto="next_node")`

### Key Decisions

- **Import `validate_skill` directly** from `tools/quick_validate.py` rather than subprocess — simpler, faster, testable
- **Lazy LLM singleton** — same pattern as `classifier.py`
- **LangFuse callbacks** — wrap in try/except like classifier does (graceful if LangFuse not configured)
- **Skill name extraction** — parse YAML frontmatter from the generated content to get the `name` field, use it as directory name
- **Output path**: `<project_root>/.deepagents/skills/<name>/SKILL.md`
- **`goto="next_node"`** — placeholder; Phase C wiring will set the actual next node

### Tests (`tests/unit/test_skill_generator.py`)

All unit tests mock the LLM. Use `tmp_path` for file I/O. Tests:

1. **`test_generate_skill_valid_output`** — Mock LLM returns valid SKILL.md, verify `GeneratedArtifact` is produced with correct fields
2. **`test_generate_skill_writes_to_disk`** — Verify SKILL.md is written to `.deepagents/skills/<name>/SKILL.md` (use `tmp_path` via monkeypatch)
3. **`test_generate_skill_passes_quick_validate`** — The mocked valid output passes `validate_skill()`
4. **`test_generate_skill_repair_pass`** — Mock LLM returns invalid frontmatter first, then valid on retry. Verify repair works.
5. **`test_generate_skill_llm_failure`** — Mock LLM raises exception. Verify error is captured and no artifact produced.
6. **`test_generate_skill_skips_non_skill_gaps`** — Gaps with `MCP_TOOL` or `AGENT` type are skipped
7. **`test_generate_skill_node_returns_command`** — Verify the node returns a `Command` with correct update dict

### Validation Import

To import `validate_skill` from `tools/quick_validate.py`, I'll add the `tools/` directory to `sys.path` at import time within the module, or use `importlib` to load it by file path. The cleaner approach is `importlib.util.spec_from_file_location` since `tools/` is not a Python package.
