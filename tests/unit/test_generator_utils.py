"""Unit tests for src/orchestrator/generators/utils.build_schema_context.

Moved from tests/unit/test_composition.py (file-placement drift: the tests
probe ``build_schema_context`` which lives in ``generators/utils.py``, not in
``composition.py``). See Batch 02b audit for context.

Sibling helper tests for ``build_task_context`` live in
``tests/unit/test_build_task_context.py``.
"""

from __future__ import annotations

from src.orchestrator.generators.utils import build_schema_context
from src.schemas.capability import CapabilityType
from src.schemas.task import ClassifiedGap, SubTask


# ---------------------------------------------------------------------------
# build_schema_context — inter-step schema contracts
# ---------------------------------------------------------------------------


class TestBuildSchemaContext:
    """Tests for build_schema_context — inter-step schema contracts."""

    def test_output_schema_in_context(self):
        """Output schema keys appear in the context string."""
        gap = ClassifiedGap(
            description="aggregate costs",
            query="aggregate-costs",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="",
        )
        subtasks = [
            SubTask(
                description="parse data",
                required_capabilities=["parse-data"],
                depends_on=[],
                output_schema={"records": "list[dict]"},
            ),
            SubTask(
                description="aggregate costs",
                required_capabilities=["aggregate-costs"],
                depends_on=[0],
                output_schema={"total_cost": "float", "count": "int"},
            ),
        ]
        ctx = build_schema_context(gap, subtasks)
        assert '"total_cost"' in ctx
        assert '"count"' in ctx
        assert '"records"' in ctx  # upstream input contract

    def test_no_schema_returns_empty(self):
        """When subtask has no output_schema, returns empty string."""
        gap = ClassifiedGap(
            description="do something",
            query="do-something",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="",
        )
        subtasks = [
            SubTask(
                description="do something",
                required_capabilities=["do-something"],
                depends_on=[],
            ),
        ]
        ctx = build_schema_context(gap, subtasks)
        assert ctx == ""

    def test_no_matching_subtask_returns_empty(self):
        """When gap query doesn't match any subtask, returns empty string."""
        gap = ClassifiedGap(
            description="unrelated",
            query="unrelated-query",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="",
        )
        subtasks = [
            SubTask(
                description="something else",
                required_capabilities=["other-thing"],
                depends_on=[],
                output_schema={"x": "int"},
            ),
        ]
        ctx = build_schema_context(gap, subtasks)
        assert ctx == ""

    def test_first_step_no_input_contract(self):
        """First step (no depends_on) only has output, no input contract."""
        gap = ClassifiedGap(
            description="parse input",
            query="parse-input",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="",
        )
        subtasks = [
            SubTask(
                description="parse input",
                required_capabilities=["parse-input"],
                depends_on=[],
                output_schema={"data": "list[dict]"},
            ),
        ]
        ctx = build_schema_context(gap, subtasks)
        assert '"data"' in ctx
        assert "receives" not in ctx.lower()  # no input contract for first step

    def test_transitive_upstream_keys_are_included_in_input_contract(self):
        """Downstream schema context should include transitive ancestor outputs.

        T11 exposed a generator/runtime drift: execution passes the transitive
        upstream closure, but ``build_schema_context`` previously documented
        only direct parents. A downstream step that consumes a transitive key
        such as ``targets`` must see that key in the generator prompt.
        """
        gap = ClassifiedGap(
            description="compare revenues to targets",
            query="compare-revenues-to-targets",
            classified_type=CapabilityType.MCP_TOOL,
            confidence=0.9,
            rationale="",
        )
        subtasks = [
            SubTask(
                description="parse files",
                required_capabilities=["parse-inputs"],
                depends_on=[],
                output_schema={
                    "sales": "list[dict]",
                    "products": "list[dict]",
                    "targets": "list[dict]",
                },
            ),
            SubTask(
                description="aggregate region revenues",
                required_capabilities=["aggregate-revenues"],
                depends_on=[0],
                output_schema={
                    "region_revenues": "dict",
                    "summary_row_count": "int",
                },
            ),
            SubTask(
                description="compare revenues to targets",
                required_capabilities=["compare-revenues-to-targets"],
                depends_on=[1],
                output_schema={
                    "summary_rows": "list[dict]",
                    "regions_meeting_target": "int",
                },
            ),
        ]

        ctx = build_schema_context(gap, subtasks)

        assert '"region_revenues"' in ctx
        assert '"summary_row_count"' in ctx
        assert '"targets"' in ctx
        assert "from step 0" in ctx
        assert "from step 1" in ctx
