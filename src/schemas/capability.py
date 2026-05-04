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
        default="general",
        description="Domain category used by taxonomy-aware registry retrieval",
    )
    is_generic: bool = Field(
        default=False,
        description=(
            "True if this capability has no hardcoded business vocabulary "
            "and should bypass the domain filter at gap detection time. "
            "Generic capabilities, such as CSV parsers or mathematical "
            "utilities, can serve any business sub-domain regardless of the "
            "query's inferred domain."
        ),
    )
    is_active: bool = Field(
        default=True, description="Whether capability is active in registry"
    )
    definition_token_cost: int = Field(
        default=0,
        ge=0,
        description=(
            "Static token count of the capability definition: the MCP tool "
            "JSON schema, SKILL.md body, or parent agent system prompt. "
            "Computed at registration time and used as the context-economy "
            "penalty term in utility scoring; reported separately from "
            "runtime per-invocation token cost."
        ),
    )
    pip_packages: list[str] = Field(
        default_factory=list,
        description=(
            "PyPI package names required by MCP_TOOL execution at runtime. "
            "Populated at generation time by the MCP tool generator from "
            "the LLM's GeneratedToolCode.pip_packages and persisted via "
            "ChromaDB metadata (JSON-encoded), so that registry-matched "
            "tools restore their dependency list at retrieval time. "
            "Empty for SKILL/AGENT capabilities."
        ),
    )
    metadata: dict[str, str] = Field(
        default_factory=dict, description="Additional key-value metadata"
    )

    model_config = {"frozen": False, "extra": "forbid"}
