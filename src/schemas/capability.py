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

    model_config = {"frozen": False, "extra": "forbid"}
