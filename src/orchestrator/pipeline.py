"""Iteration 2 Phase C: Full JIT Capability Pipeline.

Graph topology::

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

from langgraph.graph import START, StateGraph

from src.orchestrator.classifier import classifier_node
from src.orchestrator.decomposition import decomposition_node
from src.orchestrator.gap_detection import gap_detection_node
from src.orchestrator.generators.agent import generate_agent
from src.orchestrator.generators.mcp_tool import generate_mcp_tool
from src.orchestrator.generators.skill import generate_skill
from src.orchestrator.loading_node import loading_node
from src.orchestrator.tool_loader import ToolLoader
from src.orchestrator.verification_node import verification_node
from src.registry.store import SkillRegistry
from src.schemas.task import TaskState


def create_pipeline(registry: SkillRegistry, loader: ToolLoader) -> StateGraph:
    """Build the Iteration 2 JIT capability pipeline.

    Returns an *uncompiled* ``StateGraph`` — call ``.compile()`` before invoking.

    Args:
        registry: Capability registry used for gap detection and MCP tool registration.
        loader: Tool loader used to dynamically load and register generated MCP tools.

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
    # verification_node uses the stub gate (always passes) until Iteration 3
    # replaces it with the real gate: lambda state: verification_node(state, gate_fn=real_gate)
    builder.add_node("verification_node", verification_node)

    # --- Nodes with injected dependencies (closures) ---
    builder.add_node(
        "gap_detection",
        lambda state: gap_detection_node(state, registry),
    )
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
    #   generate_agent     → "loading_node"
    #   loading_node       → END
    builder.add_edge(START, "decomposition_node")

    return builder
