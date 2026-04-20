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


class GeneratedArtifactSnapshot(BaseModel):
    """Record-write-time snapshot of a generated capability's source (Step 18.10).

    Persisted inside ``EvaluationRecord.generated_artifact_sources`` so the
    proposal §M2 manual contamination audit can read full source bodies
    from the DB without depending on the registry's snapshot-isolated
    state (which wipes generated capabilities between runs).

    The ``source_body`` is polymorphic by ``capability_type``:

    * ``MCP_TOOL``  → raw Python ``source_code``.
    * ``SKILL``     → full ``skill_content`` (SKILL.md body incl. YAML).
    * ``AGENT``     → ``json.dumps(agent_config)`` — keeps system_prompt
      plus any subagent configs the parent delegates to.

    Not a place for truncation. M2 audit requires human reading of the
    complete source to catch contamination patterns (hardcoded literals
    buried deep in a function, silent try/except blocks, mock-contamination
    keyed off task phrasing) that an automated scanner misses.
    """

    capability_name: str = Field(..., description="Registry name of the generated capability")
    capability_type: CapabilityType = Field(
        ..., description="MCP_TOOL / SKILL / AGENT — discriminates source_body semantics"
    )
    source_body: str = Field(
        ...,
        description=(
            "Full source body. Polymorphic by capability_type: Python source "
            "(MCP_TOOL), SKILL.md text (SKILL), or json.dumps(agent_config) "
            "(AGENT). Never truncated — M2 audit requires complete inspection."
        ),
    )
    domain: str | None = Field(
        default=None, description="Taxonomy-assigned domain for audit context"
    )

    model_config = {"extra": "forbid"}


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
    failed_step_reason: str | None = Field(
        default=None,
        description=(
            "Free-text reason string produced by the compositional "
            "executor when a chain step fails — specifically the "
            "'<type> output missing declared schema keys [...]' message "
            "from the §12 finding #12 AGENT/SKILL output_schema "
            "compliance check, or 'agent_execution_timeout' / "
            "'agent_execution_error' for AGENT-specific failure modes. "
            "Persisted (Step 13.8) so post-hoc analyzers can distinguish "
            "compression-loss-at-handoff failures from generic chain "
            "failures — `failure_reason` enum flattens all three into "
            "'chain_step_failed' by necessity, but the free-text string "
            "carries the signal needed to classify the T3 "
            "aggressive-compression failure mode."
        ),
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

    # --- M2 audit source persistence (Step 18.10 Option B) ---
    generated_artifact_sources: list[GeneratedArtifactSnapshot] = Field(
        default_factory=list,
        description=(
            "Record-write-time snapshots of every generated capability's "
            "source body. Enables proposal §M2 manual contamination audit "
            "post-eval without depending on the registry's snapshot-isolated "
            "state (restore_clean_snapshot wipes caps between runs). Empty "
            "list for runs that generated no artifacts (static, registry-"
            "match T01-T03), for legacy rows from before Step 18.10 landed, "
            "and — by design — for runs that failed before generation."
        ),
    )

    # --- Observability cross-reference (Step 13.7 Batch B6) ---
    langfuse_trace_id: str | None = Field(
        default=None,
        description=(
            "LangFuse root trace ID captured at record write time. Enables "
            "jump-navigation from DuckDB eval row → LangFuse trace UI without "
            "a timestamp/session join. Read from ``handler.last_trace_id`` "
            "after pipeline.invoke completes; None when LangFuse is disabled "
            "or handler lookup failed. Does not affect DuckDB-native metric "
            "computation; pure convenience for Chapter 6 debugging."
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
        description=(
            "When this record was written. Set at task-END time by the "
            "default_factory; the EvaluationRecord(...) constructor is "
            "called AFTER _run_jit / _run_static returns. ext8 #56 flagged "
            "that analyze_longitudinal's per-cycle attribution uses this as "
            "an END timestamp (not a start), so the bucketing loop uses "
            "half-open intervals with the current-cycle boundary as the "
            "upper bound."
        ),
    )

    model_config = {"extra": "forbid"}

    @classmethod
    def is_success(
        cls,
        *,
        gate_passed: bool,
        output_in_range: bool,
        output_shape_mismatch: bool = False,
    ) -> bool:
        """Canonical M1 success predicate — ext8 #59/#64 single source of truth.

        Every site that reports M1 (DuckDB SQL predicate, LangFuse
        ``m1.success`` score, this class's ``success`` property) MUST route
        through this predicate so the two stores never diverge.

        Pre-ext8-#59/#64, the LangFuse score used ``gate_passed * output_in_range``
        (pre-ext6-#32 formula) while DuckDB SQL had been tightened to also
        exclude ``output_shape_mismatch`` — the two stores disagreed on
        every shape-mismatched row. A single classmethod + one-line call
        at each consumer closes the gap.
        """
        return gate_passed and output_in_range and not output_shape_mismatch

    @property
    def success(self) -> bool:
        """Composite success: gate accepted AND output in range AND shape ok.

        Delegates to ``is_success`` (ext8 #59/#64) so this property and every
        other M1 consumer share one predicate.
        """
        return self.__class__.is_success(
            gate_passed=self.gate_passed,
            output_in_range=self.output_in_range,
            output_shape_mismatch=self.output_shape_mismatch,
        )
