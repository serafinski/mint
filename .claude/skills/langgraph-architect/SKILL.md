---
name: langgraph-architect
description: "Design and implement LangGraph state machines for multi-agent orchestration. Use this skill whenever building workflows or agents with LangGraph — including state graph design, multi-step orchestration, conditional routing, parallel execution, human-in-the-loop patterns, checkpointing, streaming, or multi-agent coordination. Triggers include: mentions of 'LangGraph', 'state graph', 'orchestration', 'agent pipeline', 'workflow automation', 'state machine', 'Command API', 'interrupt', 'entrypoint', 'checkpointer', or when implementing systems that coordinate multiple agents or tools through sequential/parallel stages. Also use when the user needs help choosing between Graph API vs Functional API, designing state schemas with reducers, adding persistence or streaming, or wiring up human approval gates. Even if the user doesn't say 'LangGraph' explicitly, trigger this skill when they describe multi-step agent workflows with branching, persistence, or coordination needs."
---

# LangGraph Architect

Design and implement production-ready LangGraph workflows and agents. Covers both the Graph API (explicit nodes/edges) and the Functional API (`@entrypoint`/`@task` decorators), with modern patterns including the Command API, `interrupt()` for human-in-the-loop, durable execution, streaming, and cross-thread memory.

For deeper coverage of specific topics, see the reference files in `references/`.

## Quick Reference

| Task | Approach |
|------|----------|
| Design new workflow | Choose API → define state → implement nodes → wire edges → compile |
| Choose Graph vs Functional | Graph for complex branching/fan-out; Functional for linear/loop-heavy flows |
| Route from a node | Return `Command(update={...}, goto="next_node")` |
| Parallel fan-out/fan-in | `Send()` API (Graph) or spawn multiple `@task` futures (Functional) |
| Human-in-the-loop | Call `interrupt()` inside a node; resume with `Command(resume=value)` |
| Add persistence | Compile with `checkpointer=` (MemorySaver for dev, PostgresSaver for prod) |
| Stream output | `graph.stream(inputs, stream_mode="updates")` or `"messages"` / `"custom"` |
| Cross-thread memory | Use `InMemoryStore` (dev) or `PostgresStore` (prod) via `runtime.store` |
| Error recovery | `RetryPolicy` on `add_node` for transient; `Command` loop-back for LLM-recoverable |

---

## Choosing an API

LangGraph offers two co-equal APIs. Pick based on your workflow's shape:

**Graph API** — Define explicit nodes and edges. Best when you need complex branching, fan-out/fan-in with `Send()`, or visual graph inspection. Gives you fine-grained control over execution topology.

```python
from langgraph.graph import StateGraph, START, END

builder = StateGraph(MyState)
builder.add_node("step_a", step_a_fn)
builder.add_node("step_b", step_b_fn)
builder.add_edge(START, "step_a")
builder.add_edge("step_a", "step_b")
builder.add_edge("step_b", END)
graph = builder.compile()
```

**Functional API** — Use `@entrypoint` and `@task` decorators with normal Python control flow (if/else, while, for). Best for linear pipelines, simple loops, or when the Graph API feels like overhead.

```python
from langgraph.func import entrypoint, task

@task
def analyze(data: str):
    return llm.invoke(f"Analyze: {data}").content

@task
def summarize(analysis: str):
    return llm.invoke(f"Summarize: {analysis}").content

@entrypoint()
def pipeline(data: str):
    result = analyze(data).result()
    return summarize(result).result()
```

Both APIs support checkpointing, streaming, `interrupt()`, and `@task` for durable execution. The Graph API additionally supports `Send()` for dynamic fan-out and visual graph rendering. You can use `@task` inside Graph API nodes too — they're not mutually exclusive.

---

## State Schema Design

State is the shared data structure all nodes read from and write to. Design it carefully — it's the backbone of your workflow.

**Core principles:**
- Store raw data, format prompts on-demand inside nodes. This lets different nodes format the same data differently without coupling state to presentation.
- Use `TypedDict` for performance. Pydantic `BaseModel` works too but is slower.
- Use reducers (`Annotated[..., reducer]`) only for fields that should accumulate. Most fields should replace on update.

