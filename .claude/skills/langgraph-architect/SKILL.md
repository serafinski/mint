---
name: langgraph-architect
description: "Design and implement LangGraph state machines for multi-agent orchestration, particularly for JIT capability synthesis pipelines. Use this skill when building state graphs with task decomposition stages, skill gap detection, capability generation workflows, verification pipelines, or any multi-step orchestration requiring conditional branching, checkpointing, or parallel execution. Triggers include: mentions of 'LangGraph', 'state graph', 'orchestration flow', 'agent pipeline', 'workflow automation', 'state machine design', or when implementing systems that coordinate multiple agents or tools through sequential/parallel stages. Also use when designing systems with human-in-the-loop approval, error recovery, or state persistence requirements."
---

# LangGraph Architect

Design and implement production-ready LangGraph state machines for complex multi-agent orchestration.

## Quick Reference

| Task | Approach |
|------|----------|
| Design new state graph | Start with state schema → nodes → edges → workflow pattern |
| Add checkpointing | Use SQLite memory saver or Postgres for persistence |
| Conditional routing | Define edge function that returns next node name |
| Parallel execution | Use `send()` API to fan out, reducer to fan in |
| Human-in-the-loop | Use `.interrupt()` before sensitive operations |

---

## When to Use LangGraph vs Alternatives

**Use LangGraph when:**
- Multiple stages with conditional branching (if X then Y else Z)
- Need state persistence (resume after crash/restart)
- Human approval gates required
- Parallel execution with result aggregation
- Complex error recovery (retry, fallback, escalation)

**Don't use LangGraph for:**
- Simple sequential chains (use LangChain LCEL or plain Python)
- Single LLM call with tools (use basic agent loop)
- Purely deterministic workflows (use plain Python functions)

---

## Core Architecture Patterns

### Pattern 1: Linear Pipeline (Task Decomposition → Gap Detection → Generation)

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
from operator import add

class PipelineState(TypedDict):
    task_description: str
    task_graph: dict  # Output of decomposition
    capability_gaps: list[dict]  # Output of gap detection
    generated_artifacts: list[dict]  # Output of generation
    errors: Annotated[list[str], add]  # Accumulate errors across stages

workflow = StateGraph(PipelineState)

# Define nodes (each is a function that receives state and returns updated state)
workflow.add_node("decompose_task", decompose_task_node)
workflow.add_node("detect_gaps", detect_gaps_node)
workflow.add_node("generate_capabilities", generate_capabilities_node)

# Define edges (linear flow)
workflow.set_entry_point("decompose_task")
workflow.add_edge("decompose_task", "detect_gaps")
workflow.add_edge("detect_gaps", "generate_capabilities")
workflow.add_edge("generate_capabilities", END)

graph = workflow.compile()
```

### Pattern 2: Conditional Routing (Route Based on Capability Type)

```python
def route_by_capability_type(state: PipelineState) -> str:
    """Router function that decides next node based on state"""
    gap = state["current_gap"]
    
    if gap["type"] == "mcp_tool":
        return "generate_mcp_tool"
    elif gap["type"] == "agent_skill":
        return "generate_skill"
    elif gap["type"] == "new_agent":
        return "spawn_agent"
    else:
        return "error_handler"

workflow = StateGraph(PipelineState)

workflow.add_node("classify_gap", classify_gap_node)
workflow.add_node("generate_mcp_tool", generate_mcp_tool_node)
workflow.add_node("generate_skill", generate_skill_node)
workflow.add_node("spawn_agent", spawn_agent_node)
workflow.add_node("error_handler", error_handler_node)

workflow.set_entry_point("classify_gap")

# Conditional edges: router function returns next node name
workflow.add_conditional_edges(
    "classify_gap",
    route_by_capability_type,
    {
        "generate_mcp_tool": "generate_mcp_tool",
        "generate_skill": "generate_skill",
        "spawn_agent": "spawn_agent",
        "error_handler": "error_handler"
    }
)

workflow.add_edge("generate_mcp_tool", END)
workflow.add_edge("generate_skill", END)
workflow.add_edge("spawn_agent", END)
workflow.add_edge("error_handler", END)
```

### Pattern 3: Parallel Execution with Fan-Out/Fan-In

Use when multiple capabilities can be verified in parallel:

```python
from langgraph.constants import Send

