"""Evolutionary operations: prune, split, merge.

Type scoping (post-Steps-5/6/7/8, 2026-04-15):
- Prune: all types (unchanged)
- Split: all types — see _split_tools / _split_skills / _split_agents (Step 8)
- Merge: all types — see _merge_tools / _merge_skills / _merge_agents (Step 6)

All merge and split operations produce a ``GeneratedArtifact`` with
real executable content (not metadata-only stubs) and route it through
``verify_and_register_or_rollback`` which runs the full three-tier
verification gate and registers atomically. Parents are deactivated
only after the new artifact has been registered successfully — if the
gate rejects the new artifact, the parents stay active and the
optimizer treats the operation as a no-op. This honors the proposal's
Pillar 2 "must pass all three tiers" contract at the optimizer layer.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from langchain.chat_models import init_chat_model

from src.config import DEFAULT_LLM_MODEL
from pydantic import BaseModel, Field

from src.orchestrator.tracing import emit_langfuse_event
from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry
from src.schemas.capability import CapabilityDescriptor, CapabilityType
from src.schemas.task import GeneratedArtifact
from src.schemas.verification import Severity

logger = logging.getLogger(__name__)


def _record_and_emit_event(
    metrics_store: MetricsStore,
    capability_name: str,
    event_type: str,
    details: str = "",
) -> None:
    """Record a Pillar 3 op event in BOTH DuckDB and LangFuse (Step 13.7 Batch A3).

    Previously only DuckDB-native via ``metrics_store.record_event``; now
    mirrors to LangFuse as ``pillar3.<event_type>`` so Chapter 5.3's
    per-operation narrative is traceable in the LangFuse UI without a
    DuckDB join. Never raises — both sides are best-effort.

    Event level: ``WARNING`` for rollbacks (``verification_rollback``,
    ``split_rollback``), ``DEFAULT`` otherwise.
    """
    try:
        metrics_store.record_event(capability_name, event_type, details)
    except Exception:  # noqa: BLE001
        logger.debug("record_event failed for %s/%s", capability_name, event_type)

    level = "WARNING" if event_type.endswith("_rollback") else "DEFAULT"
    emit_langfuse_event(
        None,
        f"pillar3.{event_type}",
        level,
        {
            "capability_name": capability_name,
            "event_type": event_type,
            "details": details,
        },
    )


def _langfuse_config() -> dict:
    """Return a LangChain config dict inheriting parent session_id / tags /
    user_id from the active LangGraph config (best-effort).

    Step 13.7 Batch A2 (2026-04-18): merge/split LLM traces link to the
    triggering task's session. Without this, optimizer-path traces
    appear orphaned in LangFuse (not joinable to the originating Phase 4
    record) and Chapter 5.3 navigation requires a DB join.

    Fix #3 (2026-04-19, docs/langfuse_redundancy_audit_2026_04_19.md):
    Previously constructed a fresh ``LangfuseHandler()`` and added it to
    ``base["callbacks"]``. But ``operations.py`` runs inside
    ``optimizer_node`` which is itself a LangGraph node under the parent
    ``pipeline.invoke`` config — where a CallbackHandler is ALREADY
    attached. LangChain's ``.invoke(config=lf_config)`` merges callbacks
    additively, so the same merge/split LLM call would fire BOTH handlers
    and produce two GENERATION spans per call (pure duplicate).

    Fix: omit the explicit handler. LangGraph propagates the parent
    ``pipeline.invoke`` CallbackHandler via contextvar, so the LLM call
    auto-traces under the parent trace — single GENERATION, correctly
    nested under ``optimizer_node``'s graph position. Metadata inheritance
    unchanged (tags / session / user still flow through).
    """
    base: dict = {}

    try:
        from langgraph.config import get_config as _get_langgraph_config

        parent_config = _get_langgraph_config()
        parent_meta = (parent_config or {}).get("metadata", {})
        inherited = {
            k: v for k, v in parent_meta.items()
            if k.startswith("langfuse_")
            or k in ("task_id", "condition", "run_id", "tier")
        }
        if inherited:
            base["metadata"] = inherited
    except Exception:  # noqa: BLE001
        pass

    return base


# ---------------------------------------------------------------------------
# Step 5 (B1 Pillar 3, 2026-04-15) — shared verify-and-register-or-rollback
# ---------------------------------------------------------------------------
#
# The merge and split operations produce a GeneratedArtifact with real
# executable content (not metadata-only stubs like the pre-2026-04-15 code)
# and must route it through the three-tier verification gate before it
# enters the registry. The pre-2026-04-15 code path bypassed the gate
# entirely with a self-justifying comment ("metadata-only, no verification
# needed"), which violated the proposal's Pillar 2 contract and produced
# unexecutable descriptors that poisoned the registry.
#
# The helper below is the single entry point that both merge and split
# funnel through. Atomicity guarantee: if the gate rejects the artifact,
# NEITHER the new artifact NOR the parent capabilities are touched. The
# caller (merge/split) is responsible for deactivating parents ONLY after
# verify_and_register_or_rollback returns (True, None).
#
# Never raises — wraps every failure path (gate exception, register
# exception, unexpected errors) and returns (False, reason). Callers
# treat anything other than (True, None) as a "leave everything alone"
# signal.


def verify_and_register_or_rollback(
    artifact: GeneratedArtifact,
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    event_type: str,
    event_details: str,
    tags: list[str] | None = None,
    domain: str | None = None,
    allow_network: bool = False,
) -> tuple[bool, str | None]:
    """Verify an artifact through the three-tier gate and register atomically.

    The artifact MUST carry executable content appropriate to its type:

    - ``MCP_TOOL`` → ``artifact.source_code`` populated
    - ``SKILL`` → ``artifact.skill_content`` populated (full SKILL.md)
    - ``AGENT`` → ``artifact.agent_config`` populated (dict with
      ``system_prompt`` + optional ``subagents`` list)

    Behavior:

    1. Call ``src.verification.gate.verify(artifact)`` which runs Tier 0
       (skill quick_validate) + Tier 1 (static scanner with code-fence
       extraction) + Tier 2 (behavioral testing) + Tier 3 (adversarial
       probing). The gate never raises; this helper catches any
       unexpected exception as a safety net.
    2. If ``report.overall_passed`` is False → record a
       ``verification_rollback`` metrics event with the blocking findings
       summarized in ``details`` and return ``(False, reason)``. The
       registry is not touched.
    3. Otherwise build a ``CapabilityDescriptor`` and call
       ``registry.add``. For ``MCP_TOOL`` artifacts the registration
       path delegates to ``ToolLoader.register_tool`` which also runs
       AST schema extraction (Step 2). For ``SKILL`` and ``AGENT`` the
       descriptor is built directly from ``artifact.input_schema`` and
       ``artifact.output_schema`` which the caller (merge/split) is
       responsible for populating.
    4. Record a metrics event with ``event_type`` and ``event_details``
       so the optimizer audit trail distinguishes successful
       registrations from rollbacks.

    Args:
        artifact: The generated artifact. Must have executable content
            populated for its capability type.
        registry: Target registry.
        metrics_store: Metrics store for event recording.
        event_type: Metrics event type on success (e.g. ``"merged"``,
            ``"split_variant"``). The rollback path always records
            ``"verification_rollback"`` regardless.
        event_details: Freeform description for the success event
            (e.g. ``"from cap-a, cap-b"``).
        tags: Optional tags to attach to the descriptor.
        domain: Optional domain override. Falls back to
            ``artifact.domain`` or ``"generated"``.

    Returns:
        ``(True, None)`` on gate pass + successful registration.
        ``(False, reason)`` on gate rejection, registration exception,
        or any other failure path. Never raises.
    """
    from src.verification.gate import verify

    name = artifact.capability_name

    # --- Gate ---
    try:
        report = verify(artifact, allow_network=allow_network)
    except Exception as exc:  # noqa: BLE001
        reason = f"verify() raised unexpectedly: {exc}"
        logger.warning(
            "verify_and_register_or_rollback(%s): %s", name, reason
        )
        _record_and_emit_event(metrics_store, name, "verification_rollback", reason)
        return False, reason

    if not report.overall_passed:
        blocking = [
            f"tier{f.tier}/{f.severity.value}:{f.category}"
            for f in report.findings
            if f.severity in (Severity.CRITICAL, Severity.HIGH)
        ]
        reason = (
            f"gate rejected {name}: "
            f"{', '.join(blocking) if blocking else 'report.overall_passed=False with no blocking findings'}"
        )
        logger.warning(
            "verify_and_register_or_rollback(%s): %s", name, reason
        )
        _record_and_emit_event(metrics_store, name, "verification_rollback", reason)
        return False, reason

    # --- Register ---
    return _register_artifact_directly(
        artifact,
        registry,
        metrics_store,
        event_type=event_type,
        event_details=event_details,
        tags=tags,
        domain=domain,
    )


def _register_artifact_directly(
    artifact: GeneratedArtifact,
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    event_type: str,
    event_details: str,
    tags: list[str] | None = None,
    domain: str | None = None,
) -> tuple[bool, str | None]:
    """Register an artifact without re-running the verification gate.

    Factored out from :func:`verify_and_register_or_rollback` for reuse
    by Step 8's per-type split helpers, which verify every variant in
    a batch BEFORE registering any. The split flow is:

    1. Build N variants from the LLM output.
    2. For each variant, call ``gate.verify()`` — if any fails, abort
       without touching the registry (parent stays active).
    3. Run ``verify_coverage`` collectively — if coverage fails, abort.
    4. Register each variant via this helper (NO re-verification).
    5. Deactivate the parent.

    Phase 4 must not re-run the gate because Tier 3's adversarial probe
    is LLM-nondeterministic — a variant that passed phase 2 could fail
    phase 4 on stochastic variance, which would break atomicity without
    an obvious rollback path. Instead, the split trusts phase 2's
    verdict and only registers the artifact here.

    For merge, ``verify_and_register_or_rollback`` calls this helper
    directly after its own gate check, so there is no behavior change
    for merge callers — the logic is identical, just refactored.

    MCP_TOOL: delegates to ``ToolLoader.register_tool`` to inherit
    Step 2's AST schema extraction + source_code persistence.
    SKILL / AGENT: builds the descriptor directly from artifact fields.

    Never raises. Returns ``(True, None)`` on successful registration,
    ``(False, reason)`` on any exception (unexpected registry failure,
    descriptor validation failure, metrics event recording failure).
    On failure, records a ``verification_rollback`` event the same way
    the gate-rejection path does — the calling split helper should
    treat any failure here as terminal and abort the split.
    """
    name = artifact.capability_name
    try:
        effective_domain = domain or artifact.domain or "generated"
        if artifact.capability_type == CapabilityType.MCP_TOOL:
            # Delegate to ToolLoader.register_tool so we inherit the
            # AST-based input/output schema extraction from Step 2 AND
            # the source_code-in-metadata persistence. register_tool
            # calls registry.add internally.
            from src.orchestrator.tool_loader import ToolLoader
            loader = ToolLoader()
            loader.register_tool(
                artifact,
                registry,
                description=artifact.description,
                domain=effective_domain,
                tags=list(tags or []),
            )
        else:
            # SKILL / AGENT — build the descriptor directly. The caller
            # is responsible for populating artifact.input_schema and
            # artifact.output_schema (merge derives from parents, split
            # derives from the parent being specialized).
            #
            # ext8 #63 (2026-04-20): validate that the LLM-claimed schemas
            # look sane. The MCP_TOOL path delegates to
            # ``ToolLoader.register_tool`` which runs AST-based schema
            # extraction; SKILL/AGENT have no AST equivalent because the
            # "tool" is prose + embedded Python fences. Fail closed if
            # the artifact has executable content (skill_content /
            # agent_config) but both schemas are empty — a schema-less
            # registration would poison later compatibility-aware reuse.
            has_content = bool(
                artifact.skill_content or artifact.agent_config
            )
            has_schemas = bool(
                (artifact.input_schema or {})
                or (artifact.output_schema or {})
            )
            if has_content and not has_schemas:
                reason = (
                    f"schema-less {artifact.capability_type.value} artifact "
                    f"'{name}' is not registerable"
                )
                logger.warning("ext8 #63: %s", reason)
                return False, reason
            metadata: dict[str, str] = {
                "generated": "true",
                "source": "optimizer",
            }
            if artifact.skill_content:
                metadata["skill_content"] = artifact.skill_content
            if artifact.agent_config:
                metadata["agent_config"] = (
                    json.dumps(artifact.agent_config)
                    if isinstance(artifact.agent_config, dict)
                    else str(artifact.agent_config)
                )
            from src.registry.token_count import count_definition_tokens

            descriptor = CapabilityDescriptor(
                name=name,
                description=artifact.description or name,
                capability_type=artifact.capability_type,
                domain=effective_domain,
                is_generic=bool(artifact.is_generic),
                input_schema=dict(artifact.input_schema or {}),
                output_schema=dict(artifact.output_schema or {}),
                tags=list(tags or []),
                definition_token_cost=count_definition_tokens(artifact),
                # Security audit finding #83 (2026-04-19, ext9): preserve
                # generator-declared pip_packages through Pillar 3 Merge /
                # Split registration so optimised MCP tools retain their
                # runtime dependencies.
                pip_packages=list(getattr(artifact, "pip_packages", []) or []),
                metadata=metadata,
            )
            registry.add(descriptor)

        _record_and_emit_event(metrics_store, name, event_type, event_details)
        logger.info(
            "_register_artifact_directly(%s): registered as %s (%s)",
            name, event_type, event_details,
        )
        return True, None

    except Exception as exc:  # noqa: BLE001
        reason = f"registration failed for {name}: {exc}"
        logger.warning(
            "_register_artifact_directly(%s): %s", name, reason
        )
        _record_and_emit_event(metrics_store, name, "verification_rollback", reason)
        return False, reason


# --- Structured output schemas for LLM calls ---


class _SplitVariantCommon(BaseModel):
    """Shared fields across all three per-type split variant schemas."""

    name: str = Field(..., description="Name for the specialized variant (hyphen-case)")
    description: str = Field(
        ..., description="What this variant does in one sentence"
    )
    subspace: str = Field(
        ...,
        description=(
            "The input subspace this variant targets — the specific "
            "kind of input where the parent capability was breaking and "
            "this variant is specialized to handle. Pillar 3 Split "
            "language: 'input subspaces where the capability breaks'."
        ),
    )

    model_config = {"extra": "forbid"}


class SplitToolVariant(_SplitVariantCommon):
    """One MCP_TOOL variant produced by splitting."""

    source_code: str = Field(
        ...,
        description=(
            "Complete Python source file for this specialized variant. "
            "Must follow the FastMCP pattern (imports, mcp = FastMCP(...), "
            "@mcp.tool()-decorated async def, Annotated[type, Field(...)] "
            "parameters, if __name__ == '__main__': mcp.run()). The "
            "function body should handle the variant's declared subspace "
            "robustly — it is NOT a narrowing of the parent; it is a "
            "reimplementation specialized to one subspace."
        ),
    )
    pip_packages: list[str] = Field(
        default_factory=list,
        description=(
            "PyPI package names required by this variant's source. Use "
            "pip install names (pyyaml not yaml). Omit stdlib and the "
            "base image packages (mcp, pydantic)."
        ),
    )


class SplitSkillVariant(_SplitVariantCommon):
    """One SKILL variant produced by splitting."""

    skill_content: str = Field(
        ...,
        description=(
            "Complete SKILL.md body including YAML frontmatter (name, "
            "description, allowed-tools, etc.) and the instruction prose "
            "that teaches an agent how to handle this variant's declared "
            "subspace. Each variant is a coherent specialized skill — "
            "not a narrowing of the parent, but a focused reimplementation."
        ),
    )


class SplitAgentVariantSubagent(BaseModel):
    """One subagent of one AGENT variant in a split."""

    name: str = Field(..., max_length=64, description="Subagent name (hyphen-case, max 64 chars)")
    description: str = Field(..., description="One-sentence subagent role")
    system_prompt: str = Field(
        ..., description="Full system prompt for the subagent's isolated context"
    )
    tools: list[str] = Field(
        default_factory=list,
        description=(
            "Names of tools the subagent can call. Generator agents "
            "currently support only ``sandbox_execute``."
        ),
    )

    model_config = {"extra": "forbid"}


class SplitAgentVariant(_SplitVariantCommon):
    """One AGENT variant produced by splitting."""

    system_prompt: str = Field(
        ...,
        description=(
            "Full system prompt for the variant's lead agent. Describes "
            "the adaptive reasoning the agent should perform on its "
            "declared input subspace."
        ),
    )
    subagents: list[SplitAgentVariantSubagent] = Field(
        default_factory=list,
        description=(
            "Optional subagents for this variant. The per-variant "
            "subagent count is limited by the global budget ceiling "
            "enforced by ``_split_agents`` across ALL variants combined."
        ),
    )


class _SplitResultCommon(BaseModel):
    """Shared fields across all three per-type split LLM output schemas."""

    analysis: str = Field(
        ...,
        description=(
            "Why the parent capability has high-reuse-low-success metrics "
            "and how the failure cases partition into input subspaces. "
            "The proposal's Pillar 3 Split clause calls this 'the input "
            "subspaces where the capability breaks'. This field is the "
            "LLM's articulation of that partition and is used for "
            "Chapter 6 analysis — humans can read it to understand what "
            "the optimizer saw in the failure data."
        ),
    )


class SplitToolsResult(_SplitResultCommon):
    """LLM output for splitting an MCP_TOOL capability into specialized variants."""

    variants: list[SplitToolVariant] = Field(
        ...,
        min_length=2,
        max_length=4,
        description=(
            "Two to four specialized MCP_TOOL variants. Each variant "
            "carries complete executable source and targets a declared "
            "input subspace of the parent's failure data."
        ),
    )

    model_config = {"extra": "forbid"}


class SplitSkillsResult(_SplitResultCommon):
    """LLM output for splitting a SKILL capability into specialized variants."""

    variants: list[SplitSkillVariant] = Field(
        ...,
        min_length=2,
        max_length=4,
        description=(
            "Two to four specialized SKILL variants. Each variant "
            "carries complete SKILL.md content and targets a declared "
            "input subspace of the parent's failure data."
        ),
    )

    model_config = {"extra": "forbid"}


class SplitAgentsResult(_SplitResultCommon):
    """LLM output for splitting an AGENT capability into specialized variants."""

    variants: list[SplitAgentVariant] = Field(
        ...,
        min_length=2,
        max_length=4,
        description=(
            "Two to four specialized AGENT variants. Each variant "
            "carries a lead system prompt plus optional subagents, and "
            "targets a declared input subspace of the parent's failure "
            "data. The per-variant subagent count is constrained by the "
            "global budget ceiling enforced by the caller across all "
            "variants combined."
        ),
    )

    model_config = {"extra": "forbid"}


class _MergeResultCommon(BaseModel):
    """Shared fields across all three per-type merge LLM output schemas."""

    name: str = Field(..., description="Name for the merged capability (hyphen-case)")
    description: str = Field(..., description="What the merged capability does")
    rationale: str = Field(
        ..., description="Why merging these capabilities produces a single coherent one"
    )

    model_config = {"extra": "forbid"}


class MergeToolsResult(_MergeResultCommon):
    """LLM output for merging two or more MCP_TOOL capabilities.

    Carries a full FastMCP source file that handles the union of the
    parent capabilities' input spaces. The source goes through the
    same three-tier verification gate as generator-produced MCP tools.
    """

    combined_source_code: str = Field(
        ...,
        description=(
            "Complete Python source file for the merged MCP tool. Must "
            "follow the FastMCP pattern (imports, mcp = FastMCP(...), "
            "@mcp.tool()-decorated async def, Annotated[type, Field(...)] "
            "parameters, if __name__ == '__main__': mcp.run()). The "
            "function body should handle the union of the parents' "
            "input spaces — merging behavior, not just concatenation."
        ),
    )
    combined_pip_packages: list[str] = Field(
        default_factory=list,
        description=(
            "Union of PyPI package names required by the merged source. "
            "Use pip install names (pyyaml not yaml). Omit stdlib and "
            "the base image packages (mcp, pydantic)."
        ),
    )


class MergeSkillsResult(_MergeResultCommon):
    """LLM output for merging two or more SKILL capabilities.

    Carries a full SKILL.md body (YAML frontmatter + instruction prose +
    optional embedded Python code fences). The content goes through
    Tier 0 (``tools/quick_validate.py``) + Tier 1 (code-fence extractor
    + AST scan on extracted Python blocks) + Tier 2 / Tier 3 if Tier 1
    passes.
    """

    combined_skill_content: str = Field(
        ...,
        description=(
            "Complete SKILL.md body including YAML frontmatter (name, "
            "description, allowed-tools, etc.) and the instruction prose "
            "that teaches an agent how to perform the merged workflow. "
            "Should cover the union of the parent skills' workflows as "
            "a single coherent set of instructions, not a concatenation."
        ),
    )


class MergeAgentsSubagent(BaseModel):
    """One subagent in a lead-with-subagents AGENT configuration."""

    name: str = Field(..., max_length=64, description="Subagent name (hyphen-case, max 64 chars)")
    description: str = Field(..., description="One-sentence description of the subagent's role")
    system_prompt: str = Field(
        ..., description="Full system prompt for the subagent's isolated context"
    )
    tools: list[str] = Field(
        default_factory=list,
        description=(
            "Names of tools the subagent can call (typically "
            "['sandbox_execute']). Generator agents currently only "
            "support sandbox_execute; other tool names are ignored at "
            "runtime."
        ),
    )

    model_config = {"extra": "forbid"}


class MergeAgentsResult(_MergeResultCommon):
    """LLM output for merging two or more AGENT capabilities.

    Carries a lead system prompt plus an optional subagents list.
    Subagent merging rules (enforced by the caller after LLM output):

    - Disjoint subagent names across parents → union the lists.
    - Overlapping subagent names → the LLM is instructed to synthesize
      merged prompts via text combination.
    - Combined subagent count > 8 → reject the merge up front
      (budget ceiling per the plan).
    """

    combined_system_prompt: str = Field(
        ...,
        description=(
            "Full system prompt for the lead agent of the merged "
            "capability. Must describe the adaptive reasoning the "
            "agent should perform across the union of the parents' "
            "problem spaces."
        ),
    )
    combined_subagents: list[MergeAgentsSubagent] = Field(
        default_factory=list,
        description=(
            "Subagents the lead can delegate to via the task() tool. "
            "Empty list means the merged agent runs as a solo DeepAgent. "
            "Total count must not exceed 8 (Step 6d budget ceiling); "
            "the caller rejects the merge if this is violated."
        ),
    )


# --- Operations ---


def prune(
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    window_days: int = 30,
) -> list[str]:
    """Deactivate capabilities with zero invocations in the given window.

    Applies to ALL capability types.

    Freshly created capabilities are exempt until they age past the prune
    window. Otherwise the eval harness's `loading_node -> optimizer_node`
    ordering would deactivate a capability immediately after registration,
    before any later task in the same run had a chance to reuse it.

    Returns:
        List of pruned (deactivated) capability IDs.
    """
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    active = registry.list_all(is_active=True)
    pruned: list[str] = []

    for cap in active:
        # Never prune pinned capabilities (seeded entries that should survive
        # optimizer cycles regardless of invocation count)
        if (cap.metadata or {}).get("pinned") == "true":
            logger.debug("Skipping pinned capability '%s'", cap.name)
            continue
        if cap.created_at >= cutoff:
            logger.debug(
                "Skipping freshly created capability '%s' from prune window "
                "(created_at=%s, cutoff=%s)",
                cap.name,
                cap.created_at.isoformat(),
                cutoff.isoformat(),
            )
            continue
        count = metrics_store.invocations_since(cap.name, cutoff)
        if count == 0:
            registry.deactivate(cap.name)
            _record_and_emit_event(metrics_store, cap.name, "pruned", f"zero invocations in {window_days}d")
            logger.info("Pruned capability '%s' (zero invocations in %dd)", cap.name, window_days)
            pruned.append(cap.name)

    return pruned


def split(
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    capability_id: str,
    *,
    llm_model: str = "",
    allow_network: bool = False,
) -> list[str] | None:
    """Split a capability into specialized variants for its input subspaces.

    Dispatches by capability type to one of the per-type split helpers:

    - ``MCP_TOOL`` → :func:`_split_tools` (gate per variant + collective
      coverage verification via :func:`verify_coverage`)
    - ``SKILL`` → :func:`_split_skills` (gate per variant only;
      coverage returns vacuously passing per MCP_TOOL-only scope)
    - ``AGENT`` → :func:`_split_agents` (gate per variant only;
      global subagent budget ceiling enforced across variants)

    Each per-type helper produces a list of specialized variants as
    ``GeneratedArtifact`` objects carrying real executable content
    (source_code / skill_content / agent_config). The split flow is
    five-phase atomic:

    1. **Build** variants from the LLM output.
    2. **Verify** each variant through the three-tier gate via
       ``gate.verify()`` only — no registration. If ANY variant fails
       the gate, abort without touching the registry.
    3. **Coverage** check via :func:`verify_coverage` — MCP_TOOL runs
       the replay loop against the parent's recorded failures from
       :func:`MetricsStore.get_recent_failures`, SKILL/AGENT return a
       vacuously-passing report. If coverage fails, abort.
    4. **Register** each verified variant via
       :func:`_register_artifact_directly` — this path does NOT
       re-run the gate (Tier 3 is LLM-nondeterministic and re-running
       risks breaking atomicity on stochastic variance).
    5. **Deactivate** the parent only after all variants are
       successfully registered.

    If any of phases 2-4 fail, the function returns ``None`` and
    neither the new variants nor the parent are touched. This is the
    atomic rollback guarantee that couples Pillar 3 Split to Pillar 2's
    *"must pass all three tiers"* contract the same way merge already
    does in Step 6.

    Args:
        registry: Skill registry.
        metrics_store: Metrics store for pulling metrics + recording events.
        capability_id: ID of the capability to split.
        llm_model: Model identifier for init_chat_model. Empty string
            uses DEFAULT_LLM_MODEL.

    Returns:
        List of new variant capability names on successful split,
        ``None`` on gate rejection, coverage failure, name collision,
        LLM failure, or any other non-fatal failure.

    Raises:
        KeyError: If ``capability_id`` is not found in the registry.
    """
    cap = registry.get(capability_id)
    if cap is None:
        raise KeyError(f"Capability '{capability_id}' not found in registry")

    if cap.capability_type == CapabilityType.MCP_TOOL:
        return _split_tools(
            cap, registry, metrics_store,
            llm_model=llm_model, allow_network=allow_network,
        )
    if cap.capability_type == CapabilityType.SKILL:
        return _split_skills(
            cap, registry, metrics_store,
            llm_model=llm_model, allow_network=allow_network,
        )
    if cap.capability_type == CapabilityType.AGENT:
        return _split_agents(
            cap, registry, metrics_store,
            llm_model=llm_model, allow_network=allow_network,
        )

    raise ValueError(
        f"split() does not support capability_type={cap.capability_type.value}"
    )


_MAX_AGENT_SUBAGENT_BUDGET = 8


# ---------------------------------------------------------------------------
# Step 6 + pre-registered finding #4 from §12 (2026-04-15 end of session)
# ---------------------------------------------------------------------------
#
# The proposal's Pillar 3 Merge clause says *"The merged version inherits
# the strongest behavioral patterns from each parent."* True "strongest
# patterns" selection would require per-parent success metrics on each
# behavior subspace, which is Chapter 7 work. For now we approximate it
# via explicit robustness hints in the merge prompts — tell the LLM to
# prefer battle-tested library patterns, robust error handling, and
# explicit validation at boundaries.
#
# **Deliberately domain-general — no testbed-specific library names or
# edge-case examples.** An earlier version of this hint named
# `csv.Sniffer`, `pandas.read_csv`, `openpyxl`, specific CSV edge cases
# (BOM markers, line endings, header detection), etc. — that was
# effective at steering the LLM past the specific bugs seen on
# `test_split_merge.py`, but it overfit the hint to the Business
# Analytics testbed. MINT is supposed to work across arbitrary CLI
# inputs (medical, legal, factory simulation, text analysis, …), and
# a prompt that biases every merge toward file-format parsing would
# add noise on non-file-format tasks and hurt generalization. The
# current hint keeps only domain-general principles so it transfers
# across verticals. A Phase 5 / Chapter 6 consequence: merge success
# rates may be lower than a testbed-tuned prompt would show, because
# the LLM has no hand-holding on the specific edge cases of any one
# format. This is proposal-aligned (the gate catches bad output) but
# worth disclosing.
#
# This block is injected into all three per-type merge prompts as a
# shared section. It is the domain-general partial implementation of
# finding #4 from §12 of docs/MASTER_PLAN_2026_04_13.md — pulled
# forward from the originally-planned §12 timing because the Step 6
# merge smoke gave us strong empirical motivation to at least land
# the general principles now. True per-parent success-metric-driven
# selection remains Chapter 7 future work.

_MERGE_ROBUSTNESS_HINT = """\
## Robustness requirements (Pillar 3 "strongest behavioral patterns")

