"""Evaluation schemas for Iteration 5 end-to-end testing.

Separate from the operational InvocationRecord — evaluation records track
experimental outcomes across the five baseline conditions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from src.schemas.capability import CapabilityType


class BaselineCondition(StrEnum):
    """The evaluation conditions compared in the thesis.

    The first four are the proposal's four ablation baselines (§Evaluation
    Strategy). ``LONGITUDINAL`` is added for ``scripts/run_longitudinal.py``
    — it's not one of the four ablation conditions, it's a separate
    sequencing protocol (one snapshot restore, 40 sequential tasks, shared
    registry + metrics — exercises Pillar 3 convergence under accumulated
    history). Analysis pipelines MUST filter by condition explicitly when
    computing per-baseline metrics (see ``analyze_results.py``'s exclusion
    of ``static`` in classification accuracy for analogous reasoning).
    """

    FULL = "full"
    NO_VERIFICATION = "no_verification"
    NO_OPTIMIZATION = "no_optimization"
    STATIC = "static"
    LONGITUDINAL = "longitudinal"


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
    CHAIN_STEP_FAILED = "chain_step_failed"
    # Step 13 (2026-04-15): distinguish an AGENT execution timeout from
    # a generic chain step failure. The harness sets this when
    # ChainResult.failed_step_reason == "agent_execution_timeout", which
    # is populated from the thread-local meta recorded by
    # execute_agent_artifact on the concurrent.futures TimeoutError path.
    AGENT_EXECUTION_TIMEOUT = "agent_execution_timeout"
    AGENT_EXECUTION_ERROR = "agent_execution_error"


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
    capability_type: CapabilityType | None = Field(
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

    # --- Decomposition/registry analysis (Iter 6 task-level classification) ---
    required_capability_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of required_capabilities produced by decomposition. "
            "Useful for analyzing decomposition splitting behavior."
        ),
    )
    matched_in_registry_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of required_capabilities matched in the registry. "
            "If equal to required_capability_count, classification was skipped."
        ),
    )

    # --- Compositional execution (Stage 1: defaults; Stage 3: real values) ---
    chain_length: int = Field(
        default=1,
        ge=1,
        description="Number of steps in the executed compositional chain (1 = single artifact)",
    )
    chain_success: bool = Field(
        default=False,
        description=(
            "Did the full chain execute successfully? "
            "Equals (gate_passed AND output_in_range) for chain_length=1."
        ),
    )
    matched_and_executed_count: int = Field(
        default=0,
        ge=0,
        description="How many registry-matched capabilities were actually executed in the chain.",
    )
    generated_artifact_count: int = Field(
        default=0,
        ge=0,
        description="How many newly-generated artifacts were executed in the chain.",
    )
    capability_types: list[str] = Field(
        default_factory=list,
        description="Capability types executed in the chain, in order.",
    )
    failed_step_index: int | None = Field(
        default=None,
        description="Zero-based index of the step that failed (None on success).",
    )
    failed_step_name: str | None = Field(
        default=None,
        description="Name of the failed step (None on success).",
    )

    # --- Output contract (Step 14.6) ---
    # Records the result of comparing the pipeline's final output against
    # the task's authored OutputContract (`required_keys` + `required_files`).
    # Independent of `output_in_range` (which checks value correctness via the
    # bespoke validator); these fields check shape correctness via a pure set
    # comparison. A run can pass `output_in_range` but still fail the contract
    # check if the validator is lenient about shape, or vice versa.
    output_shape_mismatch: bool = Field(
        default=False,
        description=(
            "True iff the final output dict was missing one or more "
            "required_keys, OR the chain output_files mapping was missing "
            "one or more required_files declared in the task's "
            "OutputContract. Default False covers legacy rows from before "
            "Step 14.6 landed and runs where no contract was declared."
        ),
    )
    output_shape_missing_keys: list[str] = Field(
        default_factory=list,
        description=(
            "Required dict keys that were absent from the final output. "
            "Empty list when the contract is satisfied OR when no contract "
            "was declared. Sorted for stable diffing."
        ),
    )
    output_shape_missing_files: list[str] = Field(
        default_factory=list,
        description=(
            "Required filenames that were absent from chain_output_files. "
            "Empty list when the contract is satisfied OR when no file "
            "outputs were declared. Sorted for stable diffing."
        ),
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