### Basic state with reducers

```python
from typing import TypedDict, Annotated
from operator import add

class WorkflowState(TypedDict):
    input_data: str                          # Replaces on update
    classification: dict | None              # Replaces on update
    results: Annotated[list[dict], add]      # Accumulates across nodes
    errors: Annotated[list[str], add]        # Accumulates across nodes
```

### MessagesState for chat-based agents

For LLM message histories, use the built-in `MessagesState` instead of rolling your own. Its `add_messages` reducer handles message ID tracking, updates (not just appends), and deserialization:

```python
from langgraph.graph import MessagesState

class AgentState(MessagesState):
    # Inherits: messages: Annotated[list[AnyMessage], add_messages]
    tool_results: list[dict]
    turn_count: int
```

`add_messages` also works as a standalone function: `messages = add_messages(messages, [response, *tool_results])`. For managing message history size (trimming, removal, summarization), see `references/memory-and-store.md`.

### Input/Output schemas and private state

Control what's visible at the graph boundary vs what nodes share internally:

```python
class InputState(TypedDict):
    user_query: str

class OutputState(TypedDict):
    answer: str

class OverallState(InputState, OutputState):
    # Private fields — nodes can read/write but not exposed as input/output
    search_results: list[str]
    reasoning_chain: list[dict]

builder = StateGraph(OverallState, input=InputState, output=OutputState)
```

---

## The Command API

`Command` is the modern way to combine state updates and routing in a single return. It replaces the need for separate conditional edge functions in most cases:

```python
from langgraph.types import Command
from typing import Literal

def classify_and_route(state: WorkflowState) -> Command[Literal["handle_bug", "handle_question", "escalate"]]:
    classification = llm.with_structured_output(Classification).invoke(state["input_data"])

    if classification.urgency == "critical":
        goto = "escalate"
    elif classification.intent == "bug":
        goto = "handle_bug"
    else:
        goto = "handle_question"

    return Command(update={"classification": classification.model_dump()}, goto=goto)
```

The `Literal` type annotation on the return type tells LangGraph which nodes this node can route to — this is required for graph rendering and validation.

**When to still use `add_conditional_edges`:** When routing logic is pure (no state updates needed) and you want it separate from the node function, or for backwards compatibility. But `Command` is now the recommended default.

**Command in subgraphs** — use `Command(goto=Command.PARENT)` to hand control back from a subgraph to the parent graph. This is essential for multi-agent handoffs.

---

## Human-in-the-Loop with `interrupt()`

The `interrupt()` function is the primary mechanism for pausing execution and waiting for human input. Call it inside any node:

```python
from langgraph.types import interrupt, Command

def review_node(state: WorkflowState) -> Command[Literal["execute", END]]:
    # interrupt() MUST come before any side effects — the node reruns from
    # the beginning on resume, so anything before interrupt() runs twice
    decision = interrupt({
        "draft": state["draft_response"],
        "message": "Please review and approve"
    })

    if decision["approved"]:
        return Command(
            update={"draft_response": decision.get("edited", state["draft_response"])},
            goto="execute"
        )
    return Command(goto=END)
```

Resume with `Command(resume=...)`:

```python
# Graph pauses at interrupt(). Later, resume:
result = graph.invoke(Command(resume={"approved": True}), config)
```

### Critical rules for `interrupt()`

1. **Don't wrap in bare `try/except`** — `interrupt()` raises a special exception to pause. Catching it breaks the mechanism.
2. **Don't reorder or conditionally skip** — interrupts are matched by index on resume. If you have multiple `interrupt()` calls, they must execute in the same order every time.
3. **Side effects before `interrupt()` must be idempotent** — the node reruns from the beginning on resume.
4. **Only pass JSON-serializable values** to `interrupt()`.
5. **Requires a checkpointer** — compile with `checkpointer=` to enable.

### Common HITL patterns

See `references/hitl-patterns.md` for full examples of: approve/reject with routing, review and edit state, interrupts inside `@tool` functions, and multi-step input validation loops.

