# Memory and Store

LangGraph has two persistence layers:

- **Checkpointer** — Per-thread state snapshots. Used for resume, time travel, and `interrupt()`. Data is scoped to a single `thread_id`.
- **Store** — Cross-thread key-value storage. Used for user profiles, shared knowledge, long-term memory. Data persists across threads and conversations.

## Managing Message History Size

For long conversations, manage context window limits:

### Selective removal

```python
from langchain_core.messages import RemoveMessage, REMOVE_ALL_MESSAGES

def cleanup_node(state):
    # Remove all but last 10 messages
    old = [RemoveMessage(id=m.id) for m in state["messages"][:-10]]
    return {"messages": old}

# Or clear everything
def reset_node(state):
    return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES)]}
```

### Token-based trimming

```python
from langchain_core.messages.utils import trim_messages, count_tokens_approximately

trimmed = trim_messages(
    state["messages"],
    max_tokens=4000,
    token_counter=count_tokens_approximately,
    strategy="last",  # Keep most recent messages
)
```

### Summarization with langmem

```python
from langmem.short_term import SummarizationNode

summarize = SummarizationNode(model=llm, max_tokens=500)

class State(MessagesState):
    context: dict  # Running summary lives here

builder.add_node("summarize", summarize)
```

## Checkpointer Options

| Checkpointer | Use Case |
|---|---|
| `MemorySaver` | Development / testing (in-memory, lost on restart) |
| `SqliteSaver` | Local persistence |
| `PostgresSaver` | Production |
| `MongoDBSaver` | Production (MongoDB) |
| `RedisSaver` | Production (Redis) |
| `CosmosDBSaver` | Production (Azure) |

```python
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver

# Dev
graph = builder.compile(checkpointer=MemorySaver())

# Production
checkpointer = PostgresSaver.from_conn_string("postgresql://user:pass@host/db")
graph = builder.compile(checkpointer=checkpointer)
```

### Checkpoint operations

```python
config = {"configurable": {"thread_id": "session-123"}}

# Get current state
state = graph.get_state(config)

# Browse full history
for snapshot in graph.get_state_history(config):
    print(snapshot.config["configurable"]["checkpoint_id"], snapshot.values)

# Fork from a checkpoint
fork_config = {**config, "configurable": {**config["configurable"], "checkpoint_id": "abc"}}
graph.update_state(fork_config, {"field": "new_value"}, as_node="node_name")
graph.invoke(None, fork_config)  # Resume from fork

# Delete a thread
checkpointer.delete_thread("session-123")
```

### Encrypted checkpoints

For sensitive data:

```python
from langgraph.checkpoint.serde.enc import EncryptedSerializer

serializer = EncryptedSerializer.from_pycryptodome_aes(key=encryption_key)
checkpointer = PostgresSaver(conn, serde=serializer)
```

## Store for Cross-Thread Memory

Store persists data across threads — ideal for user preferences, conversation summaries, shared agent knowledge.

```python
from langgraph.store.memory import InMemoryStore
# Production: from langgraph.store.postgres import PostgresStore

store = InMemoryStore()
graph = builder.compile(checkpointer=MemorySaver(), store=store)
```

### Accessing Store in nodes

```python
def personalized_node(state, *, store):
    user_id = state["user_id"]

    # Read
    prefs = store.search(("users", user_id, "preferences"))

    # Write
    store.put(("users", user_id, "preferences"), "theme", {"value": "dark", "set_at": "2025-01-15"})

    # Delete
    store.delete(("users", user_id, "preferences"), "old_key")
```

Keys are namespaced by tuples. The namespace pattern `(entity_type, entity_id, category)` keeps data organized.

### Semantic Search on Store

Store supports embedding-based search for finding relevant memories:

```python
from langchain.embeddings import init_embeddings

store = InMemoryStore(
    index={
        "embed": init_embeddings("openai:text-embedding-3-small"),
        "dims": 1536,
        "fields": ["$"],  # Index all fields
    }
)

# Later, in a node:
def memory_node(state, *, store):
    relevant = store.search(
        ("user", user_id, "memories"),
        query="What does the user prefer for lunch?",
        limit=5,
    )
    for item in relevant:
        print(item.key, item.value, item.score)
```

## Message Summarization

For long conversations, use summarization to compress history while preserving context. The `langmem` library provides a built-in `SummarizationNode`:

```python
from langmem.short_term import SummarizationNode

summarize = SummarizationNode(
    model=llm,
    max_tokens=500,
    # Keeps a running summary in state["context"]
)

class State(MessagesState):
    context: dict  # Running summary lives here

builder.add_node("summarize", summarize)
```

Alternatively, implement manual summarization in a node that trims old messages and stores a summary in state.
