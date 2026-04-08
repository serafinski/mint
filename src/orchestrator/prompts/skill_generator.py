"""Prompt template for the skill generator node."""

SKILL_GENERATOR_PROMPT = """\
You are a skill generator for a multi-agent system. Your task is to generate a \
SKILL.md file that teaches an AI agent how to perform a specific capability.

## Task Context

{task_context}

## Capability to Generate

Description: {description}
Search query that found no match: {query}

## SKILL.md Format Rules

The file MUST:
1. Start with YAML frontmatter between `---` delimiters (the very first line must be `---`)
2. Have a `name` field: hyphen-case (lowercase letters, digits, hyphens only), max 64 characters, \
no leading/trailing/consecutive hyphens
3. Have a `description` field: max 1024 characters, no angle brackets (< or >)
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

## Instructions

Generate a SKILL.md file for the capability described above. The skill should:
- Provide clear, actionable guidance for an AI agent
- Include code examples where relevant
- Be specific to the described capability, not generic
- Follow the exact format rules above

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

Output ONLY the SKILL.md content. Do not wrap it in markdown code fences. \
Start directly with `---` on the first line.
"""

SKILL_REPAIR_PROMPT = """\
The SKILL.md you generated failed validation with this error:

{validation_error}

## Original Request

Description: {gap_description}
Search query: {gap_query}
Task context: {task_context}

Here is the SKILL.md content you produced:

{skill_content}

Please fix the SKILL.md to pass validation. Remember:
- First line must be exactly `---`
- YAML frontmatter must have `name` (hyphen-case, max 64 chars) and `description` \
(max 1024 chars, no angle brackets)
- Only allowed keys: `name`, `description`, `license`, `compatibility`, `allowed-tools`, `metadata`
- Content after the closing `---` must be non-empty

Output ONLY the corrected SKILL.md content. Start directly with `---` on the first line.
"""