---

## Error Handling

Use the right strategy for each error type:

| Error Type | Strategy | Mechanism |
|---|---|---|
| **Transient** (network, rate limits) | Automatic retry | `RetryPolicy` on `add_node` |
| **LLM-recoverable** (tool failure, bad parse) | Loop back with error context | `Command(update={"error": msg}, goto="agent")` |
| **User-fixable** (missing info, ambiguous input) | Pause for input | `interrupt()` |
| **Unexpected** | Let bubble up | Don't catch what you can't handle |

```python
from langgraph.types import RetryPolicy

# Transient: automatic retry with backoff
builder.add_node(
    "call_api", api_node,
    retry_policy=RetryPolicy(max_attempts=3, retry_on=ConnectionError)
)

# LLM-recoverable: store error and loop back
def tool_node(state) -> Command[Literal["agent", "tool_node"]]:
    try:
        result = run_tool(state["tool_call"])
        return Command(update={"tool_result": result}, goto="agent")
    except ToolError as e:
        return Command(update={"tool_result": f"Error: {e}"}, goto="agent")
```

### Loop safety

Protect against infinite loops with `RemainingSteps`:

```python
from langgraph.managed import RemainingSteps

class State(TypedDict):
    messages: Annotated[list, add_messages]
    remaining_steps: RemainingSteps

def agent_node(state):
    if state["remaining_steps"] <= 2:
        return {"messages": [AIMessage(content="Stopping — too many iterations.")]}
    # ... normal agent logic
```

---

## Persistence and Checkpointing

Checkpointing enables resume-after-crash, human-in-the-loop, time travel, and debugging.

```python
from langgraph.checkpoint.memory import MemorySaver  # Dev/test
# from langgraph.checkpoint.postgres import PostgresSaver  # Production

graph = builder.compile(checkpointer=MemorySaver())
config = {"configurable": {"thread_id": "session-123"}}
result = graph.invoke(inputs, config)

# Resume after interrupt or failure (None = resume from last checkpoint):
result = graph.invoke(None, config)
```

**Time travel** — browse history, fork, and replay:

```python
for snapshot in graph.get_state_history(config):
    print(snapshot.config, snapshot.values)

# Fork from checkpoint and resume
fork_config = {**config, "configurable": {**config["configurable"], "checkpoint_id": "abc"}}
graph.update_state(fork_config, {"field": "new_value"}, as_node="some_node")
graph.invoke(None, fork_config)
```

**Cross-thread memory** — for data persisting across threads (user profiles, shared knowledge), use `Store`. See `references/memory-and-store.md` for Store API, semantic search, checkpointer options (Postgres, Redis, MongoDB, Cosmos), and encryption.

---

## Durable Execution

LangGraph checkpoints at node boundaries. If a node reruns on resume, side effects may execute twice. Wrap them in `@task` — this works in both Functional API entrypoints AND Graph API nodes:

```python
from langgraph.func import task

@task
def send_email(to: str, body: str):
    """Won't re-execute on resume if already completed."""
    return email_service.send(to=to, body=body)

def notification_node(state):
    result = send_email(state["email"], state["body"]).result()
    return {"sent": True}
```

**Durability modes**: `"async"` (default, checkpoint in background), `"sync"` (block until written), `"exit"` (checkpoint only at completion). Pass as `graph.stream(inputs, config, durability="sync")`.

---

## Streaming

Five stream modes, usable individually or combined:

| Mode | What it streams |
|------|----------------|
| `values` | Full state after each node |
| `updates` | State delta from each node |
| `messages` | LLM tokens as generated (for chat UIs) |
| `custom` | Custom data via `get_stream_writer()` |
| `debug` | Internal execution events |

```python
# Combine modes — returns (mode, chunk) tuples
for mode, chunk in graph.stream(inputs, config, stream_mode=["updates", "messages"]):
    if mode == "messages":
        print(chunk.content, end="", flush=True)
    elif mode == "updates":
        print(f"\nNode finished: {chunk}")
```