class VerificationState(TypedDict):
    capabilities: list[dict]  # Multiple capabilities to verify
    verification_results: Annotated[list[dict], add]  # Results accumulate

def fan_out_verification(state: VerificationState):
    """Send each capability to verification in parallel"""
    return [
        Send("verify_capability", {"capability": cap})
        for cap in state["capabilities"]
    ]

workflow = StateGraph(VerificationState)

workflow.add_node("verify_capability", verify_capability_node)
workflow.add_node("aggregate_results", aggregate_results_node)

workflow.set_entry_point("fan_out_verification")
workflow.add_conditional_edges("fan_out_verification", fan_out_verification)
workflow.add_edge("verify_capability", "aggregate_results")
workflow.add_edge("aggregate_results", END)
```

### Pattern 4: Human-in-the-Loop Approval Gates

Use for plan approval before implementation:

```python
from langgraph.checkpoint.sqlite import SqliteSaver

class ApprovalState(TypedDict):
    plan: dict
    approved: bool | None
    feedback: str

def should_wait_for_approval(state: ApprovalState) -> str:
    if state.get("approved") is None:
        return "request_approval"  # Will interrupt here
    elif state["approved"]:
        return "execute_plan"
    else:
        return "revise_plan"

workflow = StateGraph(ApprovalState)

workflow.add_node("generate_plan", generate_plan_node)
workflow.add_node("request_approval", request_approval_node)
workflow.add_node("execute_plan", execute_plan_node)
workflow.add_node("revise_plan", revise_plan_node)

workflow.set_entry_point("generate_plan")
workflow.add_edge("generate_plan", "request_approval")

# Interrupt before execution - human provides approval via state update
workflow.add_conditional_edges(
    "request_approval",
    should_wait_for_approval,
    {
        "execute_plan": "execute_plan",
        "revise_plan": "revise_plan",
        "request_approval": "request_approval"  # Loop until approved
    }
)

workflow.add_edge("revise_plan", "request_approval")
workflow.add_edge("execute_plan", END)

# Compile with checkpointer for state persistence
memory = SqliteSaver.from_conn_string(":memory:")
graph = workflow.compile(checkpointer=memory, interrupt_before=["request_approval"])

# Invoke with config to enable resumption
config = {"configurable": {"thread_id": "plan-123"}}
result = graph.invoke(initial_state, config)

# Later: human approves, resume from checkpoint
graph.invoke({"approved": True, "feedback": "Looks good"}, config)
```

### Pattern 5: Error Recovery with Retry Logic

```python
class RobustState(TypedDict):
    task: dict
    attempt_count: int
    max_retries: int
    result: dict | None
    error: str | None

def should_retry(state: RobustState) -> str:
    if state["result"] is not None:
        return "success"
    elif state["attempt_count"] < state["max_retries"]:
        return "retry"
    else:
        return "failed"

def increment_attempt(state: RobustState) -> RobustState:
    state["attempt_count"] += 1
    return state

workflow = StateGraph(RobustState)

workflow.add_node("execute_task", execute_task_node)
workflow.add_node("increment_attempt", increment_attempt)
workflow.add_node("success_handler", success_handler_node)
workflow.add_node("failure_handler", failure_handler_node)

workflow.set_entry_point("execute_task")

workflow.add_conditional_edges(
    "execute_task",
    should_retry,
    {
        "success": "success_handler",
        "retry": "increment_attempt",
        "failed": "failure_handler"
    }
)

workflow.add_edge("increment_attempt", "execute_task")  # Loop back
workflow.add_edge("success_handler", END)
workflow.add_edge("failure_handler", END)
```

---

## State Schema Design

### Principle: Structured State Over Conversation History

Instead of passing full conversation histories between nodes (token-expensive, lossy), use typed schemas that represent the "world state" at each stage.

**Bad (Lossy):**
```python
class State(TypedDict):
    messages: list[str]  # Natural language, hard to parse
```

**Good (Structured):**
```python
class State(TypedDict):
    task_description: str
    subtasks: list[dict]  # Structured: {id, description, dependencies, required_capabilities}
    capability_gaps: list[dict]  # Structured: {name, type, confidence, reason}
    errors: Annotated[list[str], add]  # Accumulate across nodes
```

### Using Annotated for Reducers

When state fields should accumulate (not replace):

```python
from typing import Annotated
from operator import add

class State(TypedDict):
    # Replace on update
    current_stage: str
    
    # Accumulate on update
    errors: Annotated[list[str], add]
    logs: Annotated[list[dict], add]