The proposal's Pillar 3 Merge clause says the merged capability should
inherit the **strongest behavioral patterns** from each parent — not
just the union of their logic. Where the parents diverge, prefer the
approach that is **more robust under real-world input**. The criteria
below are intentionally domain-general — they should guide the
merged implementation regardless of whether the parents handle data
parsing, math, text analysis, agentic reasoning, or any other task
shape.

- **Prefer well-known, battle-tested library defaults over
  hand-rolled logic.** If a mature standard-library or widely-used
  third-party library already solves the task the parents are
  performing, use it with its documented defaults rather than
  reimplementing the same logic by hand. Hand-rolled reimplementations
  frequently miss edge cases the library has already handled.

- **Handle realistic edge cases appropriate to the data format and
  domain.** Identify the edge cases that real user input for this
  specific task will contain, and handle them by construction rather
  than by accident. The set of relevant edge cases depends on the
  task — be conservative and assume user input will be messier than
  the parent implementations assumed.

- **Explicit error handling at boundaries.** Validate inputs before
  processing them. On failure, raise a clear error with enough context
  for the caller to understand what went wrong — do NOT silently
  return a plausible-looking default, a zero count, or an empty
  collection. The verification gate's Tier 3 adversarial prober will
  run the merged implementation against realistic reference inputs
  and flag any deterministic mismatch; silent failures will trip
  that check.

