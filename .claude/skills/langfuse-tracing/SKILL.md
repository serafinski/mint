---
name: langfuse-tracing
description: "Integrate LangFuse observability and tracing into LangChain, LangGraph, and LangChain DeepAgents applications. Use this skill when implementing tracing for agent workflows, multi-agent systems, or any LLM application using LangChain/LangGraph. Triggers include: mentions of 'tracing', 'observability', 'LangFuse', 'debugging LLM calls', 'monitoring agents', 'trace LLM execution', or when working with LangChain/LangGraph and needing visibility into execution flows. Also use when implementing advanced patterns like distributed tracing, trace scoring, prompt management, or multi-agent trace grouping."
---

# LangFuse Tracing Integration

Integrate LangFuse observability into LangChain, LangGraph, and DeepAgents applications for comprehensive tracing and debugging.

## Quick Reference

| Task | Approach |
|------|----------|
| Basic LangChain tracing | Add `CallbackHandler` to config |
| LangGraph tracing | Same - add handler to invoke config |
| DeepAgents tracing | Same - add handler to invoke config |
| Group multiple runs in one trace | Use Langfuse SDK with `start_as_current_observation` |
| Add trace metadata | Use `metadata` in config or SDK `update_trace` |
| Score a trace | Use `langfuse.score_current_trace()` or `create_score()` |
| Custom trace IDs | Use `trace_context={"trace_id": ...}` in SDK |

---

## Basic Integration

### Setup

```python
from langfuse.langchain import CallbackHandler

# Initialize handler (uses environment variables)
langfuse_handler = CallbackHandler()
```

**Required environment variables:**
```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000  # or https://cloud.langfuse.com
```

### LangChain Integration

```python
from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate

llm = ChatOpenAI(model="gpt-4o")
prompt = ChatPromptTemplate.from_template("Tell me about {topic}")
chain = prompt | llm

# Add handler to config
response = chain.invoke(
    {"topic": "agents"},
    config={"callbacks": [langfuse_handler]}
)
```

### LangGraph Integration

```python
from langgraph.graph import StateGraph
from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

class State(TypedDict):
    messages: Annotated[list, add_messages]

def agent_node(state: State):
    # Your agent logic
    return {"messages": [...]}

workflow = StateGraph(State)
workflow.add_node("agent", agent_node)
# ... build graph ...
graph = workflow.compile()

# Add handler to config
result = graph.invoke(
    {"messages": [...]},
    config={"callbacks": [langfuse_handler]}
)
```

### LangChain DeepAgents Integration

```python
from deepagents import create_deep_agent

agent = create_deep_agent(
    tools=[internet_search],
    system_prompt="You are a researcher...",
)

# Add handler to config
result = agent.invoke(
    {"messages": [{"role": "user", "content": "Research topic X"}]},
    config={"callbacks": [langfuse_handler]}
)
```

---

## Advanced Patterns

### Pattern 1: Grouping Multiple Runs in One Trace

When you want multiple agent invocations to appear as one logical trace:

```python
from langfuse import get_client

langfuse = get_client()
langfuse_handler = CallbackHandler()

# Create trace context
with langfuse.start_as_current_observation(
    as_type="span",
    name="multi-step-research"
) as span:
    
    # Set trace attributes
    span.update_trace(
        user_id="user_123",
        session_id="session_abc",
        tags=["research", "multi-step"],
        input={"query": "Overall research question"}
    )
    
    # Step 1: Research
    research_result = research_agent.invoke(
        {"messages": [...]},
        config={"callbacks": [langfuse_handler]}
    )
    
    # Step 2: Critique
    critique_result = critique_agent.invoke(
        {"messages": [...]},
        config={"callbacks": [langfuse_handler]}
    )
    
    # Step 3: Synthesis
    final_result = synthesis_agent.invoke(
        {"messages": [...]},
        config={"callbacks": [langfuse_handler]}
    )
    
    # Update trace output
    span.update_trace(output={"final_report": final_result})
```

**Result:** All three agent runs appear under one trace in LangFuse.

### Pattern 2: Adding Trace Metadata

Set user_id, session_id, tags dynamically per invocation:

**Option A: Via metadata (simplest):**

```python
response = chain.invoke(
    {"topic": "agents"},
    config={
        "callbacks": [langfuse_handler],
        "metadata": {
            "langfuse_user_id": "user_123",
            "langfuse_session_id": "session_abc",
            "langfuse_tags": ["experiment-v1", "production"]
        }
    }
)
```

**Option B: Via SDK (more control):**

```python
from langfuse import get_client, propagate_attributes

langfuse = get_client()
langfuse_handler = CallbackHandler()

with langfuse.start_as_current_observation(as_type="span", name="user-query") as span:
    # Propagate attributes to all child observations
    with propagate_attributes(
        user_id="user_123",
        session_id="session_abc",
        tags=["experiment-v1"],
        metadata={"experiment": "variant_a"}
    ):
        response = chain.invoke(
            {"topic": "agents"},
            config={"callbacks": [langfuse_handler]}
        )
```

