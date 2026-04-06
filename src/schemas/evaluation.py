"""Evaluation schemas for Iteration 5 end-to-end testing.

Separate from the operational InvocationRecord — evaluation records track
experimental outcomes across the five baseline conditions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class BaselineCondition(StrEnum):
    """The evaluation conditions compared in the thesis."""

    FULL = "full"
    NO_VERIFICATION = "no_verification"
    NO_OPTIMIZATION = "no_optimization"
    STATIC = "static"


class TaskTier(StrEnum):
    """Difficulty tier of a testbed task."""

    TRIVIAL = "trivial"
    MODERATE = "moderate"
    COMPLEX = "complex"


class FailureReason(StrEnum):
    """Why a run failed (non-nullable; 'none' means success)."""

    NONE = "none"
    GENERATION_FAILED = "generation_failed"
    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"
    OUTPUT_REJECTED = "output_rejected"
    NO_SOURCE_CODE = "no_source_code"


class HumanLabel(StrEnum):
    """Manual audit label populated in Iteration 6."""

    CORRECT = "correct"
    CONTAMINATED = "contaminated"
    PARTIAL = "partial"


class EvaluationRecord(BaseModel):
    """One pipeline run on one task under one baseline condition.

    Written by the evaluation harness; read by the Iteration 6 analysis.
    """

    # --- Experimental identifiers ---
    run_id: str = Field(..., description="Groups all tasks in one experimental run")
    condition: BaselineCondition = Field(
        ..., description="Baseline condition"
    )
    task_id: str = Field(..., description="Testbed task identifier (e.g. 'T01')")
    task_tier: TaskTier = Field(
        ..., description="Difficulty tier of the task"
    )

    # --- Pipeline outcome ---
    attempt_count: int = Field(
        ..., ge=0, description="Number of generation+verification cycles"
    )
    capability_type: Literal["mcp_tool", "skill", "agent"] | None = Field(
        None, description="Type of generated capability; None for static baseline"
    )
    verification_tier_reached: int | None = Field(
        None,
        ge=0,
        le=3,
        description="Highest verification tier executed; None when bypassed",
    )
    gate_passed: bool = Field(..., description="Did the verification gate accept the artifact?")
    output_in_range: bool = Field(
        ..., description="Did the capability output satisfy the task ground-truth validator?"
    )

    # --- Composite success = gate_passed AND output_in_range ---

    # --- Metrics ---
    token_cost: int = Field(
        ..., ge=0, description="Total tokens consumed (0 = not tracked; use LangFuse)"
    )
    attempt_latency_ms: int = Field(
        ..., ge=0, description="Wall-clock time for all generation attempts (secondary metric)"
    )
    failure_reason: FailureReason = Field(
        ..., description="Why the run failed; 'none' on success"
    )

    # --- Registry state ---
    registry_snapshot_id: str = Field(
        ..., description="Identifies the clean registry state at run start"
    )

    # --- Cross-references ---
    invocation_record_id: str | None = Field(
        None, description="FK to operational invocations table (populated when available)"
    )

    # --- Reproducibility ---
    seed: int = Field(..., description="Random seed used for task ordering in this run")
    is_pilot: bool = Field(
        default=False, description="True for pilot runs used to calibrate task difficulty"
    )

    # --- Manual audit (Iteration 6) ---
    human_label: HumanLabel | None = Field(
        None, description="Manually assigned correctness label; populated in Iteration 6 audit"
    )

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this record was written",
    )

    model_config = {"extra": "forbid"}

    @property
    def success(self) -> bool:
        """Composite success: gate accepted AND output is correct."""
        return self.gate_passed and self.output_in_range