```

### Schema Evolution

As your pipeline discovers new capability types, the schema evolves:

```python
# Version 1
class CapabilityGap(TypedDict):
    name: str
    type: Literal["mcp_tool", "agent_skill"]

# Version 2 - adds new capability type
class CapabilityGap(TypedDict):
    name: str
    type: Literal["mcp_tool", "agent_skill", "new_agent"]
    spawn_config: dict | None  # New field for agent spawning
```

Plan for schema versioning from the start if your system will evolve.

---

## Node Implementation Guidelines

### Node Function Signature

```python
def node_function(state: StateType) -> dict:
    """
    Each node receives current state and returns updates.
    
    Returns:
        dict: Partial state update (only changed fields)
    """
    # Extract what you need from state
    task = state["task_description"]
    
    # Do work (call LLM, run tool, etc.)
    result = do_work(task)
    
    # Return ONLY the fields you're updating
    return {
        "result": result,
        "current_stage": "completed"
    }
```

### Common Node Patterns

**LLM-based node:**
```python
from langchain_anthropic import ChatAnthropic

def llm_node(state: State) -> dict:
    llm = ChatAnthropic(model="claude-sonnet-4-20250514")
    
    prompt = f"Analyze this task: {state['task_description']}"
    response = llm.invoke(prompt)
    
    return {"analysis": response.content}
```

**Tool-calling node:**
```python
def tool_node(state: State) -> dict:
    # Call external tool/API
    result = external_api.query(state["query"])
    
    return {"api_result": result}
```

**Validation node:**
```python
def validation_node(state: State) -> dict:
    errors = []
    
    if not state.get("required_field"):
        errors.append("Missing required field")
    
    return {
        "errors": errors,
        "is_valid": len(errors) == 0
    }
```

---

## Checkpointing and Persistence

### Why Checkpoint?

- **Resume after crash**: Don't lose progress on long-running pipelines
- **Human-in-the-loop**: Pause for approval, resume later
- **Debugging**: Inspect state at any point in execution
- **Replay**: Re-run from specific checkpoint

### SQLite Checkpointing (Development)

```python
from langgraph.checkpoint.sqlite import SqliteSaver

memory = SqliteSaver.from_conn_string("checkpoints.db")
graph = workflow.compile(checkpointer=memory)

# Each invocation gets a thread_id for isolation
config = {"configurable": {"thread_id": "pipeline-run-123"}}
result = graph.invoke(initial_state, config)

# Resume from same thread
result2 = graph.invoke(updated_state, config)
```

### Postgres Checkpointing (Production)

```python
from langgraph.checkpoint.postgres import PostgresSaver

checkpointer = PostgresSaver.from_conn_string(
    "postgresql://user:pass@localhost/db"
)
graph = workflow.compile(checkpointer=checkpointer)
```

### Streaming with Checkpoints

```python
for chunk in graph.stream(initial_state, config, stream_mode="updates"):
    print(f"Node {chunk} updated state")
    # State automatically checkpointed after each node
```

---

## Multi-Agent Coordination Patterns

### Pattern: Orchestrator + Worker Agents

Your JIT pipeline might coordinate multiple agents. Here's how to structure it:

```python
class OrchestratorState(TypedDict):
    task: str
    worker_results: Annotated[list[dict], add]
    orchestrator_decision: str | None

def spawn_workers(state: OrchestratorState):
    """Fan out to multiple worker agents"""
    subtasks = decompose(state["task"])
    
    return [
        Send("worker_agent", {"subtask": subtask})
        for subtask in subtasks
    ]

def worker_agent(state: dict) -> dict:
    """Each worker processes its subtask independently"""
    result = process_subtask(state["subtask"])
    return {"worker_results": [result]}

def orchestrator_synthesize(state: OrchestratorState) -> dict:
    """Orchestrator combines worker results"""
    synthesis = synthesize_results(state["worker_results"])
    return {"orchestrator_decision": synthesis}

workflow = StateGraph(OrchestratorState)
workflow.add_node("worker_agent", worker_agent)
workflow.add_node("orchestrator_synthesize", orchestrator_synthesize)

