# Pydantic Schemas Skill

## When to Use
Use this skill whenever creating or modifying state schemas in `src/schemas/`.

## Conventions

### All inter-node state uses Pydantic models
Never pass raw dicts or untyped state between LangGraph nodes. Every piece of state flowing through the pipeline must be a Pydantic `BaseModel` defined in `src/schemas/`.

### Schema location
All schemas live in `src/schemas/`. Other modules import from there. Never define ad-hoc models in orchestrator, verification, or optimizer code.

### Standard patterns

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

### Rules
1. Always use `Field(...)` with descriptions for required fields
2. Use `Field(default=...)` or `Field(default_factory=...)` for optional fields
3. Set `extra = "forbid"` to catch typos and unexpected data
4. Use `str` enums for fields that have a fixed set of values
5. Every schema needs a docstring explaining its purpose
6. Write unit tests that verify validation rejects bad data, not just that good data passes

### Extending schemas
When adding fields to an existing schema:
- Add with a default value so existing serialized data still loads
- Update the version if the change is breaking
- Update all tests that construct the model
