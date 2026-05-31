from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    """Severity levels for verification findings."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class VerificationFinding(BaseModel):
    """A single finding from a verification tier."""

    tier: int = Field(..., ge=0, le=3, description="Verification tier that produced this finding")
    category: str = Field(..., description="Finding category (e.g. 'security', 'correctness')")
    severity: Severity = Field(..., description="Severity level of the finding")
    message: str = Field(..., description="Human-readable description of the finding")
    location: str | None = Field(
        default=None, description="Code location or context where the finding applies"
    )

    model_config = {"extra": "forbid"}


class VerificationReport(BaseModel):
    """Aggregated verification results for a capability."""

    capability_name: str = Field(..., description="Name of the verified capability")
    findings: list[VerificationFinding] = Field(
        ..., description="All findings from verification tiers"
    )
    overall_passed: bool = Field(..., description="Whether the capability passed verification")
    tier_reached: int = Field(
        ..., ge=0, le=3, description="Highest verification tier that was executed"
    )
    verified_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the verification was performed",
    )
    artifact_hash: str = Field(
        default="",
        description=(
            "SHA-256 of the verified artifact's content "
            "(source_code + skill_content + agent_config). "
            "Used by retry logic to skip only unchanged artifacts. "
            "Empty values force re-verification for legacy reports."
        ),
    )

    model_config = {"extra": "forbid"}