Emit custom data from nodes with `get_stream_writer()` — see `references/streaming-deep-dive.md` for custom streaming, subgraph streaming, async patterns, and non-LangChain LLM streaming.

---

## Runtime Configuration

Use `context_schema` and `Runtime` for clean dependency injection:

```python
from langgraph.runtime import Runtime

class AppContext(TypedDict):
    llm_provider: str
    db_connection: str

builder = StateGraph(MyState, context_schema=AppContext)

def my_node(state: MyState, runtime: Runtime[AppContext]):
    llm = init_chat_model(runtime.context.llm_provider)
    return {"messages": [llm.invoke(state["messages"])]}

graph = builder.compile()
graph.invoke(inputs, context={"llm_provider": "anthropic", "db_connection": "..."})
```

---

## Core Workflow Patterns

### Pattern 1: Routing with Command

```python
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from typing import Literal

class State(TypedDict):
    input: str
    category: str | None
    output: str | None

def classify(state: State) -> Command[Literal["handle_a", "handle_b", "handle_c"]]:
    category = llm.with_structured_output(Category).invoke(state["input"])
    return Command(update={"category": category.name}, goto=f"handle_{category.name}")

def handle_a(state: State):
    return {"output": llm.invoke(f"Handle category A: {state['input']}").content}

builder = StateGraph(State)
builder.add_node("classify", classify)
builder.add_node("handle_a", handle_a)
builder.add_node("handle_b", handle_b)
builder.add_node("handle_c", handle_c)
builder.add_edge(START, "classify")
# No add_conditional_edges needed — Command handles routing
builder.add_edge("handle_a", END)
builder.add_edge("handle_b", END)
builder.add_edge("handle_c", END)
graph = builder.compile()
```

### Pattern 2: Parallel Fan-Out/Fan-In with Send

```python
from langgraph.types import Send

class MainState(TypedDict):
    items: list[dict]
    results: Annotated[list[dict], add]

class WorkerState(TypedDict):
    item: dict
    results: Annotated[list[dict], add]

def fan_out(state: MainState):
    return [Send("process_item", {"item": item}) for item in state["items"]]

def process_item(state: WorkerState):
    result = do_work(state["item"])
    return {"results": [result]}

builder = StateGraph(MainState)
builder.add_node("process_item", process_item)
builder.add_node("synthesize", synthesize_fn)
builder.add_conditional_edges(START, fan_out, ["process_item"])
builder.add_edge("process_item", "synthesize")
builder.add_edge("synthesize", END)
```

For fan-out with uneven branch lengths, use `defer=True` on the reduce node to wait for all branches:

```python
builder.add_node("synthesize", synthesize_fn, defer=True)
```

### Pattern 3: Tool-Calling Agent (ReAct Loop)

```python
from langgraph.graph import MessagesState, START, END
from langchain.messages import SystemMessage, ToolMessage

tools_by_name = {t.name: t for t in tools}
llm_with_tools = llm.bind_tools(tools)

def agent(state: MessagesState):
    return {"messages": [llm_with_tools.invoke(
        [SystemMessage(content="You are a helpful assistant.")] + state["messages"]
    )]}

def tool_executor(state: MessagesState):
    return {"messages": [
        ToolMessage(content=str(tools_by_name[c["name"]].invoke(c["args"])), tool_call_id=c["id"])
        for c in state["messages"][-1].tool_calls
    ]}

def should_continue(state: MessagesState):
    return "tools" if state["messages"][-1].tool_calls else END

builder = StateGraph(MessagesState)
builder.add_node("agent", agent)
builder.add_node("tools", tool_executor)
builder.add_edge(START, "agent")
builder.add_conditional_edges("agent", should_continue, ["tools", END])
builder.add_edge("tools", "agent")
graph = builder.compile()
```

### Pattern 4: Evaluator-Optimizer Loop

