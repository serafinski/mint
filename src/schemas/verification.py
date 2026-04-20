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
    # Security audit finding #26 (2026-04-20): content hash stamped by
    # ``src.verification.gate.verify`` so the retry loop's
    # ``already_passed`` skip can compare artifact content, not just
    # capability_name. Pre-fix the skip used name-identity only —
    # regenerated artifacts with the same name but modified content
    # inherited the old PASS report and bypassed the three-tier gate.
    # Empty string on pre-fix rows (migrate-by-default).
    artifact_hash: str = Field(
        default="",
        description=(
            "SHA-256 of the verified artifact's content "
            "(source_code + skill_content + agent_config). "
            "Used by the retry-loop skip to detect changed artifacts."
        ),
    )

    model_config = {"extra": "forbid"}
