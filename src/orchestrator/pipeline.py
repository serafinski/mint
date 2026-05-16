"""JIT capability pipeline graph.

Graph topology (without optimizer)::

    START
      → decomposition_node      # DeepAgent: task → subtasks + required_capabilities
      → gap_detection           # Registry search: required_capabilities → matches + gaps
      → classifier_node         # LLM: gaps → classified_gaps
      → generate_mcp_tool       # LLM: MCP_TOOL gaps → source code artifacts
      → generate_skill          # LLM: SKILL gaps → SKILL.md artifacts
      → generate_agent          # LLM: AGENT gaps → agent config artifacts
      → verification_node       # Gate: verify artifacts; retry up to 3× on failure
      → loading_node            # Register generated artifacts for reuse
      → END

With ``run_optimizer=True`` the graph gains one additional node::

      → loading_node → optimizer_node → END

All nodes use the Command API for routing.  The registry and ToolLoader are
injected via closures for the two nodes that need them.

LangFuse tracing is handled at invocation time; pass a CallbackHandler in
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
from src.schemas.optimizer_config import OptimizerConfig
from src.schemas.task import TaskState
from src.verification.gate import verify as gate_verify

logger = logging.getLogger(__name__)


def create_pipeline(
    registry: SkillRegistry,
    loader: ToolLoader,
    *,
    run_optimizer: bool = False,
    optimizer_config: OptimizerConfig | None = None,
    metrics_store: MetricsStore | None = None,
    verification_enabled: bool = True,
) -> StateGraph:
    """Build the JIT capability pipeline graph.

    Returns an *uncompiled* ``StateGraph`` — call ``.compile()`` before invoking.

    Args:
        registry: Capability registry used for gap detection and MCP tool registration.
        loader: Tool loader used to register generated MCP tool descriptors.
        run_optimizer: Compatibility boolean. When ``True``, append the
            granularity optimizer loop with all three operations enabled.
            Ignored if ``optimizer_config`` is provided explicitly.
        optimizer_config: Per-operation optimizer config. When provided,
            supersedes ``run_optimizer``. Any operation resolved to "off" is
            skipped at apply time; if all three are off for the current
            ``invocation_count``, the optimizer subgraph is not wired. When
            ``None`` and ``run_optimizer=True``, falls back to all-on; when
            ``None`` and ``run_optimizer=False``, falls back to all-off.
        metrics_store: DuckDB metrics store required when the optimizer is
            enabled.  Ignored when the optimizer is disabled.
        verification_enabled: When ``False``, the verification gate is bypassed
            (``gate_fn=None`` means generated artifacts pass). Used for the
            no_verification evaluation baseline.

    Returns:
        An uncompiled ``StateGraph[TaskState]`` ready to be compiled and invoked.
    """
    # Normalize to an OptimizerConfig. Callers pass either the compatibility
    # bool or a per-operation config. The config takes precedence.
    if optimizer_config is None:
        optimizer_config = OptimizerConfig.from_run_optimizer_bool(run_optimizer)

    # The pipeline only wires the optimizer subgraph when at least one
    # operation would fire. We don't know the invocation_count at graph-
    # build time, so we use a conservative "any non-off" check: if any
    # operation is "auto" or "on", wire it. The loop itself re-checks
    # per-operation at apply_operations_node time when it has the real
    # count from metrics_store.
    _any_op_enabled = any(
        getattr(optimizer_config, op) != "off"
        for op in ("prune", "split", "merge")
    )
    run_optimizer = _any_op_enabled
    builder = StateGraph(TaskState)

    # --- Nodes without injected dependencies ---
    builder.add_node("decomposition_node", decomposition_node)
    builder.add_node("classifier_node", classifier_node)
    builder.add_node("generate_mcp_tool", generate_mcp_tool)
    builder.add_node("generate_skill", generate_skill)
    builder.add_node("generate_agent", generate_agent)
    # Wire verification with the real gate by default. When
    # verification_enabled=False, gate_fn=None makes generated artifacts pass
    # without gate execution for the no_verification baseline.
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
        # Preserve any non-END routing from loading_node.
        def _loading_then_optimize(state: TaskState) -> Command:
            cmd = loading_node(state, loader, registry)
            next_node = "optimizer_node" if cmd.goto == END else cmd.goto
            return Command(update=cmd.update, goto=next_node)

        builder.add_node("loading_node", _loading_then_optimize)

        # optimizer_node: invoke the compiled optimization loop as a sub-graph.
        # Previous utility scores are carried across pipeline invocations so that
        # KL divergence is meaningful after the first run.
        from src.optimizer.loop import OptimizerState, create_optimization_loop

        _opt_loop = create_optimization_loop(
            registry, metrics_store, optimizer_config=optimizer_config,
        )

        def _optimizer_node(state: TaskState) -> Command:
            try:
                # Load previous utility scores from MetricsStore so optimizer
                # KL divergence compares against the previous task's registry
                # state rather than an empty in-memory baseline.
                try:
                    _prev_scores = metrics_store.load_previous_utility_scores()
                except Exception:  # noqa: BLE001
                    _prev_scores = {}
                # Carry the task's network policy into the optimizer subgraph
                # so merge/split variant verification uses the same policy as
                # the triggering task.
                init_state = OptimizerState(
                    previous_utility_scores=_prev_scores,
                    allow_network=bool(getattr(state, "allow_network", False)),
                )
                # Wrap the optimizer subgraph in trace_context(trace_id=...)
                # so optimizer and per-variant verification events attach to
                # the parent pipeline trace. Prefer ``state.trace_id``; fall
                # back to the callback handler's last trace id if LangGraph
                # checkpointing did not persist the state mutation.
                from src.orchestrator.tracing import trace_context as _trace_context

                _opt_trace_id = getattr(state, "trace_id", None)
                if not _opt_trace_id:
                    try:
                        from langgraph.config import get_config as _get_lg_config
                        _cfg = _get_lg_config()
                        _raw_cbs = (_cfg or {}).get("callbacks")
                        if _raw_cbs is None:
                            _cb_iter: list = []
                        elif isinstance(_raw_cbs, list):
                            _cb_iter = _raw_cbs
                        elif hasattr(_raw_cbs, "handlers"):
                            _cb_iter = list(_raw_cbs.handlers)
                        elif hasattr(_raw_cbs, "inheritable_handlers"):
                            _cb_iter = list(_raw_cbs.inheritable_handlers)
                        else:
                            _cb_iter = []
                        for _cb in _cb_iter:
                            _tid = getattr(_cb, "last_trace_id", None)
                            if _tid:
                                _opt_trace_id = _tid
                                break
                    except Exception:  # noqa: BLE001
                        pass

                with _trace_context(trace_id=_opt_trace_id):
                    result = _opt_loop.invoke(init_state)
                # Persist current utility scores so the next task's optimizer
                # can read them as the previous registry state.
                if isinstance(result, dict):
                    try:
                        metrics_store.persist_utility_scores(
                            result.get("utility_scores", {}) or {}
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "persist_utility_scores failed: %s", exc,
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Granularity optimizer failed (non-fatal): %s", exc)
            return Command(update={}, goto=END)

        builder.add_node("optimizer_node", _optimizer_node)
    else:
        builder.add_node(
            "loading_node",
            lambda state: loading_node(state, loader, registry),
        )

    # Entry point — all subsequent routing is via Command returns from each node.
    #
    # The three type-specific generators are intentionally wired as a sequential
    # chain rather than parallel graph branches. Each generator filters
    # state.classified_gaps by its own CapabilityType; when no gaps of that type
    # are present, the node is a cheap no-op with no LLM call. This keeps retry
    # routing simple (verification_node can always return to generate_mcp_tool)
    # while preserving intra-generator parallelism for multiple gaps of the same
    # type. The trade-off is latency, not behavior: tasks that truly require
    # generation across several capability types cannot overlap those generator
    # stages.
    #   decomposition_node → "gap_detection"
    #   gap_detection      → "classifier_node"
    #   classifier_node    → "generate_mcp_tool"
    #   generate_mcp_tool  → "generate_skill"
    #   generate_skill     → "generate_agent"
    #   generate_agent     → "verification_node"
    #   verification_node  → "loading_node" or "generate_mcp_tool" (retry)
    #   loading_node       → END  (or "optimizer_node" when run_optimizer=True)
    builder.add_edge(START, "decomposition_node")

    return builder
