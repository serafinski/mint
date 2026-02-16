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
    is_active: bool = Field(
        default=True, description="Whether capability is active in registry"
    )
    metadata: dict[str, str] = Field(
        default_factory=dict, description="Additional key-value metadata"
    )

    model_config = {"frozen": False, "extra": "forbid"}
