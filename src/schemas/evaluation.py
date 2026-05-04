"""Schemas for persisted evaluation records.

Separate from the operational InvocationRecord; evaluation records track
task-level outcomes across the ablation, longitudinal, and recurrence
evaluation runs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from src.schemas.capability import CapabilityType


class BaselineCondition(StrEnum):
    """Evaluation conditions persisted in ``evaluation_runs``.

    The first four values are the ablation conditions. ``LONGITUDINAL`` is
    a separate sequential protocol with one registry and metrics store
    shared across task executions. Analyzers filter by condition explicitly
    because the ablation, static, and longitudinal populations answer
    different questions.
    """

    FULL = "full"
    NO_VERIFICATION = "no_verification"
    NO_OPTIMIZATION = "no_optimization"
    STATIC = "static"
    LONGITUDINAL = "longitudinal"
    # Reserved for the deferred distribution-shift protocol. Keeping a
    # distinct label prevents shifted rows from being confused with the
    # plain longitudinal condition if historical or future DBs are read.
    LONGITUDINAL_SHIFT = "longitudinal_shift"


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
    # Distinguish AGENT execution failures from generic chain-step failures.
    AGENT_EXECUTION_TIMEOUT = "agent_execution_timeout"
    AGENT_EXECUTION_ERROR = "agent_execution_error"
    # OutputContract failures are first-class campaign outcomes because they
    # indicate shape mismatch even when a value validator is permissive.
    OUTPUT_SHAPE_MISMATCH = "output_shape_mismatch"
    # Per-task watchdog aborts bound the whole pipeline invocation, not only
    # AGENT artifact execution.
    WATCHDOG_ABORT = "watchdog_abort"


class HumanLabel(StrEnum):
    """Optional manual audit label for deferred contamination review."""

    CORRECT = "correct"
    CONTAMINATED = "contaminated"
    PARTIAL = "partial"


class GeneratedArtifactSnapshot(BaseModel):
    """Record-write-time snapshot of a generated capability's source.

    Persisted inside ``EvaluationRecord.generated_artifact_sources`` so
    manual contamination review and provenance checks can read full source
    bodies from the DB without depending on registry state that may be
    snapshot-restored between runs.

    The ``source_body`` is polymorphic by ``capability_type``:

    * ``MCP_TOOL``: raw Python ``source_code``.
    * ``SKILL``: full ``skill_content`` (SKILL.md body incl. YAML).
    * ``AGENT``: ``json.dumps(agent_config)``; keeps system_prompt
      plus any subagent configs the parent delegates to.

    Not a place for truncation. Manual source review needs the complete
    artifact body to catch hardcoded literals, silent try/except blocks, and
    task-phrasing contamination that automated scans can miss.
    """

    capability_name: str = Field(..., description="Registry name of the generated capability")
    capability_type: CapabilityType = Field(
        ..., description="MCP_TOOL / SKILL / AGENT; discriminates source_body semantics"
    )
    source_body: str = Field(
        ...,
        description=(
            "Full source body. Polymorphic by capability_type: Python source "
            "(MCP_TOOL), SKILL.md text (SKILL), or json.dumps(agent_config) "
            "(AGENT). Never truncated; manual audit requires complete inspection."
        ),
    )
    domain: str | None = Field(
        default=None, description="Taxonomy-assigned domain for audit context"
    )

    model_config = {"extra": "forbid"}


class EvaluationRecord(BaseModel):
    """One pipeline run on one task under one baseline condition.

    Written by the evaluation harness and read by the Chapter 6 analyzers.
    """

    # --- Experimental identifiers ---
    run_id: str = Field(..., description="Groups all tasks in one experimental run")
    condition: BaselineCondition = Field(
        ..., description="Baseline condition"
    )
    task_id: str = Field(..., description="Testbed task identifier (e.g. 'T01')")
    task_family_id: str | None = Field(
        default=None,
        description="Stable task-family identifier (e.g. 'T11') across breadth/recurrence",
    )
    task_instance_id: str | None = Field(
        default=None,
        description=(
            "Concrete task instance identifier (e.g. 'T11.v02'); equals "
            "task_id for breadth"
        ),
    )
    variant_id: str | None = Field(
        default=None,
        description="Variant identifier within a task family (e.g. 'v02'); None for breadth rows",
    )
    benchmark_track: str = Field(
        default="breadth",
        description="Benchmark track label (typically 'breadth' or 'recurrence')",
    )
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

    # --- Decomposition/registry analysis ---
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

    # --- Compositional execution ---
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
    failed_step_reason: str | None = Field(
        default=None,
        description=(
            "Free-text reason string produced by the compositional "
            "executor when a chain step fails, specifically the "
            "'<type> output missing declared schema keys [...]' message "
            "from the AGENT/SKILL output_schema "
            "compliance check, or 'agent_execution_timeout' / "
            "'agent_execution_error' for AGENT-specific failure modes. "
            "Persisted so post-hoc analyzers can distinguish "
            "compression-loss-at-handoff failures from generic chain "
            "failures; `failure_reason` enum flattens all three into "
            "'chain_step_failed' by necessity, but the free-text string "
            "carries the signal needed to classify the T3 "
            "aggressive-compression failure mode."
        ),
    )

    # --- Output contract ---
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
            "output contracts were persisted and runs where no contract "
            "was declared."
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

    # --- Generated artifact source persistence ---
    generated_artifact_sources: list[GeneratedArtifactSnapshot] = Field(
        default_factory=list,
        description=(
            "Record-write-time snapshots of every generated capability's "
            "source body. Enables manual contamination audit and provenance "
            "checks without depending on snapshot-isolated registry state. "
            "Empty list for runs that generated no artifacts, legacy rows "
            "without persisted snapshots, and runs that failed before "
            "generation."
        ),
    )

    # --- Observability cross-reference ---
    langfuse_trace_id: str | None = Field(
        default=None,
        description=(
            "LangFuse root trace ID captured at record write time. Enables "
            "jump-navigation from DuckDB eval row to LangFuse trace UI without "
            "a timestamp/session join. Read from ``handler.last_trace_id`` "
            "after pipeline.invoke completes; None when LangFuse is disabled "
            "or handler lookup failed. Does not affect DuckDB-native metric "
            "computation; pure convenience for debugging."
        ),
    )

    # --- Reproducibility ---
    seed: int = Field(..., description="Random seed used for task ordering in this run")
    is_pilot: bool = Field(
        default=False, description="True for pilot runs used to calibrate task difficulty"
    )

    # --- Manual audit ---
    human_label: HumanLabel | None = Field(
        None,
        description=(
            "Optional manually assigned correctness label. The manual "
            "contamination audit was deferred, but the field remains for "
            "compatibility and future review."
        ),
    )

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description=(
            "When this record was written. Set at task-END time by the "
            "default_factory; the EvaluationRecord(...) constructor is "
            "called after the task runner returns. Longitudinal analysis "
            "treats this as an end timestamp, so cycle attribution uses "
            "half-open intervals with the current-cycle boundary as the "
            "upper bound."
        ),
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _default_task_identity(self) -> EvaluationRecord:
        """Backfill breadth rows created before recurrence identity existed."""
        if self.task_family_id is None:
            self.task_family_id = self.task_id
        if self.task_instance_id is None:
            self.task_instance_id = self.task_id
        return self

    @classmethod
    def is_success(
        cls,
        *,
        gate_passed: bool,
        output_in_range: bool,
        output_shape_mismatch: bool = False,
    ) -> bool:
        """Canonical Chapter 6 M1 success predicate.

        Every site that reports M1 (DuckDB SQL predicate, LangFuse
        ``m1.success`` score, and this class's ``success`` property) should
        use this predicate so the stores do not diverge. A run succeeds only
        when the verification gate passed, the value validator accepted the
        output, and the output contract shape was satisfied.
        """
        return gate_passed and output_in_range and not output_shape_mismatch

    @property
    def success(self) -> bool:
        """Composite success: gate accepted AND output in range AND shape ok.

        Delegates to ``is_success`` so this property and every other M1
        consumer share one predicate.
        """
        return self.__class__.is_success(
            gate_passed=self.gate_passed,
            output_in_range=self.output_in_range,
            output_shape_mismatch=self.output_shape_mismatch,
        )
