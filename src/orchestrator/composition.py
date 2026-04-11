"""Compositional execution — dependency-ordered chain execution with workspace state passing.

Phase 3 of the pre-eval execution plan. Runs all N artifacts for a task
in topological order, passing intermediate results via a shared workspace
directory (``/tmp/mint_workspace_<task_id>/``).

Workspace layout::

    /tmp/mint_workspace_<id>/
        fixtures/          ← original input files
        step_0_output.json ← output of step 0
        step_1_output.json ← output of step 1
        ...

MCP_TOOL steps mount the workspace into Docker at ``/workspace`` and read/write
step output files there.  SKILL and AGENT steps receive upstream outputs via
``fixture_contents`` injection (no workspace mount needed — solves HP4).
"""

from __future__ import annotations

import graphlib
import json
import logging
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
    classified_gaps: list | None = None,
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
            # Backwards compat: old MatchItems without query field
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

    # Also build a classified_gaps lookup for type information
    gap_type_by_query: dict[str, CapabilityType] = {}
    for cg in classified_gaps or []:
        gap_type_by_query[cg.query] = cg.classified_type

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
    ) -> None:
        self.subtasks = subtasks
        self.resolution = resolution
        self.artifacts = artifacts
        self.matches = matches
        self.fixture_paths = [Path(p) for p in fixture_paths]
        self.task = task
        self.metrics_store = metrics_store
        self.langfuse_tags = langfuse_tags
        self.langfuse_session_id = langfuse_session_id

        # Index artifacts by gap_query for quick lookup
        self._artifact_by_query: dict[str, GeneratedArtifact] = {}
        for art in artifacts:
            if art.gap_query:
                self._artifact_by_query[art.gap_query] = art

        # Index matches by capability name
        self._match_by_name: dict[str, MatchItem] = {}
        for m in matches:
            self._match_by_name[m.capability.name] = m

    # ------------------------------------------------------------------
    # DAG construction
    # ------------------------------------------------------------------

    def build_execution_order(self) -> list[int]:
        """Return subtask indices in topological (dependency) order.

        Falls back to linear index order on ``CycleError``.
        """
        if len(self.subtasks) <= 1:
            return list(range(len(self.subtasks)))

        graph: dict[int, set[int]] = {}
        for i, st in enumerate(self.subtasks):
            graph[i] = set(st.depends_on)

        try:
            sorter = graphlib.TopologicalSorter(graph)
            return list(sorter.static_order())
        except graphlib.CycleError:
            logger.warning(
                "Cycle detected in subtask dependencies; falling back to linear order"
            )
            return list(range(len(self.subtasks)))

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
        """Reconstruct a ``GeneratedArtifact`` proxy from a registry match."""
        meta = getattr(match.capability, "metadata", {}) or {}
        return GeneratedArtifact(
            capability_name=ref.capability_name,
            capability_type=ref.capability_type,
            source_code=meta.get("source_code"),
            skill_content=meta.get("skill_content"),
            agent_config=meta.get("agent_config"),
            description=getattr(match.capability, "description", None),
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
    ) -> None:
        """Record a real invocation for this chain step in the metrics store."""
        if self.metrics_store is None:
            return
        try:
            from src.schemas.metrics import InvocationRecord

            record = InvocationRecord(
                capability_id=ref.capability_name,
                task_id=task_id[:64],
                success=bool(output),
                token_cost=0,
                latency_ms=latency_ms,
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
    ) -> dict:
        from tests.evaluation.execution import execute_mcp_tool_in_chain

        if ref.source == "generated":
            source_code = artifact_or_match.source_code
            pip_packages = artifact_or_match.pip_packages
        else:
            meta = getattr(artifact_or_match.capability, "metadata", {}) or {}
            source_code = meta.get("source_code")
            pip_packages = []

        if not source_code:
            logger.warning(
                "No source code for MCP_TOOL step %d (%s)", step_index, ref.capability_name
            )
            return {}

        return execute_mcp_tool_in_chain(
            source_code=source_code,
            step_index=step_index,
            workspace_path=workspace,
            pip_packages=pip_packages,
            capability_name=ref.capability_name,
        )

    def _execute_skill_step(
        self,
        artifact_or_match: Any,
        ref: CapabilityReference,
        workspace: Path,
        upstream_outputs: dict[int, dict],
    ) -> dict:
        from tests.evaluation.execution import execute_skill_artifact

        fixture_contents = self._build_chain_fixture_contents(workspace, upstream_outputs)
        artifact = (
            artifact_or_match
            if ref.source == "generated"
            else self._match_to_artifact(artifact_or_match, ref)
        )

        return execute_skill_artifact(
            artifact,
            self.task,
            fixture_contents,
            langfuse_tags=self.langfuse_tags,
            langfuse_session_id=self.langfuse_session_id,
        )

    def _execute_agent_step(
        self,
        artifact_or_match: Any,
        ref: CapabilityReference,
        workspace: Path,
        upstream_outputs: dict[int, dict],
    ) -> dict:
        from tests.evaluation.execution import execute_agent_artifact

        fixture_contents = self._build_chain_fixture_contents(workspace, upstream_outputs)
        artifact = (
            artifact_or_match
            if ref.source == "generated"
            else self._match_to_artifact(artifact_or_match, ref)
        )

        return execute_agent_artifact(
            artifact,
            self.task,
            fixture_contents,
            langfuse_tags=self.langfuse_tags,
            langfuse_session_id=self.langfuse_session_id,
        )

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
                artifact_or_match, ref, step_index, workspace
            )
        elif cap_type == CapabilityType.SKILL:
            output = self._execute_skill_step(
                artifact_or_match, ref, workspace, upstream_outputs
            )
        elif cap_type == CapabilityType.AGENT:
            output = self._execute_agent_step(
                artifact_or_match, ref, workspace, upstream_outputs
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

    def run(
        self,
        task_id: str | None = None,
        on_step_start: Any | None = None,
        on_step_end: Any | None = None,
    ) -> ChainResult:
        """Execute the full chain in topological order.

        The final step's output is ``step_results[-1]``.

        Optional callbacks:
            on_step_start(position, total, ref): called before each step
            on_step_end(position, total, ref, success): called after each step
        """
        execution_order = self.build_execution_order()

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
        executed_outputs: dict[int, dict] = {}
        # Lead dedup: when multiple subtasks resolve to the same artifact
        # (lead-with-subagents), execute it once and cache the output.
        _lead_cache: dict[str, dict] = {}

        # LangFuse: create a parent trace for the entire chain execution
        lf, chain_trace = self._start_langfuse_chain_trace(
            task_id, len(execution_order),
        )

        try:
            for position, step_idx in enumerate(execution_order):
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

                if on_step_start:
                    try:
                        on_step_start(position, len(execution_order), ref)
                    except Exception:  # noqa: BLE001
                        pass

                # LangFuse: start step span
                step_span = self._start_langfuse_step_span(
                    lf, chain_trace, position, len(execution_order),
                    ref, subtask,
                )

                # Lead dedup: if this artifact was already executed (lead-with-
                # subagents covering multiple subtasks), reuse cached output.
                t_step = time.monotonic()
                if ref.capability_name in _lead_cache:
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

                step_results.append(output)
                executed_outputs[step_idx] = output

                # Record real invocation in metrics store
                self._record_step_invocation(
                    ref, output, step_ms, task_id or "",
                )

                if on_step_end:
                    try:
                        on_step_end(position, len(execution_order), ref, bool(output))
                    except Exception:  # noqa: BLE001
                        pass

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
                        failed_step_index=step_idx,
                        failed_step_name=subtask.description,
                    )

            self._end_langfuse_chain_trace(lf, chain_trace, True, step_results)
            return ChainResult(
                chain_length=len(execution_order),
                chain_success=True,
                step_results=step_results,
                failed_step_index=None,
                failed_step_name=None,
            )
        finally:
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
            lf.update_current_trace(
                name="chain-executor",
                user_id="mint-cli",
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
                },
            )
            return step_cm
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _end_langfuse_step_span(
        step_cm: Any, success: bool, latency_ms: float, output: dict,
    ) -> None:
        """Finalize a step span with outcome."""
        if step_cm is None:
            return
        try:
            # Update output on the span before closing the context
            from langfuse import Langfuse
            lf = Langfuse()
            lf.update_current_span(
                output={
                    "success": success,
                    "latency_ms": round(latency_ms, 1),
                    "output_keys": list(output.keys()) if output else [],
                },
                level="DEFAULT" if success else "WARNING",
            )
            step_cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
