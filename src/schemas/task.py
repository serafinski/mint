from pydantic import BaseModel, Field

from src.schemas.capability import CapabilityDescriptor


class GapItem(BaseModel):
    """A capability that was needed but not found in the registry."""

    description: str = Field(..., description="What capability is missing")
    query: str = Field(..., description="The search query that produced no match")

    model_config = {"extra": "forbid"}


class MatchItem(BaseModel):
    """A capability found in the registry that matches a requirement."""

    capability: CapabilityDescriptor = Field(..., description="The matched capability")
    score: float = Field(..., description="Similarity score from registry search")

    model_config = {"extra": "forbid"}


class TaskState(BaseModel):
    """Minimal pipeline state for Iteration 0: intake → gap_detection → report."""

    task_description: str = Field(..., description="Natural-language task from the user")
    required_capabilities: list[str] = Field(
        default_factory=list,
        description="Capability queries extracted from the task",
    )
    matches: list[MatchItem] = Field(
        default_factory=list,
        description="Capabilities found in the registry",
    )
    gaps: list[GapItem] = Field(
        default_factory=list,
        description="Capabilities needed but not found",
    )

    model_config = {"extra": "forbid"}