### Pattern 3: Custom Trace IDs (Distributed Tracing)

For correlating traces across multiple systems:

```python
from langfuse import get_client, Langfuse

langfuse = get_client()
langfuse_handler = CallbackHandler()

# Generate deterministic trace ID
external_request_id = "req_12345"
predefined_trace_id = Langfuse.create_trace_id(seed=external_request_id)

# Use the trace ID
with langfuse.start_as_current_observation(
    as_type="span",
    name="agent-request",
    trace_context={"trace_id": predefined_trace_id}
) as span:
    
    span.update_trace(input={"query": "user question"})
    
    response = agent.invoke(
        {"messages": [...]},
        config={"callbacks": [langfuse_handler]}
    )
    
    span.update_trace(output={"response": response})

print(f"Trace ID: {predefined_trace_id}")  # Use for scoring later
```

**Use case:** External monitoring system can correlate with LangFuse trace.

### Pattern 4: Multi-Agent Traces with Sub-Agents

For complex multi-agent setups (e.g., supervisor + workers):

```python
from langfuse import get_client, Langfuse

langfuse = get_client()
predefined_trace_id = Langfuse.create_trace_id()

# Main agent
with langfuse.start_as_current_observation(
    name="🤖-main-agent",
    trace_context={"trace_id": predefined_trace_id}
) as main_span:
    
    main_span.update_trace(input={"task": "Research topic X"})
    
    # Main agent calls sub-agent as a tool
    main_result = main_agent.invoke(
        {"messages": [...]},
        config={"callbacks": [CallbackHandler()]}
    )
    
    main_span.update_trace(output=main_result)

# Sub-agent (different graph, same trace)
def research_subtask(question: str):
    """Sub-agent for focused research"""
    
    with langfuse.start_as_current_observation(
        name="🤖-sub-research-agent",
        trace_context={"trace_id": predefined_trace_id}  # Same trace ID
    ) as sub_span:
        
        sub_span.update_trace(input=question)
        
        response = research_sub_agent.invoke(
            {"messages": [...]},
            config={"callbacks": [CallbackHandler()]}
        )
        
        sub_span.update_trace(output=response)
        
        return response
```

**Result:** Main agent and all sub-agents appear in one unified trace.

### Pattern 5: Scoring Traces

Add evaluation scores to traces:

```python
from langfuse import get_client

langfuse = get_client()

# Option 1: Score during execution
with langfuse.start_as_current_observation(as_type="span", name="agent-run") as span:
    result = agent.invoke(...)
    
    # Score using span object
    span.score_trace(
        name="user-feedback",
        value=1,
        data_type="NUMERIC",
        comment="This was correct, thank you"
    )

# Option 2: Score using current context
with langfuse.start_as_current_observation(as_type="span", name="agent-run") as span:
    result = agent.invoke(...)
    
    # Score using current context
    langfuse.score_current_trace(
        name="user-feedback",
        value=1,
        data_type="NUMERIC"
    )

# Option 3: Score after execution (using trace ID)
langfuse.create_score(
    trace_id=predefined_trace_id,
    name="correctness",
    value=0.9,
    data_type="NUMERIC",
    comment="Factually accurate"
)
```

**Score types:**
- `NUMERIC`: 0-1 scores (e.g., correctness, relevance)
- `CATEGORICAL`: Labels (e.g., "good", "bad", "neutral")
- `BOOLEAN`: True/False

---

## Integration with @observe Decorator

For automatic tracing of functions:

```python
from langfuse import observe, get_client, propagate_attributes
from langfuse.langchain import CallbackHandler

@observe()  # Automatically creates a trace
def process_user_query(user_input: str):
    langfuse = get_client()
    
    # Propagate trace attributes
    with propagate_attributes(
        session_id="session-1234",
        user_id="user-5678",
    ):
        langfuse_handler = CallbackHandler()
        
        # LangChain/LangGraph execution
        result = chain.invoke(
            {"input": user_input},
            config={"callbacks": [langfuse_handler]}
        )
        
        # Update trace
        langfuse.update_current_trace(
            name="user-query-processing",
            input={"query": user_input},
            output={"response": result.content}
        )
        
    return result.content

# Usage
answer = process_user_query("What is the capital of France?")
```

---

## Prompt Management with LangFuse

Manage and version prompts in LangFuse UI:

### Create Prompt (via SDK or UI)

```python
from langfuse import get_client

langfuse = get_client()

# Create prompt in LangFuse
langfuse.create_prompt(
    name="translator-system-prompt",
    prompt="You are a translator that translates every input text into {{language}}.",
    labels=["production"]  # "production" makes it the default
)
```

### Use Prompt in Agent

