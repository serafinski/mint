"""Iteration 2 Phase C: Full JIT Capability Pipeline.

Graph topology (without optimizer)::

    START
      → decomposition_node      # DeepAgent: task → subtasks + required_capabilities
      → gap_detection           # Registry search: required_capabilities → matches + gaps
      → classifier_node         # LLM: gaps → classified_gaps
      → generate_mcp_tool       # LLM: MCP_TOOL gaps → source code artifacts
      → generate_skill          # LLM: SKILL gaps → SKILL.md artifacts
      → generate_agent          # LLM: AGENT gaps → agent config artifacts
      → verification_node       # Gate: verify artifacts; retry up to 3× on failure
      → loading_node            # Pure Python: load + smoke-test + register MCP tools
      → END

With ``run_optimizer=True`` the graph gains one additional node::

      → loading_node → optimizer_node → END

All nodes use the Command API for routing.  The registry and ToolLoader are
injected via closures for the two nodes that need them.

LangFuse tracing is handled at invocation time — pass a CallbackHandler in
the ``config`` dict when calling ``pipeline.invoke()``:

    from langfuse.langchain import CallbackHandler

    pipeline = create_pipeline(registry, loader).compile()
    result = pipeline.invoke(
        TaskState(task_description="..."),
        config={"callbacks": [CallbackHandler()]},
    )
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.orchestrator.classifier import classifier_node
from src.orchestrator.decomposition import decomposition_node
from src.orchestrator.gap_detection import gap_detection_node
from src.orchestrator.generators.agent import generate_agent
from src.orchestrator.generators.mcp_tool import generate_mcp_tool
from src.orchestrator.generators.skill import generate_skill
from src.orchestrator.loading_node import loading_node
from src.orchestrator.tool_loader import ToolLoader
from src.orchestrator.verification_node import verification_node
from src.registry.metrics import MetricsStore
from src.registry.store import SkillRegistry
from src.schemas.task import TaskState
from src.verification.gate import verify as gate_verify

logger = logging.getLogger(__name__)


def create_pipeline(
    registry: SkillRegistry,
    loader: ToolLoader,
    *,
    run_optimizer: bool = False,
    metrics_store: MetricsStore | None = None,
    verification_enabled: bool = True,
) -> StateGraph:
    """Build the JIT capability pipeline (Iterations 2–4).

    Returns an *uncompiled* ``StateGraph`` — call ``.compile()`` before invoking.

    Args:
        registry: Capability registry used for gap detection and MCP tool registration.
        loader: Tool loader used to dynamically load and register generated MCP tools.
        run_optimizer: When ``True``, append the granularity optimizer loop after the
            loading node.  Requires *metrics_store* to be provided.
        metrics_store: DuckDB metrics store required when *run_optimizer* is ``True``.
            Ignored when *run_optimizer* is ``False``.
        verification_enabled: When ``False``, the verification gate is bypassed
            (``gate_fn=None`` → stub always passes).  Used for the no_verification
            evaluation baseline.

    Returns:
        An uncompiled ``StateGraph[TaskState]`` ready to be compiled and invoked.
    """
    builder = StateGraph(TaskState)

    # --- Nodes without injected dependencies ---
    builder.add_node("decomposition_node", decomposition_node)
    builder.add_node("classifier_node", classifier_node)
    builder.add_node("generate_mcp_tool", generate_mcp_tool)
    builder.add_node("generate_skill", generate_skill)
    builder.add_node("generate_agent", generate_agent)
    # verification_node wired with the real three-tier gate (Iteration 3).
    # When verification_enabled=False (no_verification baseline), gate_fn=None
    # causes the stub gate to pass all artifacts unconditionally.
    _gate_fn = gate_verify if verification_enabled else None
    builder.add_node(
        "verification_node",
        lambda state: verification_node(state, gate_fn=_gate_fn),
    )

    # --- Nodes with injected dependencies (closures) ---
    builder.add_node(
        "gap_detection",
        lambda state: gap_detection_node(state, registry, metrics_store=metrics_store),
    )

    if run_optimizer and metrics_store is None:
        logger.warning("run_optimizer=True but metrics_store is None — optimizer disabled")

    if run_optimizer and metrics_store is not None:
        # loading_node routes to optimizer_node instead of END.
        # Preserve any non-END routing from loading_node (e.g., future retry paths).
        def _loading_then_optimize(state: TaskState) -> Command:
            cmd = loading_node(state, loader, registry)
            next_node = "optimizer_node" if cmd.goto == END else cmd.goto
            return Command(update=cmd.update, goto=next_node)

        builder.add_node("loading_node", _loading_then_optimize)

        # optimizer_node: invoke the compiled optimization loop as a sub-graph.
        # Previous utility scores are carried across pipeline invocations so that
        # KL divergence is meaningful after the first run.
        from src.optimizer.loop import OptimizerState, create_optimization_loop

        _opt_loop = create_optimization_loop(registry, metrics_store)
        _prev_optimizer_scores: dict[str, float] = {}

        def _optimizer_node(state: TaskState) -> Command:
            nonlocal _prev_optimizer_scores
            try:
                init_state = OptimizerState(
                    previous_utility_scores=_prev_optimizer_scores
                )
                result = _opt_loop.invoke(init_state)
                # Carry current utility scores forward for the next pipeline run.
                if isinstance(result, dict):
                    _prev_optimizer_scores = result.get("utility_scores", {})
            except Exception as exc:  # noqa: BLE001
                logger.warning("Granularity optimizer failed (non-fatal): %s", exc)
            return Command(update={}, goto=END)

        builder.add_node("optimizer_node", _optimizer_node)
    else:
        builder.add_node(
            "loading_node",
            lambda state: loading_node(state, loader, registry),
        )

    # Entry point — all subsequent routing is via Command returns from each node:
    #   decomposition_node → "gap_detection"
    #   gap_detection      → "classifier_node"
    #   classifier_node    → "generate_mcp_tool"
    #   generate_mcp_tool  → "generate_skill"
    #   generate_skill     → "generate_agent"
    #   generate_agent     → "verification_node"
    #   verification_node  → "loading_node"  (all pass / max attempts) or "generate_mcp_tool" (retry)
    #   loading_node       → END  (or "optimizer_node" when run_optimizer=True)
    builder.add_edge(START, "decomposition_node")

    return builder