workflow.set_entry_point("spawn_workers")
workflow.add_conditional_edges("spawn_workers", spawn_workers)
workflow.add_edge("worker_agent", "orchestrator_synthesize")
workflow.add_edge("orchestrator_synthesize", END)
```

### Pattern: Agent Team with Shared Task List

Integrate with Claude Code agent teams:

```python
class TeamState(TypedDict):
    shared_tasks: list[dict]
    completed_tasks: Annotated[list[dict], add]
    active_teammates: list[str]

def assign_task_to_teammate(state: TeamState) -> str:
    """Route unclaimed tasks to available teammates"""
    unclaimed = [t for t in state["shared_tasks"] if not t.get("claimed_by")]
    
    if unclaimed and state["active_teammates"]:
        return "assign_task"
    elif all(t.get("completed") for t in state["shared_tasks"]):
        return "finalize"
    else:
        return "wait"

workflow = StateGraph(TeamState)
workflow.add_node("assign_task", assign_task_node)
workflow.add_node("wait", wait_node)
workflow.add_node("finalize", finalize_node)

workflow.set_entry_point("assign_task")
workflow.add_conditional_edges(
    "assign_task",
    assign_task_to_teammate,
    {
        "assign_task": "assign_task",
        "wait": "wait",
        "finalize": "finalize"
    }
)
```

---

## Testing and Debugging

### Visualize Your Graph

```python
from IPython.display import Image, display

# Generate Mermaid diagram
display(Image(graph.get_graph().draw_mermaid_png()))
```

### Inspect State at Each Step

```python
for step in graph.stream(initial_state, config, stream_mode="values"):
    print(f"Current state: {step}")
```

### Unit Test Individual Nodes

```python
def test_decompose_task_node():
    state = {
        "task_description": "Build a data pipeline",
        "subtasks": []
    }
    
    result = decompose_task_node(state)
    
    assert "subtasks" in result
    assert len(result["subtasks"]) > 0
    assert all("id" in task for task in result["subtasks"])
```

### Integration Test Full Graph

```python
def test_full_pipeline():
    initial_state = {
        "task_description": "Generate MCP tool for ChromaDB queries"
    }
    
    result = graph.invoke(initial_state)
    
    assert result["generated_artifacts"]
    assert result["verification_results"][0]["passed"]
```

---

## Common Pitfalls and Solutions

### Pitfall 1: State Bloat

**Problem:** State grows unboundedly as you add fields.

**Solution:** Use reducers only when necessary. For most fields, replacement is fine:

```python
# Good - replaces on update
current_stage: str

# Good - accumulates intentionally
errors: Annotated[list[str], add]

# Bad - accumulates unintentionally
intermediate_results: list  # Should this accumulate or replace?
```

### Pitfall 2: Infinite Loops

**Problem:** Conditional edges create cycles without exit condition.

**Solution:** Always include loop counter and max iterations:

```python
class State(TypedDict):
    iteration: int
    max_iterations: int

def should_continue(state: State) -> str:
    if state["iteration"] >= state["max_iterations"]:
        return "exit"
    elif condition_met(state):
        return "exit"
    else:
        return "continue"
```

### Pitfall 3: Missing Error Handling

**Problem:** Node crashes kill entire graph execution.

**Solution:** Wrap node logic in try/except and accumulate errors:

```python
def robust_node(state: State) -> dict:
    try:
        result = risky_operation(state)
        return {"result": result}
    except Exception as e:
        return {
            "errors": [f"Node failed: {str(e)}"],
            "result": None
        }
```

### Pitfall 4: Token-Heavy Conversation Passing

**Problem:** Passing full LLM conversation histories between nodes.

**Solution:** Extract structured data from conversations:

```python
# Bad
def node(state):
    messages = state["full_conversation"]  # Huge token cost
    
# Good
def node(state):
    extracted_facts = state["structured_analysis"]  # Compact
```

---

## Performance Optimization

### Batch Operations

When processing multiple items, use parallel execution:

```python
def process_batch(state: State):
    return [
        Send("process_item", {"item": item})
        for item in state["items"]
    ]
```

### Async Nodes (for I/O-bound operations)

```python
async def async_node(state: State) -> dict:
    results = await asyncio.gather(
        fetch_from_api_1(state["query"]),
        fetch_from_api_2(state["query"])
    )
    return {"results": results}

# Compile with async support
graph = workflow.compile()
result = await graph.ainvoke(initial_state)
```

### Lazy Loading of Resources

Don't load heavy resources in node definitions:

```python
# Bad - loads on graph compilation
llm = ChatAnthropic(model="claude-sonnet-4-20250514")

