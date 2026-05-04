"""Compositional execution with dependency ordering and workspace state passing.

Runs every resolved capability for a decomposed task in topological order,
passing intermediate results via a shared workspace directory
(``/tmp/mint_workspace_<task_id>/``).

Workspace layout::

    /tmp/mint_workspace_<id>/
        fixtures/          ← original input files
        step_0_output.json ← output of step 0
        step_1_output.json ← output of step 1
        ...

MCP_TOOL steps mount the workspace into Docker at ``/workspace`` and read/write
step output files there. SKILL and AGENT steps receive upstream outputs through
``fixture_contents`` injection instead of a Docker workspace mount.
"""

from __future__ import annotations

import base64
import graphlib
import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.schemas.capability import CapabilityType
from src.schemas.task import (
    CapabilityReference,
    ChainResult,
    GeneratedArtifact,
    MatchItem,
    StepTiming,
    SubTask,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Resolution map construction
# ---------------------------------------------------------------------------


def build_resolution_map(
    required_capabilities: list[str],
    matches: list[MatchItem],
    artifacts: list[GeneratedArtifact],
) -> dict[str, CapabilityReference]:
    """Build a resolution map from pipeline state.

    Maps each ``required_capability`` name to a :class:`CapabilityReference`
    pointing at either a registry match or a generated artifact.
    """
    resolution: dict[str, CapabilityReference] = {}

    # Index matches by the original query string they answered.
    # MatchItem.query is the required_capability string from the decomposition;
    # MatchItem.capability.name is the registry name (may differ).
    match_by_query: dict[str, MatchItem] = {}
    for m in matches:
        if m.query:
            match_by_query[m.query] = m
        else:
            # Compatibility fallback for MatchItems without a query field.
            match_by_query[m.capability.name] = m

    # Index artifacts by gap_query (the required_capability string that
    # caused the gap that led to this artifact).
    artifact_by_query: dict[str, GeneratedArtifact] = {}
    for art in artifacts:
        if art.gap_query:
            artifact_by_query[art.gap_query] = art
        # Lead-with-subagents: the lead artifact covers multiple gaps
        if art.agent_config and art.agent_config.get("subagent_gap_queries"):
            for sub_query in art.agent_config["subagent_gap_queries"]:
                artifact_by_query[sub_query] = art

    for cap_name in required_capabilities:
        # Prefer registry match over generated artifact
        if cap_name in match_by_query:
            m = match_by_query[cap_name]
            resolution[cap_name] = CapabilityReference(
                source="registry",
                capability_name=m.capability.name,
                capability_type=m.capability.capability_type,
                score=m.score,
            )
        elif cap_name in artifact_by_query:
            art = artifact_by_query[cap_name]
            resolution[cap_name] = CapabilityReference(
                source="generated",
                capability_name=art.capability_name,
                capability_type=art.capability_type,
            )

    return resolution


# ---------------------------------------------------------------------------
# CompositionalExecutor
# ---------------------------------------------------------------------------


class CompositionalExecutor:
    """Execute a capability chain in dependency order with workspace state passing."""

    def __init__(
        self,
        subtasks: list[SubTask],
        resolution: dict[str, CapabilityReference],
        artifacts: list[GeneratedArtifact],
        matches: list[MatchItem],
        fixture_paths: list[str | Path],
        task: Any,
        *,
        metrics_store: Any | None = None,
        langfuse_tags: list[str] | None = None,
        langfuse_session_id: str | None = None,
        langfuse_user_id: str | None = None,
        allow_network: bool = False,
    ) -> None:
        self.subtasks = subtasks
        self.resolution = resolution
        self.artifacts = artifacts
        self.fixture_paths = [Path(p) for p in fixture_paths]
        self.task = task
        self.metrics_store = metrics_store
        self.langfuse_tags = langfuse_tags
        self.langfuse_session_id = langfuse_session_id
        # Eval harness runs can pass their own user_id so traces aggregate
        # separately from direct programmatic runs.
        self.langfuse_user_id = langfuse_user_id or "runtime"
        # Task-level network policy threaded into every step executor
        # (MCP_TOOL / SKILL / AGENT).
        self.allow_network = allow_network

        # Index artifacts by gap_query for quick lookup
        self._artifact_by_query: dict[str, GeneratedArtifact] = {}
        for art in artifacts:
            if art.gap_query:
                self._artifact_by_query[art.gap_query] = art

        # Index matches by capability name
        self._match_by_name: dict[str, MatchItem] = {}
        for m in matches:
            self._match_by_name[m.capability.name] = m

        # Per-step sticky failure reason, set by backends that can report a
        # structured timeout/error. Reset at the start of every step in
        # ``run()`` and propagated onto ``ChainResult.failed_step_reason``.
        self._last_step_failure_reason: str | None = None

    # ------------------------------------------------------------------
    # DAG construction
    # ------------------------------------------------------------------

    def build_execution_order(self) -> list[int] | None:
        """Return subtask indices in topological (dependency) order.

        Invalid DAGs fail closed instead of falling back to linear order or
        crashing the execution loop. Returns ``None`` on:

        - **Out-of-range index**: any ``depends_on`` value outside
          ``0 <= idx < len(subtasks)``.
        - **Self-loop**: a subtask depending on itself.
        - **Cycle**: ``graphlib.CycleError`` from the topological sort.

        Callers must treat ``None`` as a chain-fatal error and record a
        descriptive ``failed_step_reason``.
        """
        n = len(self.subtasks)
        if n <= 1:
            return list(range(n))

        # Bounds + self-loop validation BEFORE topological sort. graphlib
        # accepts nonexistent predecessors silently (they become
        # no-predecessor nodes) which is the root cause of the phantom-
        # execution bug.
        for i, st in enumerate(self.subtasks):
            for dep in st.depends_on:
                if not (0 <= dep < n):
                    logger.error(
                        "Subtask %d has out-of-range depends_on index %d "
                        "(valid range: 0..%d). Failing chain closed.",
                        i, dep, n - 1,
                    )
                    return None
                if dep == i:
                    logger.error(
                        "Subtask %d depends on itself (self-loop). "
                        "Failing chain closed.",
                        i,
                    )
                    return None

        graph: dict[int, set[int]] = {}
        for i, st in enumerate(self.subtasks):
            graph[i] = set(st.depends_on)

        try:
            sorter = graphlib.TopologicalSorter(graph)
            return list(sorter.static_order())
        except graphlib.CycleError as exc:
            logger.error(
                "Cycle detected in subtask dependencies: %s. "
                "Failing chain closed (no linear-order fallback).",
                exc,
            )
            return None

    def _collect_transitive_depends_on(self, step_index: int) -> list[int] | None:
        """Return the transitive predecessor closure for ``step_index``.

        The decomposer's coherence contract is transitive: a downstream subtask
        may require keys produced by any ancestor reachable through
        ``depends_on``. MCP_TOOL generation is prompted with the same contract,
        describing ``upstream_data`` as "all upstream data". To avoid unrelated
        parallel-branch state while still honoring that contract, the sandbox
        receives the transitive closure of declared predecessors.

        Returns ``None`` for invalid indices so callers can fall back to the
        pre-existing fail-safe behaviour.
        """
        if not (0 <= step_index < len(self.subtasks)):
            return None

        visited: set[int] = set()
        queue: list[int] = list(self.subtasks[step_index].depends_on)

        while queue:
            dep = queue.pop(0)
            if dep in visited:
                continue
            if not (0 <= dep < len(self.subtasks)):
                # ``build_execution_order`` already fails closed on malformed
                # DAGs; keep this helper defensive and skip invalid edges.
                continue
            visited.add(dep)
            queue.extend(self.subtasks[dep].depends_on)

        return sorted(visited)

    # ------------------------------------------------------------------
    # Workspace management
    # ------------------------------------------------------------------

    def build_workspace(self, task_id: str | None = None) -> Path:
        """Create workspace directory with fixtures copied into ``fixtures/``.

        Directories and files are world-readable (0o755 / 0o644) so the
        Docker sandbox user can read from the ``/workspace`` mount.
        """
        ws_id = task_id or uuid.uuid4().hex[:12]
        workspace = Path(tempfile.mkdtemp(prefix=f"mint_workspace_{ws_id}_"))
        workspace.chmod(0o755)

        fixtures_dir = workspace / "fixtures"
        fixtures_dir.mkdir(mode=0o755)
        for fp in self.fixture_paths:
            if fp.exists():
                dest = fixtures_dir / fp.name
                shutil.copy2(fp, dest)
                dest.chmod(0o644)

        return workspace

    @staticmethod
    def cleanup_workspace(workspace: Path) -> None:
        """Remove the workspace directory tree."""
        try:
            shutil.rmtree(workspace, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _collect_output_files(workspace: Path) -> dict[str, bytes]:
        """Scan workspace for arbitrary files written by the last step.

        Returns a mapping of filename → raw bytes. Collects any file at
        the workspace root that is NOT a known intermediate (``step_N_output.json``
        files written by the compositional executor itself) or the
        ``fixtures/`` directory.

        The collector is filename-agnostic: generated writer steps should emit
        exactly the filename declared by the task's output contract, not just
        ``output.*``.
        """
        output_files: dict[str, bytes] = {}
        try:
            for candidate in workspace.iterdir():
                if not candidate.is_file():
                    continue
                # Skip intermediate step-output JSON files — those are
                # serialized dict outputs the executor itself wrote for
                # downstream MCP_TOOL steps to read.
                if (
                    candidate.name.startswith("step_")
                    and candidate.name.endswith("_output.json")
                ):
                    continue
                output_files[candidate.name] = candidate.read_bytes()
                logger.info(
                    "Collected output file: %s (%d bytes)",
                    candidate.name, len(output_files[candidate.name]),
                )
        except OSError as exc:
            logger.warning("Failed to scan workspace for output files: %s", exc)
        return output_files

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _resolve_step(
        self, subtask: SubTask
    ) -> tuple[CapabilityReference | None, Any]:
        """Find the resolution and artifact/match for a subtask's primary capability.

        Returns ``(reference, artifact_or_match)`` or ``(None, None)``
        if no capability could be resolved.
        """
        for cap_name in subtask.required_capabilities:
            ref = self.resolution.get(cap_name)
            if ref is None:
                continue

            if ref.source == "generated":
                art = self._artifact_by_query.get(cap_name)
                if art is None:
                    # Fallback: match by capability_name
                    art = next(
                        (a for a in self.artifacts if a.capability_name == ref.capability_name),
                        None,
                    )
                if art is not None:
                    return ref, art
            elif ref.source == "registry":
                match = self._match_by_name.get(ref.capability_name)
                if match is not None:
                    return ref, match

        return None, None

    # ------------------------------------------------------------------
    # Fixture / upstream output helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _save_step_output(workspace: Path, step_index: int, output: dict) -> None:
        """Write step output to workspace for downstream steps.

        File is world-readable (0o644) so Docker sandbox user can access it.
        """
        output_file = workspace / f"step_{step_index}_output.json"
        try:
            output_file.write_text(json.dumps(output, default=str))
            output_file.chmod(0o644)
        except (TypeError, OSError) as exc:
            logger.warning("Failed to write step %d output: %s", step_index, exc)

    @staticmethod
    def _build_chain_fixture_contents(
        workspace: Path,
        upstream_outputs: dict[int, dict],
    ) -> dict[str, str]:
        """Build fixture contents dict combining original fixtures and upstream outputs.

        Used for SKILL and AGENT steps where upstream data is injected via
        ``fixture_contents`` rather than via Docker workspace mount.
        """
        contents: dict[str, str] = {}

        # Original fixture files
        fixtures_dir = workspace / "fixtures"
        if fixtures_dir.exists():
            for f in sorted(fixtures_dir.iterdir()):
                if f.is_file():
                    try:
                        contents[f.name] = f.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass

        # Upstream outputs as JSON strings
        for idx, output in sorted(upstream_outputs.items()):
            contents[f"step_{idx}_output.json"] = json.dumps(output, default=str)

        return contents

    @staticmethod
    def _match_to_artifact(match: MatchItem, ref: CapabilityReference) -> GeneratedArtifact:
        """Reconstruct a ``GeneratedArtifact`` proxy from a registry match.

        Registry metadata stores generated artifacts in descriptor form. This
        method rebuilds the artifact object needed by the execution backends,
        using the shared agent-config decoder so registry matches and generated
        artifacts follow the same schema policy.
        """
        from src.schemas.task import decode_agent_config_metadata

        meta = getattr(match.capability, "metadata", {}) or {}
        # Restore pip_packages from the descriptor so registry-matched MCP
        # tools install their declared dependencies in the sandbox.
        pip_packages = list(getattr(match.capability, "pip_packages", []) or [])
        return GeneratedArtifact(
            capability_name=ref.capability_name,
            capability_type=ref.capability_type,
            source_code=meta.get("source_code"),
            skill_content=meta.get("skill_content"),
            agent_config=decode_agent_config_metadata(meta.get("agent_config")),
            description=getattr(match.capability, "description", None),
            pip_packages=pip_packages,
            generated_at=datetime.now(UTC),
        )

    # ------------------------------------------------------------------
    # Invocation recording
    # ------------------------------------------------------------------

    def _record_step_invocation(
        self,
        ref: CapabilityReference,
        output: dict,
        latency_ms: float,
        task_id: str,
        *,
        upstream_outputs: dict | None = None,
    ) -> None:
        """Record a real invocation for this chain step in the metrics store.

        Populates ``input_args`` / ``actual_output`` / ``error_message`` so the
        row survives ``MetricsStore.get_recent_failures`` and can be used by
        the failure-informed optimizer path.

        ``input_args`` captures the upstream step outputs that feed this
        step — the closest Python-side proxy for the literal kwargs the
        capability consumed. Chain-step replay by the coverage verifier
        requires matching the sandbox's kwargs-construction logic
        (``tests/evaluation/execution.py::execute_mcp_tool_in_chain``);
        direct-invocation replay on rows where ``input_args`` already
        matches the function's parameter names works today.
        """
        if self.metrics_store is None:
            return
        try:
            from src.registry.metrics import (
                extract_error_message,
                looks_like_error,
                serialize_for_invocation,
            )
            from src.schemas.metrics import InvocationRecord

            is_error_shape = looks_like_error(output)
            success = bool(output) and not is_error_shape
            error_message = extract_error_message(output) if is_error_shape else None

            record = InvocationRecord(
                capability_id=ref.capability_name,
                task_id=task_id[:64],
                success=success,
                token_cost=0,
                latency_ms=latency_ms,
                input_args=serialize_for_invocation(upstream_outputs),
                actual_output=serialize_for_invocation(output),
                error_message=error_message,
            )
            self.metrics_store.record_invocation(record)
        except Exception:  # noqa: BLE001
            logger.debug(
                "Failed to record invocation for %s", ref.capability_name, exc_info=True
            )

    # ------------------------------------------------------------------
    # Per-type step execution
    # ------------------------------------------------------------------

    def _execute_mcp_tool_step(
        self,
        artifact_or_match: Any,
        ref: CapabilityReference,
        step_index: int,
        workspace: Path,
        subtask: SubTask | None = None,
    ) -> dict:
        from tests.evaluation.execution import execute_mcp_tool_in_chain

        if ref.source == "generated":
            source_code = artifact_or_match.source_code
            pip_packages = artifact_or_match.pip_packages
        else:
            meta = getattr(artifact_or_match.capability, "metadata", {}) or {}
            source_code = meta.get("source_code")
            # Registry-matched MCP tools need their persisted dependency list
            # restored before sandbox execution.
            pip_packages = list(
                getattr(artifact_or_match.capability, "pip_packages", []) or []
            )

        if not source_code:
            logger.warning(
                "No source code for MCP_TOOL step %d (%s)", step_index, ref.capability_name
            )
            return {}

        # Restrict sandbox state to declared predecessors, not every prior
        # step's output. Use the transitive closure rather than only direct
        # dependencies so downstream MCP_TOOL writers receive the same "all
        # upstream data" contract enforced by decomposition/generation.
        depends_on = (
            self._collect_transitive_depends_on(step_index)
            if subtask is not None
            else None
        )

        return execute_mcp_tool_in_chain(
            source_code=source_code,
            step_index=step_index,
            workspace_path=workspace,
            pip_packages=pip_packages,
            capability_name=ref.capability_name,
            allow_network=self.allow_network,
            depends_on=depends_on,
        )

    def _execute_skill_step(
        self,
        artifact_or_match: Any,
        ref: CapabilityReference,
        workspace: Path,
        upstream_outputs: dict[int, dict],
        subtask: SubTask | None = None,
    ) -> dict:
        from tests.evaluation.execution import execute_skill_artifact

        fixture_contents = self._build_chain_fixture_contents(workspace, upstream_outputs)
        artifact = (
            artifact_or_match
            if ref.source == "generated"
            else self._match_to_artifact(artifact_or_match, ref)
        )
        output_schema = self._collect_union_output_schema(
            artifact=artifact, current_ref=ref,
        ) if subtask else None

        return execute_skill_artifact(
            artifact,
            self.task,
            fixture_contents,
            langfuse_tags=self.langfuse_tags,
            langfuse_session_id=self.langfuse_session_id,
            output_schema=output_schema,
            allow_network=self.allow_network,
        )

    def _is_fully_collapsed_lead_artifact(
        self,
        *,
        artifact: Any,
        current_ref: CapabilityReference,
    ) -> bool:
        """Whether every resolved subtask maps to the same lead-with-subagents artifact.

        In this shape there are no real runtime handoff boundaries between the
        decomposer's conceptual subtasks: the lead executes once, its output is
        cached, and that same payload is reused for every conceptual step. Using
        per-subtask or union-of-subtasks ``output_schema`` as the lead's
        ``response_format`` therefore over-constrains the lead to surface
        intermediate subagent state that never leaves the lead's private context.

        When this returns true, the agent should be validated against the
        task-level result model instead of a synthetic handoff schema, and the
        per-subtask runtime output-schema compliance check should be skipped.
        """
        if not (
            isinstance(artifact, GeneratedArtifact)
            and getattr(artifact, "capability_type", None) == CapabilityType.AGENT
            and artifact.agent_config
            and artifact.agent_config.get("subagents")
        ):
            return False

        current_name = getattr(artifact, "capability_name", None) or current_ref.capability_name
        for other in self.subtasks:
            other_ref, _ = self._resolve_step(other)
            if other_ref is None:
                return False
            if other_ref.source != "generated":
                return False
            if other_ref.capability_type != CapabilityType.AGENT:
                return False
            if other_ref.capability_name != current_name:
                return False
        return True

    def _collect_union_output_schema(
        self,
        *,
        artifact: Any,
        current_ref: CapabilityReference,
    ) -> dict[str, str] | None:
        """Union output_schemas across every subtask that resolves to this artifact.

        When a generated AGENT or SKILL artifact covers multiple decomposer
        gaps and there are downstream runtime handoffs, using only the
        currently-executing subtask's schema is too narrow. The aggregated
        output must contain whatever sibling handoff keys downstream steps
        will read.

        Fully-collapsed lead-with-subagents tasks are handled separately
        by :meth:`_is_fully_collapsed_lead_artifact`: those tasks do not
        have real runtime handoffs between the conceptual subtasks, so the
        lead should use the task-level result model instead of this union.

        Returns ``None`` when no subtask resolves to this artifact (e.g.
        match from the registry, not a generated artifact) or when the
        union is empty.
        """
        current_name = getattr(artifact, "capability_name", None) or current_ref.capability_name
        union: dict[str, str] = {}
        for other in self.subtasks:
            other_ref, _ = self._resolve_step(other)
            if other_ref is None:
                continue
            if other_ref.capability_name != current_name:
                continue
            for key, desc in (other.output_schema or {}).items():
                if key not in union:
                    union[key] = desc
        return union or None

    def _execute_agent_step(
        self,
        artifact_or_match: Any,
        ref: CapabilityReference,
        workspace: Path,
        upstream_outputs: dict[int, dict],
        subtask: SubTask | None = None,
    ) -> dict:
        from tests.evaluation.execution import (
            execute_agent_artifact,
            get_last_agent_execution_meta,
        )

        fixture_contents = self._build_chain_fixture_contents(workspace, upstream_outputs)
        artifact = (
            artifact_or_match
            if ref.source == "generated"
            else self._match_to_artifact(artifact_or_match, ref)
        )

        fully_collapsed_lead = (
            self._is_fully_collapsed_lead_artifact(
                artifact=artifact, current_ref=ref,
            )
            if subtask
            else False
        )

        # Thread the decomposer-declared output_schema into the agent executor
        # when downstream runtime steps consume the agent's output. Fully
        # collapsed lead-with-subagents tasks are the exception: there, the
        # lead's output is validated only at the task boundary, not at
        # conceptual subtask boundaries that never exist at runtime.
        output_schema = None
        if subtask and not fully_collapsed_lead:
            output_schema = self._collect_union_output_schema(
                artifact=artifact, current_ref=ref,
            )
        elif fully_collapsed_lead:
            logger.info(
                "Agent step %s spans the full task via cached lead reuse; "
                "using task-level response model instead of subtask schemas",
                ref.capability_name,
            )

        output = execute_agent_artifact(
            artifact,
            self.task,
            fixture_contents,
            langfuse_tags=self.langfuse_tags,
            langfuse_session_id=self.langfuse_session_id,
            output_schema=output_schema,
            allow_network=self.allow_network,
        )

        # Capture backend failure metadata so ``run()`` can tag the
        # ChainResult with a structured failure reason.
        meta = get_last_agent_execution_meta()
        if meta is not None and not meta.succeeded:
            if meta.failure_reason == "timeout":
                self._last_step_failure_reason = "agent_execution_timeout"
            elif meta.failure_reason == "error":
                self._last_step_failure_reason = "agent_execution_error"

        return output

    def _execute_step(
        self,
        step_index: int,
        subtask: SubTask,
        ref: CapabilityReference,
        artifact_or_match: Any,
        workspace: Path,
        upstream_outputs: dict[int, dict],
    ) -> dict:
        """Dispatch execution to the appropriate backend."""
        cap_type = ref.capability_type

        if cap_type == CapabilityType.MCP_TOOL:
            output = self._execute_mcp_tool_step(
                artifact_or_match, ref, step_index, workspace,
                subtask=subtask,
            )
        elif cap_type == CapabilityType.SKILL:
            output = self._execute_skill_step(
                artifact_or_match, ref, workspace, upstream_outputs,
                subtask=subtask,
            )
        elif cap_type == CapabilityType.AGENT:
            output = self._execute_agent_step(
                artifact_or_match, ref, workspace, upstream_outputs,
                subtask=subtask,
            )
        else:
            logger.warning("Unknown capability type: %s", cap_type)
            return {}

        # Tolerate bare list returns (e.g. a join step returning [row, row, ...])
        # by wrapping in a dict. ChainResult.step_results requires list[dict].
        if isinstance(output, list):
            logger.info(
                "Step %d returned a list (%d items); wrapping as {'result': [...]}",
                step_index, len(output),
            )
            output = {"result": output}

        return output

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, task_id: str | None = None) -> ChainResult:
        """Execute the full chain in topological order.

        The final step's output is ``step_results[-1]``.
        """
        execution_order = self.build_execution_order()

        # None means the DAG is malformed (cycle, out-of-range depends_on,
        # self-loop). Fail closed with a descriptive reason.
        if execution_order is None:
            return ChainResult(
                chain_length=max(1, len(self.subtasks)),
                chain_success=False,
                step_results=[],
                failed_step_index=None,
                failed_step_name=None,
                failed_step_reason=(
                    "Invalid compositional DAG — cycle, self-loop, or "
                    "out-of-range depends_on index detected by "
                    "build_execution_order. Chain failed closed."
                ),
            )
        if not execution_order:
            return ChainResult(
                chain_length=1,
                chain_success=False,
                step_results=[],
                failed_step_index=None,
                failed_step_name=None,
            )

        workspace = self.build_workspace(task_id)
        step_results: list[dict] = []
        step_timings: list[StepTiming] = []
        executed_outputs: dict[int, dict] = {}
        # Lead dedup: when multiple subtasks resolve to the same artifact
        # (lead-with-subagents), execute it once and cache the output.
        _lead_cache: dict[str, dict] = {}

        # LangFuse: create a parent trace for the entire chain execution
        lf, chain_trace = self._start_langfuse_chain_trace(
            task_id, len(execution_order),
        )

        # Capture the chain-executor's trace_id and set it in trace_context
        # so SKILL/AGENT runtime paths attach their LLM calls to this trace
        # instead of creating fragmented sibling traces. Prefer the
        # span-scoped trace_id; fall back to the singleton client for older
        # LangFuse SDK versions.
        _chain_trace_id: str | None = None
        if lf is not None:
            try:
                # Prefer the span-scoped trace_id (race-free under concurrency).
                _chain_trace_id = (
                    getattr(chain_trace, "_mint_trace_id", None)
                    if chain_trace is not None
                    else None
                )
                if _chain_trace_id is None:
                    # Compatibility fallback for SDKs without span.trace_id.
                    _chain_trace_id = getattr(lf, "current_trace_id", None)
            except Exception:  # noqa: BLE001
                pass
        if _chain_trace_id:
            from src.orchestrator.tracing import (
                _trace_context_global,
                _trace_context_lock,
                _trace_context_var,
            )
            _chain_tc_prev_var = _trace_context_var.get()
            _chain_tc_merged = dict(_chain_tc_prev_var or {})
            _chain_tc_merged["trace_id"] = _chain_trace_id
            _chain_tc_token = _trace_context_var.set(_chain_tc_merged)
            with _trace_context_lock:
                _chain_tc_prev_global = dict(_trace_context_global)
                _trace_context_global.clear()
                _trace_context_global.update(_chain_tc_merged)
        else:
            _chain_tc_token = None
            _chain_tc_prev_global = None

        try:
            for position, step_idx in enumerate(execution_order):
                # Reset the per-step failure reason so successful steps do not
                # inherit stale failure metadata from an earlier backend call.
                self._last_step_failure_reason = None

                subtask = self.subtasks[step_idx]
                ref, artifact_or_match = self._resolve_step(subtask)

                if ref is None:
                    logger.warning(
                        "Step %d has no resolved capability; failing chain",
                        step_idx,
                    )
                    self._end_langfuse_chain_trace(
                        lf, chain_trace, False, step_results,
                        failed_step=step_idx, failed_name=subtask.description,
                    )
                    return ChainResult(
                        chain_length=len(execution_order),
                        chain_success=False,
                        step_results=step_results,
                        step_timings=step_timings,
                        failed_step_index=step_idx,
                        failed_step_name=subtask.description,
                    )

                # Collect outputs from steps this one depends on
                upstream = {
                    dep_idx: executed_outputs[dep_idx]
                    for dep_idx in subtask.depends_on
                    if dep_idx in executed_outputs
                }

                logger.info(
                    "Chain step %d/%d: %s (%s, %s)",
                    position + 1,
                    len(execution_order),
                    ref.capability_name,
                    ref.capability_type.value,
                    ref.source,
                )

                # LangFuse: start step span
                step_span = self._start_langfuse_step_span(
                    lf, chain_trace, position, len(execution_order),
                    ref, subtask,
                )

                # Lead dedup: if this artifact was already executed (lead-with-
                # subagents covering multiple subtasks), reuse cached output.
                # Track cache hits so invocation recording can skip conceptual
                # subtasks whose lead artifact already ran.
                t_step = time.monotonic()
                cache_hit = ref.capability_name in _lead_cache
                if cache_hit:
                    output = _lead_cache[ref.capability_name]
                    logger.info(
                        "Step %d reusing cached lead output from %s",
                        step_idx, ref.capability_name,
                    )
                else:
                    output = self._execute_step(
                        step_idx, subtask, ref, artifact_or_match, workspace, upstream
                    )
                    # Cache output from lead-with-subagents artifacts
                    if (
                        ref.capability_type == CapabilityType.AGENT
                        and isinstance(artifact_or_match, GeneratedArtifact)
                        and artifact_or_match.agent_config
                        and artifact_or_match.agent_config.get("subagents")
                    ):
                        _lead_cache[ref.capability_name] = output
                step_ms = (time.monotonic() - t_step) * 1000

                # LangFuse: end step span
                self._end_langfuse_step_span(
                    step_span, bool(output), step_ms, output,
                )

                step_timings.append(StepTiming(
                    step_index=step_idx,
                    capability_name=ref.capability_name,
                    capability_type=ref.capability_type.value,
                    source=ref.source,
                    success=bool(output),
                    latency_ms=step_ms,
                ))

                step_results.append(output)
                executed_outputs[step_idx] = output

                fully_collapsed_lead = (
                    ref.capability_type == CapabilityType.AGENT
                    and self._is_fully_collapsed_lead_artifact(
                        artifact=artifact_or_match, current_ref=ref,
                    )
                )

                # Runtime output-schema compliance check. Fails the chain when
                # an AGENT/SKILL step output is missing keys the decomposer
                # declared in output_schema — the same keys downstream steps
                # rely on via requires_upstream_keys. MCP_TOOL output is
                # deterministic, so only enforce for AGENT/SKILL outputs.
                if (
                    subtask.output_schema
                    and output
                    and not fully_collapsed_lead
                    and ref.capability_type in (
                        CapabilityType.AGENT,
                        CapabilityType.SKILL,
                    )
                ):
                    expected = set(subtask.output_schema.keys())
                    actual = set(output.keys())
                    missing = expected - actual
                    if missing:
                        reason = (
                            f"{ref.capability_type.value} output missing "
                            f"declared schema keys {sorted(missing)} "
                            f"(declared: {sorted(expected)}, "
                            f"got: {sorted(actual)})"
                        )
                        logger.warning(
                            "Step %d (%s): %s — failing chain on "
                            "AGENT/SKILL output_schema compliance.",
                            step_idx, ref.capability_name, reason,
                        )
                        self._last_step_failure_reason = reason
                        self._end_langfuse_chain_trace(
                            lf, chain_trace, False, step_results,
                            failed_step=step_idx,
                            failed_name=subtask.description,
                        )
                        return ChainResult(
                            chain_length=len(execution_order),
                            chain_success=False,
                            step_results=step_results,
                            step_timings=step_timings,
                            failed_step_index=step_idx,
                            failed_step_name=subtask.description,
                            failed_step_reason=reason,
                        )

                # Record real invocation in metrics store. Pass the upstream
                # outputs as input_args so the row survives get_recent_failures'
                # WHERE input_args IS NOT NULL filter and feeds
                # failure-informed optimization. Skip cache-hit subtasks
                # because the artifact did not actually run.
                if not cache_hit:
                    self._record_step_invocation(
                        ref, output, step_ms, task_id or "",
                        upstream_outputs=executed_outputs,
                    )

                # Persist for downstream MCP_TOOL steps (Docker reads from host)
                self._save_step_output(workspace, step_idx, output)

                if not output:
                    logger.warning(
                        "Step %d (%s) returned empty output; failing chain",
                        step_idx,
                        ref.capability_name,
                    )
                    self._end_langfuse_chain_trace(
                        lf, chain_trace, False, step_results,
                        failed_step=step_idx, failed_name=subtask.description,
                    )
                    return ChainResult(
                        chain_length=len(execution_order),
                        chain_success=False,
                        step_results=step_results,
                        step_timings=step_timings,
                        failed_step_index=step_idx,
                        failed_step_name=subtask.description,
                        failed_step_reason=self._last_step_failure_reason,
                    )

            # Collect any output files the last step wrote to the workspace
            # (e.g. output.csv, output.xlsx) before cleanup removes them.
            collected_files = self._collect_output_files(workspace)

            # SKILL/AGENT steps can't write to the Docker workspace, so they
            # embed file content in the output dict using special keys.
            #
            # Scan every step's output, not just the terminal step, because
            # intermediate SKILL/AGENT steps can emit file payloads.
            _binary_exts = {".xlsx", ".xls", ".parquet", ".pkl", ".zip", ".gz"}
            for step_output in step_results:
                if not isinstance(step_output, dict):
                    continue
                file_name = step_output.get("_output_file_name")
                file_content = step_output.get("_output_file_content")
                if not (file_name and file_content):
                    continue
                if file_name in collected_files:
                    # MCP_TOOL path (workspace mount) already captured it.
                    continue
                if isinstance(file_content, str):
                    _, ext = os.path.splitext(file_name)
                    if ext.lower() in _binary_exts:
                        try:
                            collected_files[file_name] = base64.b64decode(
                                file_content, validate=True,
                            )
                        except Exception:  # noqa: BLE001
                            collected_files[file_name] = file_content.encode("utf-8")
                    else:
                        collected_files[file_name] = file_content.encode("utf-8")
                elif isinstance(file_content, bytes):
                    collected_files[file_name] = file_content

            self._end_langfuse_chain_trace(lf, chain_trace, True, step_results)
            return ChainResult(
                chain_length=len(execution_order),
                chain_success=True,
                step_results=step_results,
                step_timings=step_timings,
                failed_step_index=None,
                failed_step_name=None,
                output_files=collected_files,
            )
        finally:
            # Reset the trace_context set around chain execution so subsequent
            # code outside run() is not affected.
            if _chain_tc_token is not None:
                try:
                    from src.orchestrator.tracing import (
                        _trace_context_global,
                        _trace_context_lock,
                        _trace_context_var,
                    )
                    _trace_context_var.reset(_chain_tc_token)
                    with _trace_context_lock:
                        _trace_context_global.clear()
                        if _chain_tc_prev_global is not None:
                            _trace_context_global.update(_chain_tc_prev_global)
                except Exception:  # noqa: BLE001
                    pass
            self.cleanup_workspace(workspace)

    # ------------------------------------------------------------------
    # LangFuse tracing helpers
    # ------------------------------------------------------------------

    def _start_langfuse_chain_trace(
        self, task_id: str | None, chain_length: int,
    ) -> tuple[Any, Any]:
        """Create a LangFuse trace for the chain execution.

        Uses ``Langfuse().start_as_current_span()`` context manager pattern.
        Returns ``(langfuse_client, context_manager)`` or ``(None, None)``.
        The caller must use the context manager (``__enter__``/``__exit__``).
        """
        try:
            from langfuse import Langfuse

            lf = Langfuse()
            span_cm = lf.start_as_current_span(name="chain-executor")
            span = span_cm.__enter__()
            # Capture the span trace_id immediately so run() can prefer this
            # span-scoped value over a singleton-client lookup.
            try:
                _span_trace_id = (
                    getattr(span, "trace_id", None)
                    or getattr(lf, "current_trace_id", None)
                )
                if _span_trace_id:
                    setattr(span_cm, "_mint_trace_id", _span_trace_id)
            except Exception:  # noqa: BLE001
                pass
            span.update(
                input={
                    "task_id": task_id,
                    "chain_length": chain_length,
                    "steps": [
                        {
                            "description": st.description[:100],
                            "required_capabilities": st.required_capabilities,
                            "depends_on": st.depends_on,
                        }
                        for st in self.subtasks
                    ],
                },
                metadata={"chain_length": chain_length},
            )
            # Sanitise tags: LangGraph metadata serialization can turn
            # list[str] into dicts like {"arrayValue": {}}. Ensure plain strings.
            clean_tags = [
                str(t) for t in (self.langfuse_tags or [])
                if isinstance(t, str)
            ]
            # Add one capability_type:X tag per distinct type present in the
            # chain so LangFuse can filter traces by executed backend type.
            try:
                _types = {
                    ref.capability_type.value
                    for ref in self.resolution.values()
                    if hasattr(ref, "capability_type")
                }
                for _ct in sorted(_types):
                    clean_tags.append(f"capability_type:{_ct}")
                clean_tags.append(f"chain_length:{chain_length}")
            except Exception:  # noqa: BLE001
                pass
            lf.update_current_trace(
                name="chain-executor",
                user_id=self.langfuse_user_id,
                session_id=self.langfuse_session_id,
                tags=clean_tags,
            )
            return lf, span_cm
        except Exception:  # noqa: BLE001
            return None, None

    def _end_langfuse_chain_trace(
        self,
        lf: Any,
        span_cm: Any,
        success: bool,
        step_results: list[dict],
        *,
        failed_step: int | None = None,
        failed_name: str | None = None,
    ) -> None:
        """Finalize the chain trace with outcome."""
        if span_cm is None:
            return
        try:
            lf.update_current_span(
                output={
                    "success": success,
                    "steps_completed": len(step_results),
                    "failed_step_index": failed_step,
                    "failed_step_name": failed_name,
                },
            )
            span_cm.__exit__(None, None, None)
            lf.flush()
        except Exception:  # noqa: BLE001
            pass

    def _start_langfuse_step_span(
        self,
        lf: Any,
        trace: Any,
        position: int,
        total: int,
        ref: CapabilityReference,
        subtask: SubTask,
    ) -> Any:
        """Create a child span for one chain step."""
        if trace is None:
            return None
        try:
            cap_type = ref.capability_type.value
            # Prefer typed observations when the SDK supports them so LangFuse
            # can distinguish MCP_TOOL / SKILL / AGENT steps visually.
            _type_map = {"mcp_tool": "tool", "agent": "agent", "skill": "span"}
            _obs_type = _type_map.get(cap_type, "span")
            step_cm = None
            try:
                start_typed = getattr(lf, "start_as_current_observation", None)
                if start_typed is not None:
                    step_cm = start_typed(
                        as_type=_obs_type,
                        name=f"step-{position + 1}/{total}: {ref.capability_name}",
                    )
            except Exception:  # noqa: BLE001
                step_cm = None
            if step_cm is None:
                step_cm = lf.start_as_current_span(
                    name=f"step-{position + 1}/{total}: {ref.capability_name}",
                )
            span = step_cm.__enter__()
            span.update(
                input={
                    "capability_name": ref.capability_name,
                    "capability_type": cap_type,
                    "source": ref.source,
                    "subtask_description": subtask.description[:200],
                    "depends_on": subtask.depends_on,
                },
                metadata={
                    "capability_type": cap_type,
                    "source": ref.source,
                    "step_position": position + 1,
                    "step_total": total,
                    "observation_type": _obs_type,
                },
            )
            return step_cm
        except Exception:  # noqa: BLE001
            return None

    def _end_langfuse_step_span(
        self,
        step_cm: Any, success: bool, latency_ms: float, output: dict,
    ) -> None:
        """Finalize a step span with outcome.

        Includes ``failed_step_reason`` on failed step spans when a backend or
        schema check set structured failure metadata.
        """
        if step_cm is None:
            return
        try:
            # Update output on the span before closing the context
            from langfuse import Langfuse
            lf = Langfuse()
            out_payload: dict = {
                "success": success,
                "latency_ms": round(latency_ms, 1),
                "output_keys": list(output.keys()) if output else [],
            }
            if not success and self._last_step_failure_reason:
                out_payload["failed_step_reason"] = self._last_step_failure_reason
            lf.update_current_span(
                output=out_payload,
                level="DEFAULT" if success else "WARNING",
            )
            step_cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