- **Never swallow exceptions to return a plausible-looking dict.**
  If the function can't complete its work, let the exception propagate
  so the calling pipeline sees a real error, not a synthesized "empty
  result" that masks the failure.

Where the two parents genuinely take different approaches to the same
step, prefer the one that is more robust under the criteria above.
If neither parent uses a robust pattern, introduce one — the merged
version should be stronger than either parent in isolation, not
merely the LCD of their shared logic.

"""


def _invoke_structured_with_repair(
    structured_llm,
    prompt: str,
    lf_config: dict,
    op: str,
    context: str,
):
    """Invoke a structured LLM with a one-shot repair pass on failure.

    Pattern lifted from the generator nodes — first attempt, on
    Pydantic validation / LLM error log a warning and re-invoke with
    a repair prompt appended. On repair failure, re-raise so the
    caller can classify the operation as "no-op, parents untouched".
    """
    try:
        return structured_llm.invoke(prompt, config=lf_config)
    except Exception as first_err:
        logger.warning(
            "%s LLM output invalid for %s, triggering repair pass: %s",
            op, context, first_err,
        )
        repair_prompt = (
            f"{prompt}\n\nPrevious attempt failed with: {first_err}\n"
            f"Return only valid JSON matching the schema above."
        )
        try:
            return structured_llm.invoke(repair_prompt, config=lf_config)
        except Exception as repair_err:
            logger.error(
                "%s repair pass failed for %s: %s", op, context, repair_err
            )
            raise


def _merge_tools(
    caps: list[CapabilityDescriptor],
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    llm_model: str,
    allow_network: bool = False,
) -> str | None:
    """Per-type merge for MCP_TOOL parents.

    Builds a prompt containing each parent's source_code (pulled from
    ``CapabilityDescriptor.metadata['source_code']``), asks the LLM
    for a single combined FastMCP source that handles the union of
    the parents' input spaces, packages the output as a
    ``GeneratedArtifact``, and routes it through
    :func:`verify_and_register_or_rollback`.

    On gate pass, deactivates both parents atomically and returns the
    new capability name. On gate fail, returns ``None`` and leaves the
    parents active.
    """
    llm = init_chat_model(llm_model or DEFAULT_LLM_MODEL)
    structured_llm = llm.with_structured_output(MergeToolsResult)
    lf_config = _langfuse_config()

    parent_blocks: list[str] = []
    for c in caps:
        source = (c.metadata or {}).get("source_code", "").strip()
        if source:
            parent_blocks.append(
                f"## {c.name}\n"
                f"Description: {c.description}\n"
                f"```python\n{source}\n```"
            )
        else:
            parent_blocks.append(
                f"## {c.name}\nDescription: {c.description}\n(no source code available)"
            )

    parents_section = "\n\n".join(parent_blocks)
    prompt = (
        "Merge the following MCP_TOOL capabilities into a single broader "
        "FastMCP tool that handles the union of their input spaces. "
        "The merged tool must follow the FastMCP pattern: `from "
        "mcp.server.fastmcp import FastMCP` + `mcp = FastMCP(...)` + "
        "an `@mcp.tool()`-decorated `async def` with `Annotated[type, "
        "Field(...)]` parameters + `if __name__ == '__main__': "
        "mcp.run()` at the end.\n\n"
        "Do NOT just concatenate the parent sources. Produce a single "
        "coherent function that subsumes both parents' logic.\n\n"
        "## Parent capabilities\n\n"
        f"{parents_section}\n\n"
        f"{_MERGE_ROBUSTNESS_HINT}"
        "Return a JSON object with fields: `name` (hyphen-case), "
        "`description`, `rationale`, `combined_source_code` (complete "
        "Python file), `combined_pip_packages` (list of pip install "
        "names, omit stdlib and mcp/pydantic)."
    )

    try:
        result: MergeToolsResult = _invoke_structured_with_repair(
            structured_llm, prompt, lf_config,
            op="merge(MCP_TOOL)", context=[c.name for c in caps],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_merge_tools: LLM repair failed for %s: %s",
                       [c.name for c in caps], exc)
        return None

    if registry.get(result.name) is not None:
        logger.warning(
            "_merge_tools: name '%s' already exists — aborting merge of %s",
            result.name, [c.name for c in caps],
        )
        return None

    # Union parent tags for the merged descriptor
    all_tags = list({tag for c in caps for tag in c.tags})

    artifact = GeneratedArtifact(
        capability_name=result.name,
        capability_type=CapabilityType.MCP_TOOL,
        description=result.description,
        source_code=result.combined_source_code,
        pip_packages=list(result.combined_pip_packages or []),
        gap_query=None,
        domain=caps[0].domain,
    )

    ok, reason = verify_and_register_or_rollback(
        artifact,
        registry,
        metrics_store,
        event_type="merged",
        event_details=f"from {[c.name for c in caps]}",
        tags=all_tags,
        domain=caps[0].domain,
        allow_network=allow_network,
    )
    if not ok:
        logger.warning(
            "_merge_tools: gate/register rejected '%s' — parents untouched: %s",
            result.name, reason,
        )
        return None

    # Atomically deactivate parents only after successful registration
    for c in caps:
        registry.deactivate(c.name)
        _record_and_emit_event(metrics_store, c.name, "merged", f"into {result.name}")

    logger.info("Merged MCP_TOOL %s → '%s'", [c.name for c in caps], result.name)
    return result.name


def _merge_skills(
    caps: list[CapabilityDescriptor],
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    llm_model: str,
    allow_network: bool = False,
) -> str | None:
    """Per-type merge for SKILL parents.

    Pulls each parent's ``skill_content`` from metadata, asks the LLM
    for a combined SKILL.md body, packages as a GeneratedArtifact,
    and routes through the gate. On pass, deactivates parents; on
    fail, leaves them active and returns None.
    """
    llm = init_chat_model(llm_model or DEFAULT_LLM_MODEL)
    structured_llm = llm.with_structured_output(MergeSkillsResult)
    lf_config = _langfuse_config()

    parent_blocks: list[str] = []
    for c in caps:
        content = (c.metadata or {}).get("skill_content", "").strip()
        if content:
            parent_blocks.append(
                f"## {c.name}\n"
                f"Description: {c.description}\n"
                f"```markdown\n{content}\n```"
            )
        else:
            parent_blocks.append(
                f"## {c.name}\nDescription: {c.description}\n(no skill content available)"
            )

    parents_section = "\n\n".join(parent_blocks)
    prompt = (
        "Merge the following SKILL capabilities into a single SKILL.md "
        "that covers the union of their workflows. The merged SKILL.md "
        "must have valid YAML frontmatter with `name`, `description`, "
        "and optionally `allowed-tools`, followed by instruction prose "
        "and optional fenced Python code blocks.\n\n"
        "Produce a coherent single skill, not a concatenation of two. "
        "The merged instructions should read as one unified workflow.\n\n"
        "## Parent capabilities\n\n"
        f"{parents_section}\n\n"
        f"{_MERGE_ROBUSTNESS_HINT}"
        "**Note on SKILL content shape.** Fenced Python code blocks in "
        "a SKILL.md are treated as executable surface by the verification "
        "gate — extracted by the layered code-fence scanner (Tier 1) and "
        "run through the adversarial prober (Tier 3). Any Python you "
        "embed is subject to the same robustness criteria above and "
        "will be tested against realistic reference inputs. Choose the "
        "content shape that best fits the merged workflow: embed Python "
        "only when the workflow is inherently code-shaped, otherwise "
        "prefer prose instructions that the executing agent can follow "
        "with its own tool-calling loop.\n\n"
        "Return a JSON object with fields: `name` (hyphen-case), "
        "`description`, `rationale`, `combined_skill_content` (full "
        "SKILL.md body including YAML frontmatter)."
    )

    try:
        result: MergeSkillsResult = _invoke_structured_with_repair(
            structured_llm, prompt, lf_config,
            op="merge(SKILL)", context=[c.name for c in caps],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_merge_skills: LLM repair failed for %s: %s",
                       [c.name for c in caps], exc)
        return None

    if registry.get(result.name) is not None:
        logger.warning(
            "_merge_skills: name '%s' already exists — aborting merge of %s",
            result.name, [c.name for c in caps],
        )
        return None

    all_tags = list({tag for c in caps for tag in c.tags})
    # Merge input/output schemas from parents (union). Empty if parents
    # had empty schemas — the gate still runs but Pillar 3 merge discovery
    # won't find partners for the merged capability until it accumulates
    # real invocations. That's acceptable for an optimizer output.
    merged_input_schema: dict[str, str] = {}
    merged_output_schema: dict[str, str] = {}
    for c in caps:
        for k, v in (c.input_schema or {}).items():
            merged_input_schema.setdefault(k, v)
        for k, v in (c.output_schema or {}).items():
            merged_output_schema.setdefault(k, v)

    artifact = GeneratedArtifact(
        capability_name=result.name,
        capability_type=CapabilityType.SKILL,
        description=result.description,
        skill_content=result.combined_skill_content,
        gap_query=None,
        input_schema=merged_input_schema,
        output_schema=merged_output_schema,
        domain=caps[0].domain,
    )

    ok, reason = verify_and_register_or_rollback(
        artifact,
        registry,
        metrics_store,
        event_type="merged",
        event_details=f"from {[c.name for c in caps]}",
        tags=all_tags,
        domain=caps[0].domain,
        allow_network=allow_network,
    )
    if not ok:
        logger.warning(
            "_merge_skills: gate/register rejected '%s' — parents untouched: %s",
            result.name, reason,
        )
        return None

    for c in caps:
        registry.deactivate(c.name)
        _record_and_emit_event(metrics_store, c.name, "merged", f"into {result.name}")

    logger.info("Merged SKILL %s → '%s'", [c.name for c in caps], result.name)
    return result.name


def _merge_agents(
    caps: list[CapabilityDescriptor],
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    llm_model: str,
    allow_network: bool = False,
) -> str | None:
    """Per-type merge for AGENT parents.

    Packages parent agent_configs (system prompts + subagents lists),
    asks the LLM to produce a single merged agent config, and routes
    the result through the gate. Enforces the budget ceiling: if the
    LLM returns more than ``_MAX_AGENT_SUBAGENT_BUDGET`` (=8) subagents,
    the merge is rejected without calling the gate — the parents stay
    active and None is returned. Rationale: a lead with more than 8
    subagents becomes a context-management liability (every task()
    call to a subagent is a separate DeepAgent invocation) and the
    proposal's Pillar 3 Merge is supposed to REDUCE complexity, not
    increase it.
    """
    llm = init_chat_model(llm_model or DEFAULT_LLM_MODEL)
    structured_llm = llm.with_structured_output(MergeAgentsResult)
    lf_config = _langfuse_config()

    parent_blocks: list[str] = []
    for c in caps:
        agent_cfg_raw = (c.metadata or {}).get("agent_config", "")
        try:
            agent_cfg = (
                json.loads(agent_cfg_raw)
                if isinstance(agent_cfg_raw, str) and agent_cfg_raw
                else (agent_cfg_raw if isinstance(agent_cfg_raw, dict) else {})
            )
        except Exception:  # noqa: BLE001
            agent_cfg = {}
        system_prompt = (agent_cfg.get("system_prompt") or "").strip()
        subagents = agent_cfg.get("subagents") or []
        sub_summary = (
            ", ".join(s.get("name", "?") for s in subagents)
            if subagents
            else "(solo agent, no subagents)"
        )
        parent_blocks.append(
            f"## {c.name}\n"
            f"Description: {c.description}\n"
            f"System prompt:\n```\n{system_prompt}\n```\n"
            f"Subagents: {sub_summary}"
        )

    parents_section = "\n\n".join(parent_blocks)
    prompt = (
        "Merge the following AGENT capabilities into a single lead agent "
        "that can adapt across the union of their problem spaces. The "
        "merged agent may delegate to subagents via the task() tool, "
        "but the total subagent count MUST NOT exceed "
        f"{_MAX_AGENT_SUBAGENT_BUDGET}. If the parents have overlapping "
        "subagent names, synthesize their prompts into a single merged "
        "subagent. If the parents have disjoint subagents, include the "
        "union (subject to the budget ceiling).\n\n"
        "## Parent capabilities\n\n"
        f"{parents_section}\n\n"
        f"{_MERGE_ROBUSTNESS_HINT}"
        "**Note on AGENT content shape.** The merged agent's "
        "`system_prompt` and each `subagent.system_prompt` are "
        "prose-only — no executable Python. The robustness criteria "
        "above apply to the **instructions** you give the agent: tell "
        "the agent to prefer well-known library patterns and robust "
        "error handling in whatever code it generates at runtime, "
        "rather than telling it to hand-roll logic that matches the "
        "parents' implementation detail. An agent following robust "
        "instructions will produce robust outputs at runtime; an agent "
        "following hand-rolled-pattern instructions will inherit the "
        "parents' edge-case failures. The `strongest behavioral "
        "patterns` criterion applies to the instruction content even "
        "though the agent config itself has no verifiable code "
        "surface.\n\n"
        "Return a JSON object with fields: `name` (hyphen-case), "
        "`description`, `rationale`, `combined_system_prompt` (full "
        "lead system prompt), `combined_subagents` (list of objects "
        "with `name`, `description`, `system_prompt`, `tools`). "
        "Subagent `tools` is typically `[\"sandbox_execute\"]`."
    )

    try:
        result: MergeAgentsResult = _invoke_structured_with_repair(
            structured_llm, prompt, lf_config,
            op="merge(AGENT)", context=[c.name for c in caps],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_merge_agents: LLM repair failed for %s: %s",
                       [c.name for c in caps], exc)
        return None

    if len(result.combined_subagents) > _MAX_AGENT_SUBAGENT_BUDGET:
        logger.warning(
            "_merge_agents: LLM returned %d subagents (> %d budget) — "
            "aborting merge of %s, parents stay active",
            len(result.combined_subagents),
            _MAX_AGENT_SUBAGENT_BUDGET,
            [c.name for c in caps],
        )
        return None

    if registry.get(result.name) is not None:
        logger.warning(
            "_merge_agents: name '%s' already exists — aborting merge of %s",
            result.name, [c.name for c in caps],
        )
        return None

    # Build the merged agent_config dict that the execution backend expects.
    agent_config: dict = {
        "system_prompt": result.combined_system_prompt,
        "is_deep_agent": True,
    }
    if result.combined_subagents:
        agent_config["subagents"] = [
            sa.model_dump() for sa in result.combined_subagents
        ]

    merged_input_schema: dict[str, str] = {}
    merged_output_schema: dict[str, str] = {}
    for c in caps:
        for k, v in (c.input_schema or {}).items():
            merged_input_schema.setdefault(k, v)
        for k, v in (c.output_schema or {}).items():
            merged_output_schema.setdefault(k, v)

    all_tags = list({tag for c in caps for tag in c.tags})

    artifact = GeneratedArtifact(
        capability_name=result.name,
        capability_type=CapabilityType.AGENT,
        description=result.description,
        agent_config=agent_config,
        input_schema=merged_input_schema,
        output_schema=merged_output_schema,
        gap_query=None,
        domain=caps[0].domain,
    )

    ok, reason = verify_and_register_or_rollback(
        artifact,
        registry,
        metrics_store,
        event_type="merged",
        event_details=f"from {[c.name for c in caps]}",
        tags=all_tags,
        domain=caps[0].domain,
        allow_network=allow_network,
    )
    if not ok:
        logger.warning(
            "_merge_agents: gate/register rejected '%s' — parents untouched: %s",
            result.name, reason,
        )
        return None

    for c in caps:
        registry.deactivate(c.name)
        _record_and_emit_event(metrics_store, c.name, "merged", f"into {result.name}")

    logger.info("Merged AGENT %s → '%s'", [c.name for c in caps], result.name)
    return result.name


def merge(
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    capability_ids: list[str],
    *,
    llm_model: str = "",
    allow_network: bool = False,
) -> str | None:
    """Merge multiple capabilities into a single broader capability.

    Dispatches by capability type to one of the per-type merge helpers:

    - All parents ``MCP_TOOL`` → :func:`_merge_tools`
    - All parents ``SKILL`` → :func:`_merge_skills`
    - All parents ``AGENT`` → :func:`_merge_agents`
    - Mixed types → ``ValueError`` (cross-type merge is Chapter 7
      future work — the merged executable content only makes sense
      within a single type).

    Each per-type helper produces a ``GeneratedArtifact`` with real
    executable content, routes it through
    :func:`verify_and_register_or_rollback`, and atomically deactivates
    the parents only after the gate passes. If the gate rejects the
    merged artifact, the function returns ``None`` and neither the
    new artifact nor the parents are touched. This is the atomic
    rollback guarantee the proposal's Pillar 2 contract requires for
    optimizer-generated capabilities.

    Args:
        registry: Skill registry.
        metrics_store: Metrics store for event recording.
        capability_ids: IDs of capabilities to merge (typically 2,
            but per-type helpers accept any ≥ 2).
        llm_model: Model identifier for init_chat_model. Empty string
            uses DEFAULT_LLM_MODEL.

    Returns:
        New merged capability name on gate pass + register success,
        ``None`` on gate rejection or any other non-fatal failure.

    Raises:
        KeyError: If any capability_id is not found in the registry.
        ValueError: If capabilities have mixed types (cross-type
            merge is explicitly unsupported — see "mixed types" above).
    """
    if len(capability_ids) < 2:
        raise ValueError(
            f"merge() requires at least 2 capabilities, got {len(capability_ids)}"
        )

    caps: list[CapabilityDescriptor] = []
    for cid in capability_ids:
        cap = registry.get(cid)
        if cap is None:
            raise KeyError(f"Capability '{cid}' not found in registry")
        caps.append(cap)

    types = {c.capability_type for c in caps}
    if len(types) > 1:
        type_names = sorted(t.value for t in types)
        raise ValueError(
            "merge() does not support cross-type merging — all parents "
            f"must be the same CapabilityType. Got: {type_names}"
        )

    cap_type = next(iter(types))
    if cap_type == CapabilityType.MCP_TOOL:
        return _merge_tools(
            caps, registry, metrics_store,
            llm_model=llm_model, allow_network=allow_network,
        )
    if cap_type == CapabilityType.SKILL:
        return _merge_skills(
            caps, registry, metrics_store,
            llm_model=llm_model, allow_network=allow_network,
        )
    if cap_type == CapabilityType.AGENT:
        return _merge_agents(
            caps, registry, metrics_store,
            llm_model=llm_model, allow_network=allow_network,
        )

    raise ValueError(f"merge() does not support capability_type={cap_type.value}")


# ---------------------------------------------------------------------------
# Step 8 + finding #4/#5 — per-type split helpers
# ---------------------------------------------------------------------------
#
# The split operation produces a ``GeneratedArtifact`` per variant with
# real executable content (not metadata-only stubs like the pre-2026-04-16
# code) and routes each through a five-phase atomic flow:
#
#   Phase A. Build N variants from the LLM output.
#   Phase B. ``gate.verify()`` each variant (no registration). If any
#            variant fails, abort without touching the registry.
#   Phase C. ``verify_coverage`` on all variants collectively. MCP_TOOL
#            runs the replay loop; SKILL/AGENT return vacuously passing
#            per the MCP_TOOL-only coverage scope (see §12 finding #5
#            in docs/MASTER_PLAN_2026_04_13.md — Pillar 1 × Pillar 3
#            tension analysis).
#   Phase D. Register each variant via ``_register_artifact_directly``
#            (no re-verification — Tier 3 is LLM-nondeterministic and
#            re-running risks breaking atomicity on stochastic variance).
#   Phase E. Deactivate the parent only after all variants registered.
#
# If any phase fails, the function returns ``None`` — parent stays
# active, no variants registered. This mirrors Step 6's merge atomicity
# guarantee at the multi-variant level.
#
# The robustness hint below is the split-side counterpart to Step 6's
# ``_MERGE_ROBUSTNESS_HINT`` — same four general principles (prefer
# library defaults, handle edge cases, explicit error handling, don't
# swallow exceptions), framed for split's specialize-to-subspace
# direction rather than merge's union-across-parents direction. It is
# deliberately domain-general for the same generalization-concern
# reasons documented in finding #4.


_SPLIT_ROBUSTNESS_HINT = """\
## Robustness requirements (Pillar 3 "strongest behavioral patterns")