def node(state):
    return {"result": llm.invoke(state["prompt"])}

# Good - loads on node execution
def node(state):
    llm = ChatAnthropic(model="claude-sonnet-4-20250514")
    return {"result": llm.invoke(state["prompt"])}
```

---

## Integration with Your Thesis System

### Recommended Graph Structure for JIT Pipeline

```python
class JITPipelineState(TypedDict):
    # Input
    task_description: str
    
    # Pillar 1: JIT Capability Pipeline
    task_graph: dict | None
    capability_gaps: list[dict]
    classified_gaps: list[dict]
    generated_artifacts: list[dict]
    
    # Pillar 2: Verification
    verification_queue: list[dict]
    verification_results: Annotated[list[dict], add]
    
    # Pillar 3: Granularity Optimizer
    registry_snapshot: dict | None
    optimization_proposals: list[dict]
    
    # Coordination
    current_pillar: Literal["pipeline", "verification", "optimization"]
    errors: Annotated[list[str], add]

workflow = StateGraph(JITPipelineState)

# Pillar 1 nodes
workflow.add_node("decompose_task", decompose_task_node)
workflow.add_node("detect_gaps", detect_gaps_node)
workflow.add_node("classify_gaps", classify_gaps_node)
workflow.add_node("generate_capabilities", generate_capabilities_node)

# Pillar 2 nodes
workflow.add_node("static_scan", tier1_static_scan_node)
workflow.add_node("behavioral_test", tier2_behavioral_test_node)
workflow.add_node("adversarial_probe", tier3_adversarial_probe_node)

# Pillar 3 nodes
workflow.add_node("compute_metrics", compute_metrics_node)
workflow.add_node("propose_operations", propose_operations_node)
workflow.add_node("apply_operations", apply_operations_node)

# Flow
workflow.set_entry_point("decompose_task")
workflow.add_edge("decompose_task", "detect_gaps")
workflow.add_edge("detect_gaps", "classify_gaps")
workflow.add_edge("classify_gaps", "generate_capabilities")
workflow.add_edge("generate_capabilities", "static_scan")
workflow.add_edge("static_scan", "behavioral_test")
workflow.add_edge("behavioral_test", "adversarial_probe")
workflow.add_edge("adversarial_probe", "compute_metrics")
workflow.add_edge("compute_metrics", "propose_operations")
workflow.add_edge("propose_operations", "apply_operations")
workflow.add_edge("apply_operations", END)

memory = SqliteSaver.from_conn_string("jit_pipeline.db")
graph = workflow.compile(checkpointer=memory)
```

---

## Quick Start Template

Copy this to bootstrap a new LangGraph project:

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated, Literal
from operator import add
from langgraph.checkpoint.sqlite import SqliteSaver

# 1. Define state schema
class MyState(TypedDict):
    input: str
    result: str | None
    errors: Annotated[list[str], add]

# 2. Define nodes
def process_input(state: MyState) -> dict:
    try:
        result = do_work(state["input"])
        return {"result": result}
    except Exception as e:
        return {"errors": [str(e)]}

# 3. Build graph
workflow = StateGraph(MyState)
workflow.add_node("process", process_input)
workflow.set_entry_point("process")
workflow.add_edge("process", END)

# 4. Compile with checkpointing
memory = SqliteSaver.from_conn_string(":memory:")
graph = workflow.compile(checkpointer=memory)

# 5. Run
result = graph.invoke(
    {"input": "test"},
    config={"configurable": {"thread_id": "1"}}
)
print(result)
```

---

## Further Resources

- **LangGraph docs**: https://langchain-ai.github.io/langgraph/
- **Checkpointing guide**: https://langchain-ai.github.io/langgraph/how-tos/persistence/
- **Multi-agent examples**: https://langchain-ai.github.io/langgraph/tutorials/multi_agent/

---

## Summary

LangGraph excels at orchestrating complex, stateful workflows with:
- Conditional branching based on state
- Parallel execution with fan-out/fan-in
- State persistence for resumption
- Human-in-the-loop approval gates
- Multi-agent coordination

For your thesis system:
- Use structured state schemas (not conversation histories)
- Checkpoint at every stage for debugging
- Design for three pillars as separate node groups
- Plan for schema evolution as you discover new capability types
- Test individual nodes before integrating into full graph
