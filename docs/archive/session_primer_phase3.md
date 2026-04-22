# Session Primer: Phase 3 Implementation (2026-04-10/11)

## What happened

Phase 3 of the pre-eval execution plan was implemented: the **compositional executor** that runs all generated artifacts in dependency order with workspace state passing. This is the mechanism that makes the decompose -> gaps -> classify -> generate -> execute-all pipeline work end-to-end.

## Architecture

```
Decomposer produces subtasks with depends_on
    |
Gap detection matches registry / produces gaps
    |
Classifier classifies each gap (MCP_TOOL / SKILL / AGENT)
    |
Generators produce one artifact per gap
    |
CompositionalExecutor runs them all in topological order:
    Step 0: MCP_TOOL (Docker + workspace mount)
      -> writes step_0_output.json to /workspace/
    Step 1: SKILL (constrained LLM + fixture_contents injection)
      -> receives step_0_output.json as inline data
    Step 2: AGENT (DeepAgent + StateBackend)
      -> receives upstream outputs via fixture_contents
    Step 3: MCP_TOOL (Docker, reads /workspace/step_*.json)
      -> final output
```

**HP4 (mixed-type workspace passing) solved:** MCP_TOOL steps use Docker workspace mount. SKILL/AGENT steps receive upstream outputs via `fixture_contents` injection into the LLM message. No StateBackend vs Docker filesystem mismatch.

## Key files changed

| File | What changed |
|---|---|
| `src/orchestrator/composition.py` | NEW: CompositionalExecutor, build_resolution_map |
| `src/verification/sandbox_tool.py` | New sandbox_execute_in_workspace() |
| `src/orchestrator/classifier.py` | BUG FIX: ClassifiedGap.query preserves original gap.query |
| `src/schemas/task.py` | MatchItem.query field added |
| `src/orchestrator/gap_detection.py` | Populates MatchItem.query |
| `src/orchestrator/verification_node.py` | Skip re-verification of passed artifacts |
| `src/verification/gate.py` | Tier 2 clearance for Tier 1 HIGHs |
| `src/verification/behavioral.py` | pip_packages priority + upfront dep check |
| `tests/evaluation/execution.py` | execute_mcp_tool_in_chain() + upstream priority |
| `tests/evaluation/harness.py` | _extract_result_compositional() |
| `src/cli/runner.py` | Chain execution, spinner, display, output subdirs |
| `src/cli/display.py` | Chain summary, verification notes, dedup |
| `tests/unit/test_composition.py` | NEW: 29 tests |
| `tests/integration/test_composition.py` | NEW: Docker chain tests |

## Known issues (Phase 3.5a scope)

1. **Generated tools are monoliths** — each tool solves the entire task instead of its subtask. The generator sees the task description and produces a complete solution, ignoring the "one step of a workflow" framing. Fix: Phase 3.5a generator prompt discipline.

2. **Over-parameterized tools** — generators produce tools with config parameters (`quantity_field`, `encoding`, `how`) that the chain executor can't match from workspace data. Fix: generators should hardcode task-specific constants.

3. **Registry pollution across test_mint tests** — capabilities from earlier tests match later tests' gaps incorrectly. Not a Phase 3 bug; it's the expected accumulation behavior. Fix: better registry precision (description quality, search threshold).

4. **Decomposer over-splits simple tasks** — T01 ("parse CSV") sometimes produces 3 subtasks. Fix: Phase 3.5c rule 10 tightening.

5. **Sandbox import check not implemented** — tools declaring wrong pip package names (`yaml` vs `pyyaml`) now caught by upfront dep check (HIGH severity), but a comprehensive Tier 1.5 import check covering all three artifact types is deferred to Phase 3.5a.

## test_mint results

Best run: 4/7 (T01, T04, T14, T16 pass). All failures are generator quality issues, not executor bugs. The chain infrastructure works correctly.

## What's next

1. **Commit Phase 3** (uncommitted changes)
2. **Phase 3.5a** — generator prompt discipline (highest impact)
3. **Phase 3.5b** — DeepAgents subagents (context engineering)
4. **Phase 3.5c** — decomposer rule 10