```python
# Fetch prompt from LangFuse
langfuse_prompt = langfuse.get_prompt("translator-system-prompt")
langchain_prompt = langfuse_prompt.get_langchain_prompt()

# Use in agent
system_prompt = {
    "role": "system",
    "content": langchain_prompt
}

def agent_node(state: State):
    messages_with_prompt = [system_prompt] + state["messages"]
    response = llm.invoke(messages_with_prompt)
    return {"messages": [response]}
```

**Benefits:**
- Version prompts in UI without code changes
- A/B test different prompt versions
- Track which prompt version was used in each trace

---

## Flushing and Shutdown

LangFuse queues events in background. For short-lived scripts:

```python
from langfuse import get_client

langfuse = get_client()

# After all agent invocations
langfuse.flush()  # Wait for all events to send
# or
langfuse.shutdown()  # Flush and close client
```

**When to use:**
- Scripts that exit immediately after execution
- Serverless functions
- Test suites

**When NOT to use:**
- Long-running applications (handles automatically)
- Production web servers

---

## Debugging Tips

### View Trace URL

```python
# After invocation, get the last trace ID
trace_id = langfuse_handler.last_trace_id
print(f"View trace: https://cloud.langfuse.com/trace/{trace_id}")
```

**Note:** `last_trace_id` can be unreliable in concurrent environments where the handler is reused.

### Verify Integration

```python
from langfuse import get_client

langfuse = get_client()

# Check authentication
if langfuse.auth_check():
    print("LangFuse connected successfully!")
else:
    print("Authentication failed - check environment variables")
```

### Common Issues

**Issue:** Traces not appearing in LangFuse
- Check environment variables (PUBLIC_KEY, SECRET_KEY, HOST)
- Call `langfuse.flush()` in short-lived scripts
- Verify network connectivity to LangFuse host

**Issue:** Traces are split instead of grouped
- Make sure you're using the same `trace_context` or `@observe()` scope
- Don't create multiple `CallbackHandler` instances - reuse the same one

**Issue:** Metadata not appearing
- Use `langfuse_user_id` (with underscore), not `user_id` in metadata
- Or use SDK methods (`span.update_trace(user_id=...)`) instead

---

## Example: Complete Multi-Agent Research System

```python
from langfuse import get_client, Langfuse, observe, propagate_attributes
from langfuse.langchain import CallbackHandler
from deepagents import create_deep_agent

langfuse = get_client()

@observe()
def research_workflow(user_question: str):
    # Generate trace ID for distributed tracing
    trace_id = Langfuse.create_trace_id()
    
    # Propagate attributes to all child observations
    with propagate_attributes(
        user_id="user_123",
        session_id="session_abc",
        tags=["research", "deep-agents"],
        metadata={"experiment": "multi-agent-v2"}
    ):
        langfuse_handler = CallbackHandler()
        
        # Step 1: Main research agent
        with langfuse.start_as_current_observation(
            name="main-research",
            trace_context={"trace_id": trace_id}
        ) as main_span:
            
            research_result = research_agent.invoke(
                {"messages": [{"role": "user", "content": user_question}]},
                config={"callbacks": [langfuse_handler]}
            )
            
            main_span.update(output=research_result)
        
        # Step 2: Critique agent
        with langfuse.start_as_current_observation(
            name="critique",
            trace_context={"trace_id": trace_id}
        ) as critique_span:
            
            critique_result = critique_agent.invoke(
                {"messages": [{"role": "user", "content": f"Critique: {research_result}"}]},
                config={"callbacks": [langfuse_handler]}
            )
            
            critique_span.update(output=critique_result)
        
        # Step 3: Final synthesis
        with langfuse.start_as_current_observation(
            name="synthesis",
            trace_context={"trace_id": trace_id}
        ) as synthesis_span:
            
            final_result = synthesis_agent.invoke(
                {"messages": [{"role": "user", "content": f"Synthesize: {research_result} and {critique_result}"}]},
                config={"callbacks": [langfuse_handler]}
            )
            
            synthesis_span.update(output=final_result)
        
        # Update main trace
        langfuse.update_current_trace(
            name="deep-research-workflow",
            input={"question": user_question},
            output={"final_report": final_result}
        )
        
        # Score the trace
        langfuse.score_current_trace(
            name="completion",
            value=1,
            data_type="BOOLEAN"
        )
        
    return final_result

# Usage
report = research_workflow("What are the latest developments in AI agents?")
```

---

## Summary

LangFuse tracing for LangChain/LangGraph/DeepAgents:

1. **Basic tracing** - Add `CallbackHandler` to `config={"callbacks": [...]}`
2. **Trace grouping** - Use SDK context managers with same `trace_id`
3. **Metadata** - Set via `metadata={"langfuse_user_id": ...}` or SDK
4. **Scoring** - Use `score_current_trace()` or `create_score()`
5. **Prompts** - Manage in LangFuse UI, fetch with `get_prompt()`
6. **Flushing** - Call `langfuse.flush()` in short-lived scripts

**Key principle:** LangChain/LangGraph handles execution, LangFuse handles observability. The two work together via callbacks.