The proposal's Pillar 3 Split clause asks for *"specialized variants
that target input subspaces where the parent capability breaks"*.
Each variant you produce is a **focused specialist** for one such
subspace — not a narrowing of the parent, but a reimplementation
optimized for the specific kind of input the parent was breaking on.
The criteria below apply to each variant independently and are
intentionally domain-general — they should guide every variant
regardless of whether the subspace involves data parsing, math, text
analysis, agentic reasoning, or any other task shape.

- **Prefer well-known, battle-tested library defaults over hand-rolled
  logic.** If a mature standard-library or widely-used third-party
  library already solves the task the variant is specialized for, use
  it with its documented defaults rather than reimplementing the same
  logic by hand. Hand-rolled reimplementations frequently miss edge
  cases the library has already handled — exactly the kind of failure
  that put the parent capability into the split candidate pool.

- **Handle the variant's subspace fully.** The parent was breaking on
  this subspace; your variant exists to fix it. Identify the edge
  cases real user input for this subspace will contain and handle
  them by construction. The variant should be measurably more robust
  on its declared subspace than the parent was.

- **Explicit error handling at boundaries.** Validate inputs before
  processing them. On failure, raise a clear error with enough
  context for the caller to understand what went wrong — do NOT
  silently return a plausible-looking default, a zero count, or an
  empty collection. The verification gate's Tier 3 adversarial
  prober will run the variant against realistic reference inputs and
  flag any deterministic mismatch; silent failures will trip that
  check.

