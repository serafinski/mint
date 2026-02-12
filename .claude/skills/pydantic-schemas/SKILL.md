---
name: pydantic-schemas
description: "Enforce Pydantic schema conventions for all inter-node state in the JIT capability synthesis pipeline. Use this skill whenever creating, modifying, or reviewing state schemas in src/schemas/, whenever a new LangGraph node is being built that needs to define or consume state, or whenever someone mentions 'schema', 'state', 'Pydantic', 'BaseModel', 'TaskState', 'CapabilityDescriptor', 'VerificationReport', or 'GranularityMetrics'. Also trigger when code is passing raw dicts between LangGraph nodes — that's a sign this skill is needed."
---

# Pydantic Schemas

All state flowing between LangGraph nodes in this project uses Pydantic `BaseModel` classes defined in `src/schemas/`. This prevents subtle bugs from typos in dict keys, makes state self-documenting, and gives you validation for free.

## Schema Location

Every schema lives in `src/schemas/`. Other modules import from there. Never define ad-hoc models inline in orchestrator, verification, or optimizer code — if a node needs a new state shape, add it to `src/schemas/` first.

The reason for centralizing: when Agent Team teammates work in parallel on different modules, they all import from the same source of truth. If schemas lived inline, teammates would create incompatible versions.

## Standard Pattern

```python
from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional
from datetime import datetime


class CapabilityType(str, Enum):
    MCP_TOOL = "mcp_tool"
    SKILL = "skill"
    AGENT = "agent"


class CapabilityDescriptor(BaseModel):
    """Metadata for a registered capability."""
    name: str = Field(..., description="Unique capability name")
    description: str = Field(..., description="What this capability does")
    capability_type: CapabilityType
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    version: int = Field(default=1)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"frozen": False, "extra": "forbid"}
```

## Field Conventions

Use `Field(...)` with a `description` for every required field. This serves double duty: Pydantic uses it for validation error messages, and it documents intent for anyone reading the schema.

Use `Field(default=...)` or `Field(default_factory=...)` for optional fields. Explicit defaults make it clear what a "not provided" value looks like.

Set `extra = "forbid"` on every model. This catches typos immediately — if someone passes `capabilty_type` instead of `capability_type`, Pydantic raises instead of silently ignoring it.

Use `str` enums for fields with a fixed set of values. This gives you type safety and readable serialization (the JSON shows `"mcp_tool"` instead of `0`).

Every schema needs a docstring explaining its purpose. A model named `VerificationReport` is obvious; a model named `GateResult` is not.

## Testing Schemas

Write tests that verify validation rejects bad data, not just that good data passes. The interesting behavior is what happens at the edges:

```python
def test_capability_descriptor_rejects_extra_fields():
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            name="test",
            description="test",
            capability_type=CapabilityType.MCP_TOOL,
            unknown_field="oops",  # Should fail with extra="forbid"
        )

def test_capability_descriptor_rejects_invalid_type():
    with pytest.raises(ValueError):
        CapabilityDescriptor(
            name="test",
            description="test",
            capability_type="not_a_real_type",
        )
```

## Extending Existing Schemas

When adding fields to an existing schema, add them with a default value so existing serialized data still loads without breaking. If the change is breaking (removing a field, changing a type), bump the version.

Update all tests that construct the model — the test suite should fail loudly if a schema changes in a way that affects existing code.
