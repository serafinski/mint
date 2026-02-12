"""Minimal LangGraph pipeline for Iteration 0: intake → gap_detection → report."""

from __future__ import annotations

from typing import Literal

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.registry.store import SkillRegistry
from src.schemas.task import GapItem, MatchItem, TaskState


def intake_node(state: TaskState) -> Command[Literal["gap_detection"]]:
    """Parse the task description into capability queries.

    For Iteration 0 this is a simple pass-through that treats the whole
    task description as a single capability query.  Later iterations will
    use a DeepAgent for proper task decomposition.
    """
    return Command(
        update={"required_capabilities": [state.task_description]},
        goto="gap_detection",
    )


def gap_detection_node(
    state: TaskState, registry: SkillRegistry
) -> Command[Literal["report"]]:
    """Search the registry for each required capability and classify as match or gap."""
    matches_raw, gaps_raw = registry.detect_gaps(state.required_capabilities)

    matches = [
        MatchItem(
            capability=cap,
            score=score,
        )
        for cap, score, _query in matches_raw
    ]
    gaps = [
        GapItem(description=query, query=query)
        for query, _reason in gaps_raw
    ]

    return Command(
        update={"matches": matches, "gaps": gaps},
        goto="report",
    )


def report_node(state: TaskState) -> Command[Literal["__end__"]]:
    """Terminal node that returns the final state."""
    return Command(goto=END)


def create_pipeline(registry: SkillRegistry) -> StateGraph:
    """Build the Iteration 0 pipeline graph.

    Returns an uncompiled StateGraph — call .compile() before invoking.
    """
    builder = StateGraph(TaskState)

    builder.add_node("intake", intake_node)
    builder.add_node(
        "gap_detection",
        lambda state: gap_detection_node(state, registry),
    )
    builder.add_node("report", report_node)

    builder.add_edge(START, "intake")
    # Routing is handled by Command returns — no conditional edges needed.
    # But we still need to declare edges for graph validation when using
    # lambdas that wrap the actual Command-returning functions.

    return builder
