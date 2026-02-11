# Human-in-the-Loop Patterns

Detailed patterns for using `interrupt()` in LangGraph workflows. All patterns require compiling with a checkpointer.

## Pattern 1: Approve / Reject with Routing

The most common pattern. Pause for approval, then route based on the decision:

```python
from langgraph.types import interrupt, Command
from typing import Literal

def review_node(state) -> Command[Literal["execute", "revise", END]]:
    decision = interrupt({
        "draft": state["draft"],
        "context": state.get("classification"),
        "action": "Approve, reject, or request revision"
    })

    if decision["action"] == "approve":
        return Command(
            update={"approved_draft": decision.get("edited", state["draft"])},
            goto="execute"
        )
    elif decision["action"] == "revise":
        return Command(
            update={"revision_notes": decision["notes"]},
            goto="revise"
        )
    else:
        return Command(goto=END)
```

Resume:

```python
graph.invoke(Command(resume={"action": "approve", "edited": "..."}), config)
```

## Pattern 2: Review and Edit State

Let the human directly modify state before continuing:

```python
def human_edit_node(state) -> Command[Literal["continue"]]:
    edits = interrupt({
        "current_plan": state["plan"],
        "message": "Edit the plan as needed"
    })

    return Command(
        update={"plan": edits["updated_plan"]},
        goto="continue"
    )
```

## Pattern 3: Interrupt Inside a Tool

You can call `interrupt()` inside a LangChain `@tool` function. This lets an agent ask for confirmation before executing a sensitive action:

```python
from langchain.tools import tool
from langgraph.types import interrupt

@tool
def delete_records(query: str) -> str:
    """Delete records matching the query. Requires human confirmation."""
    confirmation = interrupt({
        "tool": "delete_records",
        "query": query,
        "message": f"About to delete records matching: {query}. Confirm?"
    })

    if not confirmation.get("confirmed"):
        return "Deletion cancelled by user."

    count = db.delete(query)
    return f"Deleted {count} records."
```

The graph pauses mid-tool-execution. On resume, the tool continues from where `interrupt()` returned.

## Pattern 4: Multi-Step Input Validation Loop

Use multiple `interrupt()` calls in a loop to collect and validate information:

```python
def collect_info_node(state) -> Command[Literal["process"]]:
    info = {}

    while True:
        user_input = interrupt({
            "collected_so_far": info,
            "message": "Please provide the missing fields",
            "required": [f for f in ["name", "email", "account_id"] if f not in info]
        })

        info.update(user_input)

        # Validate
        if all(k in info for k in ["name", "email", "account_id"]):
            break

    return Command(update={"customer_info": info}, goto="process")
```

Each iteration through the loop creates a new `interrupt()`. The index-based matching rule means you must not reorder or conditionally skip interrupts — always execute them in the same sequence.

## Streaming with HITL

Detect interrupts in streaming mode to build real-time UIs:

```python
for mode, chunk in graph.stream(inputs, config, stream_mode=["messages", "updates"]):
    if mode == "updates" and "__interrupt__" in chunk:
        interrupt_data = chunk["__interrupt__"]
        # Show approval UI to user
        break
    elif mode == "messages":
        print(chunk.content, end="", flush=True)
```

## Runtime interrupt_before / interrupt_after

For debugging, you can pass interrupt points at invocation time without changing the graph:

```python
result = graph.invoke(inputs, config, interrupt_before=["sensitive_node"])
# Inspect state, then resume:
result = graph.invoke(None, config)
```

These are best for debugging — for production HITL, use `interrupt()` inside nodes.
