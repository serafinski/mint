"""Prompt template for the skill generator node."""

SKILL_GENERATOR_PROMPT = """\
You are a skill generator for a multi-agent system. Your task is to generate \
a SKILL.md file that teaches an AI agent how to perform ONE STEP of a larger \
workflow. Classification has decided this specific step is best implemented \
as a skill (packaged procedural knowledge with a prescribed workflow).

## Full Task Context (for reference only)

This is the overall task the step belongs to. Other steps of this task are \
handled by separate artifacts — the skill you produce covers only the single \
step described below, not the whole task.

{task_context}

## The Specific Step to Implement

Your skill teaches an agent how to perform this specific step:

    {gap_description}

Focus on exactly this step. If the workflow has other steps (parsing, \
aggregation, reporting, investigation, etc.) those are handled by separate \
skills or tools — do not try to cover them here. The skill should guide the \
agent through the sub-steps of implementing THIS step.

CRITICAL: Do NOT produce a skill that solves the entire task. The skill must \
guide the agent through THIS step only. If the step says "filter products \
below threshold", the skill must NOT also compute reorder quantities or \
aggregate costs — those are later steps handled by other artifacts. \
Return only the intermediate data this step produces.

## SKILL.md Format Rules

The file MUST:
1. Start with YAML frontmatter between `---` delimiters (the very first line must be `---`)
2. Have a `name` field: hyphen-case (lowercase letters, digits, hyphens only), max 64 characters, \
no leading/trailing/consecutive hyphens
3. Have a `description` field: max 1024 characters, no angle brackets (< or >); \
use plain identifiers like `metric_function` instead of `<metric>_<function>`
4. Only use allowed frontmatter keys: `name`, `description`, `license`, `compatibility`, \
`allowed-tools`, `metadata`
5. Have non-empty content after the frontmatter closing `---`

## Complete Example

Here is a complete, valid SKILL.md file (this is exactly the format your output should match):

---
name: data-validation
description: "Patterns and conventions for validating data inputs in pipeline \
stages. Use this skill when building data ingestion, transformation, or quality \
check components that need to enforce schema constraints and handle malformed data."
---

# Data Validation

Validate all data at system boundaries using schema-driven checks. Internal \
pipeline stages trust validated data and skip redundant checks.

## Schema-Driven Validation

Define expected schemas using Pydantic models. Every ingestion endpoint \
validates inputs against the schema before processing:

```python
from pydantic import BaseModel, Field

class SalesRecord(BaseModel):
    date: str = Field(..., description="ISO-8601 date string")
    amount: float = Field(..., ge=0, description="Transaction amount")
    currency: str = Field(default="USD", description="ISO currency code")

    model_config = {{"extra": "forbid"}}
```

## Error Handling

When validation fails, collect all errors rather than failing on the first one. \
Return a structured error report so upstream callers can fix and retry.

## Common Patterns

- Use `model_validate()` for dict inputs, `model_validate_json()` for raw JSON
- Set `extra="forbid"` to catch unexpected fields
- Use `Field(ge=0)` and similar constraints for numeric bounds
- Validate at ingestion, trust internally

The SKILL.md `name` frontmatter field MUST be exactly `{forced_name}` \
(hyphen-case). Do not choose a different name.

## Body Length Guideline

Per the Agent Skills specification, the SKILL.md body (everything after the \
closing `---`) should be under 500 lines and under 5,000 tokens. Keep the \
skill focused and concise. If detailed reference material or long examples \
are needed, mention that they should go in `references/` files — but do NOT \
generate those files yourself; only generate the SKILL.md.

## Instructions

Generate a SKILL.md file for the capability described above. The skill should:
- Provide clear, actionable guidance for an AI agent
- Include code examples where relevant
- Be specific to the described capability, not generic
- Follow the exact format rules above
- Stay under 500 lines of body content

## Important: Filesystem Isolation

The agent executing this skill has two SEPARATE filesystems:
- **sandbox_execute** runs Python in an isolated Docker container. Data must \
be embedded directly in the Python code string (e.g., write CSV content to \
/tmp/ files using Python's open()/write() INSIDE the code string).
- **write_file** writes to the agent's in-memory workspace (for notes and \
intermediate reasoning). Files written with write_file are NOT accessible \
inside sandbox_execute.

When writing instructions that involve data processing, tell the agent to \
embed input data from the user message directly into the sandbox_execute \
code. Do NOT instruct the agent to use write_file to pass data to the sandbox.

## File Output (last step only)

If this step is the LAST step of a workflow and the task explicitly requests a \
file output format (CSV, Excel, Markdown, etc.), the skill should instruct the \
agent to produce BOTH:
1. A JSON dict with metadata (e.g. ``{{"output_file": "output.csv", "row_count": 42}}``)
2. The file content embedded in the dict under a ``_output_file_content`` key \
(base64-encoded for binary formats, plain text for text formats) and the \
filename under ``_output_file_name`` (e.g. ``"output.csv"``).
If the task does NOT ask for a file format, return a plain JSON dict as usual.

{schema_context}

Output ONLY the SKILL.md content. Do not wrap it in markdown code fences. \
Start directly with `---` on the first line.
"""

SKILL_REPAIR_PROMPT = """\
The SKILL.md you generated failed validation with this error:

{validation_error}

## Full Task Context (reference only)

{task_context}

## The Specific Step to Implement

{gap_description}

Here is the SKILL.md content you produced:

{skill_content}

Please fix the SKILL.md to pass validation. Remember:
- First line must be exactly `---`
- YAML frontmatter must have `name` (hyphen-case, max 64 chars) and `description` \
(max 1024 chars, no angle brackets; use `metric_function` instead of `<metric>_<function>`)
- Only allowed keys: `name`, `description`, `license`, `compatibility`, `allowed-tools`, `metadata`
- Content after the closing `---` must be non-empty

Output ONLY the corrected SKILL.md content. Start directly with `---` on the first line.
"""