- **Never swallow exceptions to return a plausible-looking dict.**
  If the variant can't complete its work, let the exception propagate
  so the calling pipeline sees a real error, not a synthesized "empty
  result" that masks the failure.

Each variant should be a **stronger, narrower reimplementation** of
the capability for its declared subspace — not a wrapper around the
parent, not a subset of the parent's logic, and not a concatenation
of parent code with extra guards. Write each variant as if it were
being authored from scratch to handle one specific kind of input
really well.

"""


def _summarize_failures_for_prompt(
    failures: list[InvocationRecord], max_cases: int = 5,
) -> str:
    """Build a human-readable failure-case summary for the split prompt.

    The LLM needs to see the parent's failure data to identify input
    subspaces. This helper formats the most recent failures (up to
    ``max_cases``) as a compact multi-line summary showing each
    failure's task_id, input_args, and either the error_message or
    the wrong actual_output — whichever is recorded.

    Returns "No recorded failure cases." if the list is empty, which
    signals to the LLM that it should rely on the parent's description
    alone to identify subspaces (degraded mode — split will still run
    but coverage verification will be vacuous since there's nothing
    to replay).
    """
    if not failures:
        return "No recorded failure cases. Identify subspaces from the parent's description alone."

    lines: list[str] = []
    for i, f in enumerate(failures[:max_cases], start=1):
        args_preview = (f.input_args or "")[:200]
        if f.error_message:
            mode = f"ERROR — {f.error_message[:200]}"
        elif f.actual_output:
            mode = f"WRONG OUTPUT — {f.actual_output[:200]}"
        else:
            mode = "UNKNOWN FAILURE MODE"
        lines.append(
            f"{i}. task_id={f.task_id}\n"
            f"   input_args: {args_preview}\n"
            f"   failure: {mode}"
        )
    if len(failures) > max_cases:
        lines.append(f"... plus {len(failures) - max_cases} more failure case(s) not shown.")
    return "\n".join(lines)


def _split_phase_b_verify_all(
    artifacts: list[GeneratedArtifact],
    context: str,
    *,
    allow_network: bool = False,
) -> tuple[bool, str | None]:
    """Phase B of the split flow: verify each variant via gate.verify() only.

    Returns ``(True, None)`` if all variants pass the three-tier gate,
    ``(False, reason)`` on the first failure. No registration happens
    here — callers that receive a True result proceed to phase C
    (coverage) and then phase D (``_register_artifact_directly``).

    ``context`` is the parent capability name for logging.
    """
    from src.verification.gate import verify as _gate_verify

    for artifact in artifacts:
        try:
            report = _gate_verify(artifact, allow_network=allow_network)
        except Exception as exc:  # noqa: BLE001
            reason = f"gate.verify raised on variant '{artifact.capability_name}': {exc}"
            logger.warning("split phase B (%s): %s", context, reason)
            return False, reason
        if not report.overall_passed:
            blocking = [
                f"tier{f.tier}/{f.severity.value}:{f.category}"
                for f in report.findings
                if f.severity in (Severity.CRITICAL, Severity.HIGH)
            ]
            reason = (
                f"variant '{artifact.capability_name}' failed gate: "
                f"{', '.join(blocking) if blocking else 'overall_passed=False with no blocking findings'}"
            )
            logger.warning("split phase B (%s): %s", context, reason)
            return False, reason
    return True, None


def _split_finalize(
    parent: CapabilityDescriptor,
    artifacts: list[GeneratedArtifact],
    registry: SkillRegistry,
    metrics_store: MetricsStore,
) -> list[str] | None:
    """Phases D + E: register each verified variant and deactivate parent.

    Called after phase B (gate) and phase C (coverage) have both
    returned clean. Registers each variant via
    :func:`_register_artifact_directly` (no re-verification), then
    deactivates the parent. Returns the list of new variant names.

    On any registration failure, attempts to roll back already-
    registered variants via ``registry.deactivate``. This should
    rarely happen because phase B already verified every artifact,
    but the fallback preserves the atomicity guarantee.
    """
    registered: list[str] = []
    for artifact in artifacts:
        ok, reason = _register_artifact_directly(
            artifact,
            registry,
            metrics_store,
            event_type="split_variant",
            event_details=f"split from {parent.name}",
            tags=list(parent.tags),
            domain=parent.domain,
        )
        if not ok:
            # Shouldn't happen because phase B already verified — but
            # if it does, roll back any variants we've already registered
            # in this phase so the parent can stay active cleanly.
            logger.error(
                "split phase D (%s): registration unexpectedly failed for "
                "'%s' after phase B verified it — %s. Rolling back %d "
                "already-registered variants.",
                parent.name, artifact.capability_name, reason, len(registered),
            )
            for prev_name in registered:
                try:
                    registry.deactivate(prev_name)
                    _record_and_emit_event(metrics_store, prev_name,
                        "split_rollback",
                        f"phase D registration failed on sibling '{artifact.capability_name}'",)
                except Exception as rollback_exc:  # noqa: BLE001
                    logger.error(
                        "split phase D (%s): rollback of '%s' also failed: %s",
                        parent.name, prev_name, rollback_exc,
                    )
            return None
        registered.append(artifact.capability_name)

    registry.deactivate(parent.name)
    _record_and_emit_event(metrics_store, parent.name, "split", f"into {registered}",)
    logger.info("Split %s %s → %s", parent.capability_type.value, parent.name, registered)
    return registered


def _split_tools(
    parent: CapabilityDescriptor,
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    llm_model: str,
    allow_network: bool = False,
) -> list[str] | None:
    """Per-type split for MCP_TOOL parents.

    Pulls recent failures from the metrics store, prompts the LLM with
    the parent's source + failure summary + robustness hint, packages
    each returned variant as a ``GeneratedArtifact``, and routes them
    through the five-phase atomic flow.

    Coverage verification is ACTIVE for MCP_TOOL splits — the collective
    :func:`verify_coverage` call replays each recorded failure case
    against each variant and asserts at least one variant fixes each
    failure mode. If coverage fails, the split is rejected and the
    parent stays active.

    Returns the list of new variant names on success, ``None`` on any
    gate/coverage/registration failure.
    """
    from src.verification.coverage import verify_coverage

    parent_source = (parent.metadata or {}).get("source_code", "").strip()
    if not parent_source:
        logger.warning(
            "_split_tools: parent '%s' has no source_code in metadata — "
            "cannot split without a reference implementation",
            parent.name,
        )
        return None

    failure_cases = metrics_store.get_recent_failures(parent.name, limit=10)
    failure_summary = _summarize_failures_for_prompt(failure_cases)

    llm = init_chat_model(llm_model or DEFAULT_LLM_MODEL)
    structured_llm = llm.with_structured_output(SplitToolsResult)
    lf_config = _langfuse_config()

    prompt = (
        "Split the following MCP_TOOL capability into 2-4 specialized "
        "variants, each targeting an input subspace where the parent "
        "has been breaking. Each variant must be a complete FastMCP "
        "source file: `from mcp.server.fastmcp import FastMCP` + `mcp "
        "= FastMCP(...)` + an `@mcp.tool()`-decorated `async def` with "
        "`Annotated[type, Field(...)]` parameters + `if __name__ == "
        "'__main__': mcp.run()` at the end.\n\n"
        "Each variant is a FOCUSED SPECIALIST for its declared subspace "
        "— not a narrowing of the parent and not a wrapper around it. "
        "Write each variant as if you were authoring it from scratch "
        "to handle one kind of input really well.\n\n"
        "## Parent capability\n\n"
        f"### {parent.name}\n"
        f"Description: {parent.description}\n"
        f"```python\n{parent_source}\n```\n\n"
        "## Recent failure cases\n\n"
        f"{failure_summary}\n\n"
        f"{_SPLIT_ROBUSTNESS_HINT}"
        "Return a JSON object with fields: `analysis` (string — your "
        "interpretation of what input subspaces the parent is breaking "
        "on, grounded in the failure cases above), and `variants` "
        "(list of 2-4 objects each with `name` (hyphen-case), "
        "`description`, `subspace` (what kind of input this variant "
        "handles), `source_code` (complete Python file), and "
        "`pip_packages` (list of pip install names, omit stdlib and "
        "mcp/pydantic))."
    )

    try:
        result: SplitToolsResult = _invoke_structured_with_repair(
            structured_llm, prompt, lf_config,
            op="split(MCP_TOOL)", context=parent.name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_split_tools: LLM repair failed for %s: %s", parent.name, exc)
        return None

    # Name collision check — all-or-nothing.
    for v in result.variants:
        if registry.get(v.name) is not None:
            logger.warning(
                "_split_tools: variant name '%s' already exists — aborting split of '%s'",
                v.name, parent.name,
            )
            return None

    # Phase A — build GeneratedArtifacts for each variant.
    artifacts: list[GeneratedArtifact] = []
    for v in result.variants:
        artifacts.append(
            GeneratedArtifact(
                capability_name=v.name,
                capability_type=CapabilityType.MCP_TOOL,
                description=v.description,
                source_code=v.source_code,
                pip_packages=list(v.pip_packages or []),
                gap_query=None,
                domain=parent.domain,
            )
        )

    # Phase B — verify each variant through the gate.
    ok, reason = _split_phase_b_verify_all(
        artifacts, parent.name, allow_network=allow_network,
    )
    if not ok:
        logger.warning(
            "_split_tools: phase B rejected split of %s — %s. Parent stays active.",
            parent.name, reason,
        )
        _record_and_emit_event(metrics_store, parent.name, "split_rollback", f"phase B: {reason}",)
        return None

    # Phase C — collective coverage verification (MCP_TOOL replay).
    try:
        coverage = verify_coverage(
            artifacts, failure_cases, allow_network=allow_network,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_split_tools: verify_coverage raised on %s: %s. Parent stays active.",
            parent.name, exc,
        )
        _record_and_emit_event(metrics_store, parent.name, "split_rollback", f"phase C exception: {exc}",)
        return None

    if not coverage.all_covered:
        logger.warning(
            "_split_tools: coverage rejected split of %s — status=%s, "
            "uncovered_cases=%s, execution_errors=%d. Parent stays active.",
            parent.name, coverage.status,
            coverage.uncovered_cases, len(coverage.execution_errors),
        )
        _record_and_emit_event(metrics_store, parent.name,
            "split_rollback",
            (
                f"phase C coverage: status={coverage.status}, "
                f"uncovered={len(coverage.uncovered_cases)}"
            ),
        )
        return None

    # Phases D + E — register variants and deactivate parent.
    return _split_finalize(parent, artifacts, registry, metrics_store)


def _split_skills(
    parent: CapabilityDescriptor,
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    llm_model: str,
    allow_network: bool = False,
) -> list[str] | None:
    """Per-type split for SKILL parents.

    Same five-phase flow as :func:`_split_tools` but for SKILL parents.
    Coverage verification in phase C dispatches to
    ``CoverageReport(all_covered=True, status="unverifiable_type")``
    per the MCP_TOOL-only scope documented in §12 finding #5 — SKILL
    splits rely on the three-tier gate's per-variant safety check
    alone, the same way SKILL merge already does in Step 6. The
    coverage call is still made for consistency (and so any future
    Chapter 7 work that adds SKILL coverage drops in cleanly) but
    currently it's vacuous.
    """
    from src.verification.coverage import verify_coverage

    parent_content = (parent.metadata or {}).get("skill_content", "").strip()
    if not parent_content:
        logger.warning(
            "_split_skills: parent '%s' has no skill_content in metadata — "
            "cannot split without a reference implementation",
            parent.name,
        )
        return None

    failure_cases = metrics_store.get_recent_failures(parent.name, limit=10)
    failure_summary = _summarize_failures_for_prompt(failure_cases)

    llm = init_chat_model(llm_model or DEFAULT_LLM_MODEL)
    structured_llm = llm.with_structured_output(SplitSkillsResult)
    lf_config = _langfuse_config()

    prompt = (
        "Split the following SKILL capability into 2-4 specialized "
        "variants, each targeting an input subspace where the parent "
        "has been breaking. Each variant must be a complete SKILL.md "
        "body with valid YAML frontmatter (`name`, `description`, and "
        "optionally `allowed-tools`) followed by instruction prose and "
        "optional fenced Python code blocks.\n\n"
        "Each variant is a FOCUSED SPECIALIST for its declared subspace "
        "— not a narrowing of the parent, but a coherent specialized "
        "skill. Write each variant as if it were being authored from "
        "scratch for one kind of input.\n\n"
        "## Parent capability\n\n"
        f"### {parent.name}\n"
        f"Description: {parent.description}\n"
        f"```markdown\n{parent_content}\n```\n\n"
        "## Recent failure cases\n\n"
        f"{failure_summary}\n\n"
        f"{_SPLIT_ROBUSTNESS_HINT}"
        "**Note on SKILL content shape.** Fenced Python code blocks in "
        "a SKILL.md are treated as executable surface by the "
        "verification gate — extracted by the layered code-fence "
        "scanner (Tier 1) and run through the adversarial prober "
        "(Tier 3). Any Python you embed in a variant is subject to "
        "the same robustness criteria above and will be tested against "
        "realistic reference inputs. Choose the content shape that "
        "best fits the variant's subspace: embed Python only when the "
        "workflow is inherently code-shaped, otherwise prefer prose "
        "instructions the executing agent can follow with its own "
        "tool-calling loop.\n\n"
        "Return a JSON object with fields: `analysis` (string — your "
        "interpretation of the input subspaces the parent is breaking "
        "on, grounded in the failure cases above), and `variants` "
        "(list of 2-4 objects each with `name` (hyphen-case), "
        "`description`, `subspace`, and `skill_content` (full SKILL.md "
        "body including YAML frontmatter))."
    )

    try:
        result: SplitSkillsResult = _invoke_structured_with_repair(
            structured_llm, prompt, lf_config,
            op="split(SKILL)", context=parent.name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_split_skills: LLM repair failed for %s: %s", parent.name, exc)
        return None

    for v in result.variants:
        if registry.get(v.name) is not None:
            logger.warning(
                "_split_skills: variant name '%s' already exists — aborting split of '%s'",
                v.name, parent.name,
            )
            return None

    # Phase A — build GeneratedArtifacts. SKILL input/output schemas
    # are inherited from the parent's declaration (the LLM doesn't
    # need to re-derive them — they're stable across variants because
    # the input/output contract is a parent-level property).
    artifacts: list[GeneratedArtifact] = []
    for v in result.variants:
        artifacts.append(
            GeneratedArtifact(
                capability_name=v.name,
                capability_type=CapabilityType.SKILL,
                description=v.description,
                skill_content=v.skill_content,
                gap_query=None,
                input_schema=dict(parent.input_schema or {}),
                output_schema=dict(parent.output_schema or {}),
                domain=parent.domain,
            )
        )

    # Phase B — gate per variant.
    ok, reason = _split_phase_b_verify_all(
        artifacts, parent.name, allow_network=allow_network,
    )
    if not ok:
        logger.warning(
            "_split_skills: phase B rejected split of %s — %s. Parent stays active.",
            parent.name, reason,
        )
        _record_and_emit_event(metrics_store, parent.name, "split_rollback", f"phase B: {reason}",)
        return None

    # Phase C — coverage dispatches to unverifiable_type (vacuous pass)
    # per MCP_TOOL-only scope. Still called for consistency with the
    # MCP_TOOL path and to make future Chapter 7 extensions drop in.
    coverage = verify_coverage(
        artifacts, failure_cases, allow_network=allow_network,
    )
    if not coverage.all_covered:
        # Should be impossible for SKILL (coverage is vacuous) but
        # guarded for robustness — if verify_coverage ever tightens
        # for SKILL, this branch catches it cleanly.
        logger.warning(
            "_split_skills: coverage unexpectedly rejected SKILL split of %s — "
            "status=%s. Parent stays active.",
            parent.name, coverage.status,
        )
        _record_and_emit_event(metrics_store, parent.name, "split_rollback", f"phase C (SKILL): {coverage.status}",
        )
        return None

    return _split_finalize(parent, artifacts, registry, metrics_store)


def _split_agents(
    parent: CapabilityDescriptor,
    registry: SkillRegistry,
    metrics_store: MetricsStore,
    *,
    llm_model: str,
    allow_network: bool = False,
) -> list[str] | None:
    """Per-type split for AGENT parents.

    Same five-phase flow as :func:`_split_tools` / :func:`_split_skills`
    but for AGENT parents. Coverage is vacuous for the same reason
    (MCP_TOOL-only scope). Enforces a **global** subagent budget
    ceiling: the sum of subagent counts across ALL variants must not
    exceed ``_MAX_AGENT_SUBAGENT_BUDGET`` (=8), matching the
    per-operation ceiling that ``_merge_agents`` enforces on merge.
    Splitting a single AGENT into 4 variants each with 3 subagents
    would blow up to 12 total subagents, which would be more context-
    management complexity than the original — defeating the point of
    splitting.
    """
    from src.verification.coverage import verify_coverage

    parent_cfg_raw = (parent.metadata or {}).get("agent_config", "")
    try:
        parent_cfg = (
            json.loads(parent_cfg_raw)
            if isinstance(parent_cfg_raw, str) and parent_cfg_raw
            else (parent_cfg_raw if isinstance(parent_cfg_raw, dict) else {})
        )
    except Exception:  # noqa: BLE001
        parent_cfg = {}

    parent_system_prompt = (parent_cfg.get("system_prompt") or "").strip()
    parent_subagents = parent_cfg.get("subagents") or []
    if not parent_system_prompt:
        logger.warning(
            "_split_agents: parent '%s' has no system_prompt in agent_config — "
            "cannot split without a reference implementation",
            parent.name,
        )
        return None

    parent_sub_summary = (
        ", ".join(s.get("name", "?") for s in parent_subagents)
        if parent_subagents
        else "(solo agent, no subagents)"
    )

    failure_cases = metrics_store.get_recent_failures(parent.name, limit=10)
    failure_summary = _summarize_failures_for_prompt(failure_cases)

    llm = init_chat_model(llm_model or DEFAULT_LLM_MODEL)
    structured_llm = llm.with_structured_output(SplitAgentsResult)
    lf_config = _langfuse_config()

    prompt = (
        "Split the following AGENT capability into 2-4 specialized "
        "variants, each targeting an input subspace where the parent "
        "has been breaking. Each variant is a complete agent config: "
        "a lead `system_prompt` plus optional `subagents`.\n\n"
        "Each variant is a FOCUSED SPECIALIST for its declared subspace "
        "— not a narrowing of the parent, but a coherent specialized "
        "agent. The lead system prompts and subagent prompts should "
        "describe the adaptive reasoning the agent should perform on "
        "its one specific subspace.\n\n"
        "**CRITICAL budget constraint.** The total subagent count "
        "across ALL variants combined MUST NOT exceed "
        f"{_MAX_AGENT_SUBAGENT_BUDGET}. If you propose 4 variants, you "
        f"have at most {_MAX_AGENT_SUBAGENT_BUDGET} total subagent "
        "slots to distribute across them (on average 2 subagents per "
        "variant, though uneven distribution is fine). Splitting an "
        "AGENT is supposed to REDUCE per-variant reasoning complexity, "
        "not multiply it — variants that each have 3+ subagents would "
        "make the resulting registry more complex than the parent was, "
        "defeating the point.\n\n"
        "## Parent capability\n\n"
        f"### {parent.name}\n"
        f"Description: {parent.description}\n"
        f"System prompt:\n```\n{parent_system_prompt}\n```\n"
        f"Subagents: {parent_sub_summary}\n\n"
        "## Recent failure cases\n\n"
        f"{failure_summary}\n\n"
        f"{_SPLIT_ROBUSTNESS_HINT}"
        "**Note on AGENT content shape.** Each variant's `system_prompt` "
        "and each subagent's `system_prompt` are prose-only — no "
        "executable Python. The robustness criteria above apply to the "
        "**instructions** you give the agent: tell it to prefer "
        "well-known library patterns and robust error handling in "
        "whatever code it generates at runtime, not to hand-roll logic "
        "that matches the parent's implementation detail. An agent "
        "following robust instructions will produce robust outputs at "
        "runtime; an agent following hand-rolled-pattern instructions "
        "will inherit the parent's edge-case failures.\n\n"
        "Return a JSON object with fields: `analysis` (string — your "
        "interpretation of the input subspaces the parent is breaking "
        "on, grounded in the failure cases above), and `variants` "
        "(list of 2-4 objects each with `name` (hyphen-case), "
        "`description`, `subspace`, `system_prompt`, and `subagents` "
        "(list of objects with `name`, `description`, `system_prompt`, "
        "`tools`))."
    )

    try:
        result: SplitAgentsResult = _invoke_structured_with_repair(
            structured_llm, prompt, lf_config,
            op="split(AGENT)", context=parent.name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_split_agents: LLM repair failed for %s: %s", parent.name, exc)
        return None

    # Global budget ceiling check — BEFORE the gate, before any
    # registration. Cheaper to reject an over-budget plan than run
    # Tier 3 on every over-complex variant.
    total_subagents = sum(len(v.subagents) for v in result.variants)
    if total_subagents > _MAX_AGENT_SUBAGENT_BUDGET:
        logger.warning(
            "_split_agents: LLM returned %d total subagents across %d variants "
            "(> %d global budget) — aborting split of %s, parent stays active",
            total_subagents,
            len(result.variants),
            _MAX_AGENT_SUBAGENT_BUDGET,
            parent.name,
        )
        _record_and_emit_event(metrics_store, parent.name,
            "split_rollback",
            f"subagent budget exceeded: {total_subagents} > {_MAX_AGENT_SUBAGENT_BUDGET}",)
        return None

    for v in result.variants:
        if registry.get(v.name) is not None:
            logger.warning(
                "_split_agents: variant name '%s' already exists — aborting split of '%s'",
                v.name, parent.name,
            )
            return None

    # Phase A — build GeneratedArtifacts. Like SKILL splitting, AGENT
    # variants inherit the parent's input/output contract; the LLM is
    # specializing behavior, not redefining the callable schema.
    artifacts: list[GeneratedArtifact] = []
    for v in result.variants:
        agent_config: dict = {
            "system_prompt": v.system_prompt,
            "is_deep_agent": True,
        }
        if v.subagents:
            agent_config["subagents"] = [sa.model_dump() for sa in v.subagents]
        artifacts.append(
            GeneratedArtifact(
                capability_name=v.name,
                capability_type=CapabilityType.AGENT,
                description=v.description,
                agent_config=agent_config,
                gap_query=None,
                input_schema=dict(parent.input_schema or {}),
                output_schema=dict(parent.output_schema or {}),
                domain=parent.domain,
            )
        )

    # Phase B — gate per variant.
    ok, reason = _split_phase_b_verify_all(
        artifacts, parent.name, allow_network=allow_network,
    )
    if not ok:
        logger.warning(
            "_split_agents: phase B rejected split of %s — %s. Parent stays active.",
            parent.name, reason,
        )
        _record_and_emit_event(metrics_store, parent.name, "split_rollback", f"phase B: {reason}",)
        return None

    # Phase C — vacuous for AGENT per MCP_TOOL-only scope.
    coverage = verify_coverage(
        artifacts, failure_cases, allow_network=allow_network,
    )
    if not coverage.all_covered:
        logger.warning(
            "_split_agents: coverage unexpectedly rejected AGENT split of %s — "
            "status=%s. Parent stays active.",
            parent.name, coverage.status,
        )
        _record_and_emit_event(metrics_store, parent.name, "split_rollback", f"phase C (AGENT): {coverage.status}",
        )
        return None

    return _split_finalize(parent, artifacts, registry, metrics_store)
