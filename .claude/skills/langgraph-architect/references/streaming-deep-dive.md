# Streaming Deep Dive

## Stream Modes

LangGraph supports five stream modes, usable individually or combined:

| Mode | Returns | Use Case |
|------|---------|----------|
| `values` | Full state after each node | Simple debugging, state inspection |
| `updates` | State delta from each node | Efficient progress tracking |
| `messages` | LLM tokens as generated | Real-time chat UIs |
| `custom` | Data from `get_stream_writer()` | Progress bars, intermediate results |
| `debug` | Internal execution events | Deep debugging |

### Multi-mode streaming

Combine modes to get both granular LLM output and node-level progress:

```python
for mode, chunk in graph.stream(inputs, config, stream_mode=["updates", "messages"]):
    if mode == "messages":
        # Token-by-token LLM output with metadata
        msg, metadata = chunk
        print(msg.content, end="", flush=True)
    elif mode == "updates":
        node_name = list(chunk.keys())[0]
        print(f"\n[{node_name} completed]")
```

### Filtering LLM token streams

The `messages` mode includes metadata for filtering by node or tag:

```python
for msg, metadata in graph.stream(inputs, config, stream_mode="messages"):
    if metadata.get("langgraph_node") == "agent":
        # Only show tokens from the agent node, not tool nodes
        print(msg.content, end="", flush=True)
```

## Custom Streaming with `get_stream_writer()`

Emit custom data from inside any node:

```python
from langgraph.config import get_stream_writer

def analysis_node(state):
    writer = get_stream_writer()

    writer({"type": "progress", "step": "Loading data..."})
    data = load_data(state["source"])

    writer({"type": "progress", "step": "Running analysis..."})
    result = analyze(data)

    writer({"type": "preview", "content": result[:200]})
    return {"analysis": result}
```

Consume with `stream_mode="custom"`:

```python
for chunk in graph.stream(inputs, config, stream_mode="custom"):
    if chunk["type"] == "progress":
        update_progress_bar(chunk["step"])
```

### Streaming from non-LangChain LLMs

If you're using an LLM client that isn't LangChain-based, use `get_stream_writer()` to emit tokens manually:

```python
def custom_llm_node(state):
    writer = get_stream_writer()

    for token in my_custom_llm.stream(state["prompt"]):
        writer({"type": "token", "content": token})

    return {"response": my_custom_llm.last_response}
```

## Subgraph Streaming

Stream from nested subgraphs by passing `subgraphs=True`:

```python
for namespace, mode, chunk in graph.stream(inputs, config, stream_mode="updates", subgraphs=True):
    if namespace:
        # namespace is a tuple like ("parent_node:child_id",)
        print(f"  Subgraph {namespace}: {chunk}")
    else:
        print(f"Parent: {chunk}")
```

## Async Streaming

All streaming methods have async variants:

```python
async for chunk in graph.astream(inputs, config, stream_mode="updates"):
    await process(chunk)

async for msg, metadata in graph.astream(inputs, config, stream_mode="messages"):
    await websocket.send(msg.content)
```
