from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class CapabilityType(StrEnum):
    """Types of capabilities the system can generate and manage."""

    MCP_TOOL = "mcp_tool"
    SKILL = "skill"
    AGENT = "agent"


class CapabilityDescriptor(BaseModel):
    """Metadata for a registered capability in the skill registry."""

    name: str = Field(..., description="Unique capability name")
    description: str = Field(..., description="What this capability does")
    capability_type: CapabilityType = Field(..., description="Kind of capability")
    version: int = Field(default=1, description="Schema version for this capability")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    input_schema: dict = Field(
        default_factory=dict, description="JSON Schema for capability inputs"
    )
    output_schema: dict = Field(
        default_factory=dict, description="JSON Schema for capability outputs"
    )
    tags: list[str] = Field(
        default_factory=list, description="Searchable tags for hierarchical matching"
    )
    domain: str = Field(
        default="general", description="Domain category for hierarchical embeddings"
    )
    is_generic: bool = Field(
        default=False,
        description=(
            "True if this capability has no hardcoded business vocabulary "
            "and should bypass the domain filter at gap detection time. "
            "Set by the evolutionary taxonomy (B1 Step 3.5f). Generic caps "
            "(e.g. parse_csv_and_return_stats, fibonacci) serve any "
            "business sub-domain regardless of the query's inferred domain."
        ),
    )
    is_active: bool = Field(
        default=True, description="Whether capability is active in registry"
    )
    definition_token_cost: int = Field(
        default=0,
        ge=0,
        description=(
            "Static token count of the capability's definition string as the "
            "proposal's Pillar 3 M3 metric defines it — the MCP tool JSON "
            "schema, the SKILL.md body, or the agent's system prompt. "
            "Computed once at registration time by tokenizing the serialized "
            "definition with tiktoken. Reported side-by-side with the runtime "
            "per-invocation cost used by U(c); never feeds into the optimizer "
            "formula. Step 18.6."
        ),
    )
    metadata: dict[str, str] = Field(
        default_factory=dict, description="Additional key-value metadata"
    )

    model_config = {"frozen": False, "extra": "forbid"}