```python
def generate(state) -> Command[Literal["evaluate"]]:
    prompt = f"Improve: {state['feedback']}\n{state['draft']}" if state.get("feedback") \
             else f"Write about: {state['topic']}"
    return Command(update={"draft": llm.invoke(prompt).content}, goto="evaluate")

def evaluate(state) -> Command[Literal["generate", END]]:
    grade = evaluator_llm.with_structured_output(Grade).invoke(state["draft"])
    if grade.passed:
        return Command(goto=END)
    return Command(update={"feedback": grade.feedback}, goto="generate")
```

### Pattern 5: Subgraph Multi-Agent Coordination

Two approaches for composing agents as subgraphs:

```python
# Option 1: Add compiled graph as node (shared state keys auto-mapped)
agent_a_graph = build_agent_a()  # Returns compiled StateGraph
builder.add_node("agent_a", agent_a_graph)

# Option 2: Invoke from node (different schemas — you map state manually)
def run_agent_a(state: OrchestratorState):
    result = agent_a_graph.invoke({"task": state["task"], "messages": []})
    return {"result": result["messages"][-1].content}
builder.add_node("agent_a", run_agent_a)
```

Only compile the parent graph with a checkpointer — it auto-propagates to subgraphs. For subgraphs needing isolated memory (per-agent message history), compile with `checkpointer=True`. Use `Command(goto=Command.PARENT)` for subgraph-to-parent handoffs.

---

## Node-Level Features

### Caching expensive nodes

```python
from langgraph.types import CachePolicy
from langgraph.cache.memory import InMemoryCache

builder.add_node("expensive_lookup", lookup_fn, cache_policy=CachePolicy(ttl=120))
graph = builder.compile(cache=InMemoryCache())
```

### The Overwrite escape hatch

Normally, fields with reducers accumulate. To force-replace a reducer field:

```python
from langgraph.types import Overwrite

def reset_node(state):
    return {"messages": Overwrite([])}  # Clears the list instead of appending
```

Only one node can use `Overwrite` on a given key per superstep.

---

## Quick Start Template

```python
from langgraph.graph import StateGraph, START, END, MessagesState
from langgraph.types import Command, interrupt
from langgraph.checkpoint.memory import MemorySaver
from langchain.chat_models import init_chat_model
from typing import Literal

class MyState(MessagesState):
    result: str | None

def process(state: MyState) -> Command[Literal["review", END]]:
    response = init_chat_model("claude-sonnet-4-5-20250929").invoke(state["messages"])
    return Command(update={"messages": [response], "result": response.content},
                   goto="review" if needs_review(response) else END)

def review(state: MyState) -> Command[Literal["process", END]]:
    decision = interrupt({"draft": state["result"], "action": "Review this"})
    if decision.get("approved"):
        return Command(goto=END)
    return Command(update={"messages": [HumanMessage(content=decision["feedback"])]}, goto="process")

builder = StateGraph(MyState)
builder.add_node("process", process)
builder.add_node("review", review)
builder.add_edge(START, "process")
graph = builder.compile(checkpointer=MemorySaver())

result = graph.invoke({"messages": [HumanMessage(content="Analyze this")]},
                      {"configurable": {"thread_id": "1"}})
```

---

## Common Pitfalls

- **State bloat** — Use reducers only for fields that genuinely accumulate. Most fields should replace on update.
- **Infinite loops** — Use `RemainingSteps` or a manual counter. Always have an exit condition.
- **Side effects re-executing** — Wrap API calls/writes in `@task` for durability, or ensure idempotency.
- **`interrupt()` in try/except** — Never catch the exception `interrupt()` raises. It's the pause mechanism, not an error.
- **Parallel file conflicts** — In fan-out, ensure each worker owns distinct files/resources.
- **Missing checkpointer** — Without it, `interrupt()` won't work and state won't persist.

---

## Further Reading

- `references/hitl-patterns.md` — Detailed human-in-the-loop patterns (approve/reject, edit, tool interrupts, validation loops)
- `references/memory-and-store.md` — Cross-thread memory, semantic search on Store, summarization
- `references/streaming-deep-dive.md` — Subgraph streaming, LLM token streaming, streaming with HITL
- **LangGraph docs**: https://docs.langchain.com/langgraph/
