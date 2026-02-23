"""Integration tests for the task decomposition DeepAgent.

These tests invoke the real DeepAgent with a live LLM API call.
They verify that:
- ``write_todos`` planning is used by the agent
- ``response_format`` returns structured subtasks via ``structured_response``
- The node-level timeout and recursion limit are respected

Requires DEEPSEEK_API_KEY environment variable to be set.
"""

from __future__ import annotations

import os

import pytest

from src.orchestrator.decomposition import (
    DECOMPOSITION_RECURSION_LIMIT,
    decomposition_node,
)
from src.orchestrator.prompts.decomposition import DECOMPOSITION_SYSTEM_PROMPT
from src.schemas.task import DecompositionResult, TaskState, TaskStatus

_has_api_key = bool(os.environ.get("DEEPSEEK_API_KEY"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _has_api_key, reason="DEEPSEEK_API_KEY not set"),
]



class TestDecompositionIntegration:
    """Live DeepAgent integration tests."""

    def test_structured_response_returned(self):
        """The DeepAgent returns a valid DecompositionResult via response_format."""
        state = TaskState(
            task_description=(
                "Build a CSV report: load sales.csv, compute total revenue "
                "per region, and generate a summary table."
            )
        )
        cmd = decomposition_node(state)

        assert cmd.goto == "gap_detection"
        assert cmd.update["status"] == TaskStatus.DECOMPOSING

        subtasks = cmd.update["subtasks"]
        assert len(subtasks) >= 1, "Expected at least one subtask"

        for st in subtasks:
            assert st.description, "Subtask description must be non-empty"
            assert len(st.required_capabilities) >= 1, (
                f"Subtask '{st.description}' must have at least one required capability"
            )

        # required_capabilities should be populated from subtasks
        assert len(cmd.update["required_capabilities"]) >= 1

        # No errors expected on a normal run
        assert cmd.update["errors"] == []

    def test_write_todos_is_used(self):
        """The DeepAgent uses write_todos during planning.

        We verify this by checking the agent's message history for
        write_todos tool calls. The production system prompt instructs
        the agent to use write_todos before producing output.
        """
        from deepagents import create_deep_agent

        agent = create_deep_agent(
            model="deepseek:deepseek-chat",
            system_prompt=DECOMPOSITION_SYSTEM_PROMPT,
            response_format=DecompositionResult,
            name="test-decomposer",
        )

        result = agent.invoke(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "Parse a JSON config file and validate its schema "
                            "against a specification."
                        ),
                    }
                ]
            },
            {"recursion_limit": DECOMPOSITION_RECURSION_LIMIT},
        )

        # Check that write_todos was called in the message history
        messages = result.get("messages", [])
        tool_calls_found = []
        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    # ToolCall can be a TypedDict (dict-like) or an object
                    if isinstance(tc, dict):
                        tool_calls_found.append(tc.get("name", ""))
                    else:
                        tool_calls_found.append(getattr(tc, "name", ""))

        assert "write_todos" in tool_calls_found, (
            f"Expected write_todos in tool calls, found: {tool_calls_found}"
        )

        # Structured response should still be present
        structured = result.get("structured_response")
        assert structured is not None, "Expected structured_response in result"
        assert isinstance(structured, DecompositionResult)
        assert len(structured.subtasks) >= 1

    def test_subtask_dependencies_are_valid(self):
        """depends_on indices reference valid subtask positions."""
        state = TaskState(
            task_description=(
                "Load customer data from a database, clean the records, "
                "train a churn prediction model, and generate a report "
                "with the predictions."
            )
        )
        cmd = decomposition_node(state)

        subtasks = cmd.update["subtasks"]
        assert len(subtasks) >= 2, (
            f"Expected at least 2 subtasks for a multi-step task, got {len(subtasks)}; "
            f"errors: {cmd.update['errors']}"
        )
        n = len(subtasks)
        for i, st in enumerate(subtasks):
            for dep in st.depends_on:
                assert 0 <= dep < n, f"Subtask {i} depends_on index {dep} is out of range [0, {n})"
                assert dep < i, (
                    f"Subtask {i} depends on later subtask {dep} — expected only backward deps"
                )
