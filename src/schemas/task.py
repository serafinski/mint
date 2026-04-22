from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.verification import VerificationReport


def decode_agent_config_metadata(raw: Any) -> dict | None:
    """Decode a ChromaDB-serialized ``agent_config`` back to a dict.

    Registry metadata values must be primitives, so ``agent_config`` is
    persisted as a JSON string by ``loading_node`` and ``seed_registry``.
    Every call site that reconstructs a ``GeneratedArtifact`` proxy from
    a registry row must run its ``agent_config`` through this decoder —
    otherwise Pydantic's ``dict_type`` validator rejects the string and
    reconstruction raises ``ValidationError``. Security audit finding #9
    (2026-04-19): the compositional path's ``_match_to_artifact`` skipped
    this step and crashed on every registry-matched AGENT artifact.

    Accepted inputs:
    - ``None`` / ``""`` → ``None``
    - ``dict`` → returned unchanged (pre-serialized row)
    - ``str`` of valid JSON that parses to a dict → parsed dict
    - anything else (malformed JSON, non-dict JSON, unexpected type) →
      ``None`` so callers can fail-soft rather than propagate a parse
      error. Callers that want loud-fail semantics can check for
      ``None`` when the raw input was truthy and emit their own error.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


class TaskStatus(StrEnum):
    """Status of a task as it progresses through the pipeline."""

    PENDING = "pending"
    DECOMPOSING = "decomposing"
    DETECTING_GAPS = "detecting_gaps"
    CLASSIFYING = "classifying"
    GENERATING = "generating"
    VERIFYING = "verifying"
    EXECUTING = "executing"  # reserved for future use (not set by current pipeline)
    COMPLETED = "completed"
    FAILED = "failed"


class SubTaskItem(BaseModel):
    """A single subtask as produced by the decomposition DeepAgent.

    This is the lean response-format model — it omits ``status`` so the LLM
    schema stays clean.  The pipeline converts these into ``SubTask`` objects
    (which carry runtime ``status``) after receiving the agent output.
    """

    description: str = Field(..., description="What this subtask needs to accomplish")
    required_capabilities: list[str] = Field(
        ...,
        min_length=1,
        description="Capability names or search queries needed for this subtask",
    )
    depends_on: list[int] = Field(
        default_factory=list,
        description="Zero-based indices of subtasks this one depends on",
    )
    output_schema: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Keys this step MUST include in its output dict, mapped to type "
            "descriptions. Downstream steps use these exact key names as their "
            "input contract. Example: {'inventory': 'list[dict] with product records'}"
        ),
    )
    requires_upstream_keys: list[str] = Field(
        default_factory=list,
        description=(
            "Top-level keys this subtask consumes from ``upstream_data`` at "
            "runtime. Each key here MUST appear in the ``output_schema`` of a "
            "transitive predecessor (direct or indirect via ``depends_on``). "
            "This is the structured contract used by the chain-coherence check "
            "— list ONLY top-level keys, not nested fields. For example, if "
            "this subtask reads ``upstream_data['rules']['min_days_of_stock']``, "
            "list 'rules', NOT 'min_days_of_stock'. Root subtasks (no "
            "``depends_on``) leave this empty."
        ),
    )

    model_config = {"extra": "forbid"}


class DecompositionResult(BaseModel):
    """Structured output returned by the decomposition DeepAgent."""

    subtasks: list[SubTaskItem] = Field(..., description="Ordered list of decomposed subtasks")
    reasoning: str = Field(
        default="",
        description="Brief explanation of the decomposition strategy",
    )

    model_config = {"extra": "forbid"}


class SubTask(BaseModel):
    """A decomposed sub-task with dependency tracking."""

    description: str = Field(..., description="What this sub-task needs to accomplish")
    required_capabilities: list[str] = Field(
        ..., min_length=1, description="Capability names or queries needed for this sub-task"
    )
    depends_on: list[int] = Field(
        default_factory=list, description="Indices of sub-tasks this one depends on"
    )
    output_schema: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Keys this step MUST include in its output dict, mapped to type "
            "descriptions. Downstream steps use these exact key names."
        ),
    )
    requires_upstream_keys: list[str] = Field(
        default_factory=list,
        description=(
            "Top-level keys this subtask consumes from ``upstream_data`` at "
            "runtime. Each key MUST appear in the ``output_schema`` of a "
            "transitive predecessor. Top-level keys only — not nested fields."
        ),
    )
    status: TaskStatus = Field(
        default=TaskStatus.PENDING, description="Current status of this sub-task"
    )

    model_config = {"extra": "forbid"}


class ClassifiedGap(BaseModel):
    """A gap that has been classified into a capability type."""

    description: str = Field(..., description="What capability is missing")
    query: str = Field(..., description="The search query that produced no match")
    classified_type: CapabilityType = Field(
        ..., description="What type of capability should fill this gap"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence score for the classification"
    )
    rationale: str = Field(default="", description="Explanation for why this type was chosen")

    model_config = {"extra": "forbid"}


class GeneratedArtifact(BaseModel):
    """An artifact generated by the capability pipeline."""

    capability_name: str = Field(..., description="Name of the generated capability")
    capability_type: CapabilityType = Field(..., description="Type of the generated capability")
    description: str | None = Field(
        default=None,
        description="Human-readable description of what the capability does",
    )
    source_code: str | None = Field(
        default=None, description="Generated source code (for MCP tools)"
    )
    skill_content: str | None = Field(
        default=None, description="Generated SKILL.md content (for skills)"
    )
    agent_config: dict | None = Field(default=None, description="Agent configuration (for agents)")
    gap_query: str | None = Field(
        default=None,
        description=(
            "The gap query string that produced this artifact. "
            "Set at generation time to enable skip logic on retry — "
            "gaps whose query matches an existing passing artifact are skipped."
        ),
    )
    pip_packages: list[str] = Field(
        default_factory=list,
        description="PyPI package names declared by the generator (e.g. ['pyyaml', 'scikit-learn'])",
    )
    input_schema: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Input parameter contract as {name: type_string}. For MCP_TOOL, "
            "extracted from the function signature via AST. For SKILL/AGENT, "
            "set from the upstream subtask's output_schema (or empty). "
            "Used by the registry to build the enriched embedding document (B1) "
            "so Pillar 1 gap detection and Pillar 3 Merge can match on "
            "structural signatures rather than description prose alone."
        ),
    )
    output_schema: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Output contract as {name: type_string}. Populated from the "
            "decomposer's SubTask.output_schema (Phase 3.5c) or by AST "
            "extraction of the return annotation. Used in the enriched "
            "embedding document (B1) and by downstream steps as the input "
            "schema they will receive."
        ),
    )
    domain: str | None = Field(
        default=None,
        description=(
            "Business sub-domain category assigned by the evolutionary "
            "taxonomy (B1 Step 3.5f). Populated by each generator after "
            "structured-output generation by calling "
            "DomainTaxonomy.get_or_create_for_capability(). None for "
            "pre-B1 artifacts and during pipeline warm-up before the "
            "taxonomy is wired; tool_loader / loading_node fall back to "
            "domain='general' when forwarding None onto CapabilityDescriptor."
        ),
    )
    is_generic: bool = Field(
        default=False,
        description=(
            "Set True by the evolutionary taxonomy when the implementation "
            "has NO hardcoded business vocabulary and would serve any "
            "business sub-domain (e.g. parse_csv_and_return_stats, "
            "fibonacci, format_date_string). Generic caps BYPASS the "
            "domain-equality filter at gap detection time, so they stay "
            "available to queries regardless of which business sub-domain "
            "the query is classified into. Without this flag, Phase E on "
            "2026-04-14 showed that strict domain filtering produces false "
            "recall losses on generic caps."
        ),
    )
    needs_network: bool = Field(
        default=False,
        description=(
            "Declared by the generator when the capability legitimately "
            "requires outbound network access at runtime (e.g. calls an "
            "external HTTP API). Used by the verification gate to (a) run "
            "Tier 2 behavioral tests under the same network policy the "
            "capability will execute under and (b) trigger the optional "
            "network-disconnect differential check that compares on/off "
            "behaviors to catch silent fallbacks. Offline-by-default tools "
            "leave this False — an MCP_TOOL that does pure local computation "
            "never needs to set it. Step 14.12."
        ),
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this artifact was generated",
    )

    model_config = {"extra": "forbid"}


class ClassificationResult(BaseModel):
    """LLM classification of a capability gap."""

    classified_type: CapabilityType = Field(
        ..., description="The capability type that best fits the gap"
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in the classification")
    rationale: str = Field(..., description="Brief explanation of why this type was chosen")
    capability_name: str = Field(
        ...,
        description=(
            "Short, semantic, kebab-case name for the capability (3-5 words, "
            "max 40 chars). Must describe WHAT THE CAPABILITY DOES, not how "
            "the task was phrased. Avoid task-specific verbs like 'parse-this', "
            "'build-a', 'you-are-investigating'. Prefer reusable names: "
            "'xml-revenue-extractor', 'inventory-reorder-calculator', "
            "'cascade-root-cause-investigator', 'department-spend-aggregator'. "
            "Lowercase letters, digits, and hyphens only."
        ),
    )

    model_config = {"extra": "forbid"}


class GapItem(BaseModel):
    """A capability that was needed but not found in the registry."""

    description: str = Field(..., description="What capability is missing")
    query: str = Field(..., description="The search query that produced no match")

    model_config = {"extra": "forbid"}


class GeneratorInput(BaseModel):
    """Input for type-specific generator nodes.

    Reserved for future Send-based fan-out (parallel generation).  In the
    current implementation (Iteration 2 Phase C) the three generator nodes
    are chained sequentially and each receives the full ``TaskState`` directly.
    ``GeneratorInput`` is defined here for schema completeness and potential
    use in a later iteration.
    """

    gap: ClassifiedGap = Field(..., description="The classified gap to generate a capability for")
    task_context: str = Field(default="", description="Original task description for context")

    model_config = {"extra": "forbid"}


class MatchItem(BaseModel):
    """A capability found in the registry that matches a requirement."""

    capability: CapabilityDescriptor = Field(..., description="The matched capability")
    score: float = Field(..., description="Similarity score from registry search")
    query: str = Field(
        default="",
        description="The original required_capability query that this match answered",
    )

    model_config = {"extra": "forbid"}


class OutputContract(BaseModel):
    """Declared shape of a task's expected output (Step 14.6).

    First-class typed contract for "what should the pipeline produce?" — the
    counterpart to ``task_description`` (which says *what to do*). Promotes the
    expected-output shape from prose buried inside ``task_prompt`` strings and
    imperative logic buried inside per-task ``validator`` closures into a single
    structured field that the CLI terminal-state check, the evaluation harness,
    and any future regeneration loop can read deterministically.

    The contract carries two orthogonal shapes:

    - ``required_keys``: top-level dict keys the pipeline's final output dict
      must contain (covers tasks like T01, T16 where the answer IS the dict).
    - ``required_files``: filenames that must appear in the execution backend's
      ``output_files`` mapping (covers tasks like T07, T11 that produce CSV /
      Excel outputs alongside a metadata dict).

    A task may declare either, both, or (for legacy / free-form CLI prompts
    where extraction failed) neither — when ``required_keys`` and
    ``required_files`` are both empty the contract check is a no-op and the
    pipeline falls back to its current exit-code-only success classification.

    Aligns with ``docs/thesis_proposal.md`` §Structured State Communication:
    inter-stage handoffs use strictly typed schemas, not natural-language
    prose or opaque callables. See ``docs/MASTER_PLAN_2026_04_13.md`` §14.6
    for the full motivating failure (T16 silent wrong-output) and design
    rationale.
    """

    required_keys: list[str] = Field(
        default_factory=list,
        description=(
            "Top-level dict keys that must be present in the pipeline's final "
            "output dict. Set comparison only — nested-key validation is out "
            "of scope. Empty list means 'no key requirements declared'."
        ),
    )
    required_files: list[str] = Field(
        default_factory=list,
        description=(
            "Filenames that must be present in the execution backend's "
            "output_files mapping (e.g. ['output.csv', 'output.xlsx']). "
            "Empty list means 'no file requirements declared'. Composes "
            "with the future file-returning-MCP-tool work without coupling: "
            "this field declares WHAT must exist, not HOW it gets produced."
        ),
    )

    model_config = {"extra": "forbid"}

    def is_empty(self) -> bool:
        """True when neither keys nor files are declared (legacy fall-through)."""
        return not self.required_keys and not self.required_files

    def check(
        self,
        output: object,
        output_files: dict[str, bytes] | None = None,
    ) -> "ContractCheckResult":
        """Set-compare the contract against an actual output dict + files map.

        Pure function — no IO, no LLM, no exceptions. Used by both the CLI
        terminal-state check and the evaluation harness's result assembly.
        """
        produced_keys: set[str] = set()
        if isinstance(output, dict):
            produced_keys = set(output.keys())
        produced_files = set((output_files or {}).keys())

        missing_keys = sorted(set(self.required_keys) - produced_keys)
        missing_files = sorted(set(self.required_files) - produced_files)
        return ContractCheckResult(
            satisfied=not missing_keys and not missing_files,
            missing_keys=missing_keys,
            missing_files=missing_files,
            produced_keys=sorted(produced_keys),
            produced_files=sorted(produced_files),
        )


class ContractCheckResult(BaseModel):
    """Result of comparing an OutputContract against actual outputs (Step 14.6)."""

    satisfied: bool = Field(
        ...,
        description="True iff every required key and required file is present.",
    )
    missing_keys: list[str] = Field(
        default_factory=list,
        description="Required dict keys absent from the actual output.",
    )
    missing_files: list[str] = Field(
        default_factory=list,
        description="Required filenames absent from the output_files mapping.",
    )
    produced_keys: list[str] = Field(
        default_factory=list,
        description="Keys actually present in the output dict (for diagnostics).",
    )
    produced_files: list[str] = Field(
        default_factory=list,
        description="Filenames actually present in output_files (for diagnostics).",
    )

    model_config = {"extra": "forbid"}


class TaskState(BaseModel):
    """Full pipeline state for capability synthesis tasks."""

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
    subtasks: list[SubTask] = Field(
        default_factory=list,
        description="Decomposed sub-tasks from task decomposition",
    )
    classified_gaps: list[ClassifiedGap] = Field(
        default_factory=list,
        description="Gaps that have been classified into capability types",
    )
    generated_artifacts: list[GeneratedArtifact] = Field(
        default_factory=list,
        description="Artifacts produced by capability generators",
    )
    verification_results: list[VerificationReport] = Field(
        default_factory=list,
        description="Verification reports for generated artifacts",
    )
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        description="Current pipeline status",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Error messages accumulated during pipeline execution",
    )
    trace_id: str | None = Field(
        default=None,
        description="LangFuse trace ID for observability",
    )
    attempt_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of generation+verification cycles attempted for the current task. "
            "Incremented by the verification node on each retry. "
            "Used to enforce the 3-strike limit before failing open."
        ),
    )
    decomposition_succeeded: bool = Field(
        default=True,
        description=(
            "Sticky flag set to False by decomposition_node whenever the "
            "DeepAgent times out, errors, or returns zero subtasks. Downstream "
            "nodes MUST NOT read or write this field — it is consumed only by "
            "the terminal CLI display and the evaluation harness's success "
            "classifier so the pipeline can continue through its fallback path "
            "without the user-facing layer mistaking a fallback-produced wrong "
            "output for success. The enum `status` field transitions forward "
            "through every node and so cannot carry a sticky failure signal."
        ),
    )
    output_contract: OutputContract | None = Field(
        default=None,
        description=(
            "Declared shape of the task's expected output (Step 14.6). "
            "Populated at task entry by the harness (directly from "
            "TaskDefinition.output_contract) or by the CLI boundary adapter "
            "(known-task shortcut, then LLM extractor for free-form prompts). "
            "Pipeline nodes MUST NOT read or write this field — it is a "
            "passenger that flows from task entry to the terminal-state "
            "contract check, where a pure set comparison against the final "
            "output dict and output_files mapping classifies the run as "
            "success / shape-violation. None means no contract was declared "
            "or extraction failed; the check degrades to legacy exit-code-only "
            "behavior in that case."
        ),
    )
    verification_succeeded: bool = Field(
        default=True,
        description=(
            "Sticky flag set to False by verification_node when MAX_ATTEMPTS "
            "retries are exhausted and at least one generated artifact still "
            "fails the three-tier gate. Restores proposal-strict Pillar 2 "
            "semantics (§12 finding #14): 'Only verified capabilities enter "
            "production'. When False, failed artifacts are dropped from "
            "generated_artifacts (so they never register) and the CLI "
            "runner / evaluation harness classify the run as "
            "VERIFICATION_FAILED rather than fall-through success. Same "
            "sticky-flag pattern as decomposition_succeeded — carried "
            "dedicated because the status enum transitions forward through "
            "every downstream node and cannot preserve a rejection signal."
        ),
    )
    classification_succeeded: bool = Field(
        default=True,
        description=(
            "Sticky flag set to False by classifier_node when any gap fails "
            "classification after MAX_CLASSIFICATION_ATTEMPTS retries (§12 "
            "finding #16, 2026-04-16). Restores proposal-strict Pillar 1.3 "
            "semantics: 'An MCP tool that should have been a skill will be "
            "too rigid; a skill that should have been an agent will lack the "
            "autonomy to handle state.' Previously a classification LLM "
            "failure silently defaulted to MCP_TOOL with confidence=0.3, "
            "which is exactly the type-mismatch failure mode the proposal "
            "warns against. Under the new contract: 3x retry on exception; "
            "final failure flips this sticky flag, surfaces a distinct CLI "
            "exit code, and fails the task cleanly rather than shipping a "
            "mis-classified artifact. Same sticky-flag pattern as "
            "decomposition_succeeded and verification_succeeded."
        ),
    )
    allow_network: bool = Field(
        default=False,
        description=(
            "When True, runtime sandbox execution and the verification gate "
            "(Tier 2 + Tier 3) allow outbound network access — Docker runs "
            "with ``--network bridge`` instead of ``--network none``. Set "
            "by the CLI from the ``--allow-network`` flag (overridable via "
            "``mint config set network.allow true``) and by the evaluation "
            "harness for tasks flagged as network-dependent. Offline-by-"
            "default (False) preserves the standard safety posture. "
            "Plumbed through CompositionalExecutor → execution backends and "
            "gate.verify(). Step 14.12."
        ),
    )

    model_config = {"extra": "forbid"}


class CapabilityReference(BaseModel):
    """Pointer to either a registry-matched or freshly-generated capability.

    Used by Stage 3 CompositionalExecutor to resolve a subtask's
    required_capability to a concrete artifact at execution time.
    """

    source: Literal["registry", "generated"]
    capability_name: str
    capability_type: CapabilityType
    score: float | None = Field(
        default=None,
        description="Similarity score (registry matches only); None for generated.",
    )

    model_config = {"extra": "forbid"}


class StepTiming(BaseModel):
    """Per-step execution metrics for chain analysis."""

    step_index: int
    capability_name: str
    capability_type: str
    source: str  # "registry" or "generated"
    success: bool
    latency_ms: float

    model_config = {"extra": "forbid"}


class ChainResult(BaseModel):
    """Result of a compositional execution chain."""

    chain_length: int = Field(..., ge=1)
    chain_success: bool
    step_results: list[dict] = Field(default_factory=list)
    failed_step_index: int | None = None
    failed_step_name: str | None = None
    failed_step_reason: str | None = Field(
        default=None,
        description=(
            "Structured reason for a chain step failure, orthogonal to "
            "``failed_step_name`` (which carries the subtask description). "
            "Populated from the execution backend's sticky meta — currently "
            "``'agent_execution_timeout'`` and ``'agent_execution_error'`` "
            "(Step 13). Harness reads this field to classify a chain failure "
            "as a timeout vs a shape failure without relying on output-shape "
            "validation alone. None for success, SKILL/MCP_TOOL failures, "
            "or AGENT failures where no meta was recorded."
        ),
    )
    step_timings: list[StepTiming] = Field(
        default_factory=list,
        description="Per-step execution metrics (capability name, type, latency).",
    )
    output_files: dict[str, bytes] = Field(
        default_factory=dict,
        description=(
            "Files produced by the last step of the chain. "
            "Maps filename (e.g. 'output.csv') to raw bytes. "
            "Populated when the last step writes to /workspace/output.*"
        ),
    )

    model_config = {"extra": "forbid"}
