---
name: deep-agents
description: "When and how to use the deepagents package (LangChain Deep Agents) vs. plain LangGraph nodes in the JIT capability synthesis pipeline. Use this skill whenever implementing a component that might need multi-step autonomous reasoning (task decomposition, adversarial probing, failure analysis), whenever someone mentions 'DeepAgent', 'create_deep_agent', 'deepagents', 'write_todos', 'subagent spawning', or 'agent harness', or whenever deciding whether a pipeline node should be a plain LangGraph node or a DeepAgent. Also trigger when someone is wiring a DeepAgent into a LangGraph pipeline — the integration pattern matters and getting it wrong causes hard-to-debug errors. Also trigger when generating skills or agents that target the DeepAgents runtime."
---

# Deep Agents

The `deepagents` package provides an agent harness with built-in planning (`write_todos`), file system context management (auto-evicts large results to files), subagent delegation (`task` tool), and conversation summarization. Use it when a component needs autonomous multi-step reasoning. Use a plain LangGraph node when a single LLM call or deterministic logic suffices.

## Decision Framework

| Component | Use DeepAgent? | Why |
|---|---|---|
| Task decomposition | **Yes** | Needs planning, exploration, critique — multi-step |
| Adversarial probing | **Yes** | Needs to plan attacks, execute probes, adapt strategy |
| Failure analysis (optimizer) | **Optional** | Useful for investigating complex failure patterns |
| Capability classifier | No | LLM classification (single call, `temperature=0`) |
| MCP tool/skill/agent generators | No | Single-pass generation with retry on failure |
| Static scanner | No | Deterministic AST + regex, no LLM |
| Behavioral testing | No | Test generation + sandbox execution |
| Metrics computation | No | Deterministic queries |
| Granularity operations | No | Metrics-driven decisions |

## Core API

`create_deep_agent()` returns a `CompiledStateGraph` — a complete agent, not a LangGraph node. Invoke it directly:

```python
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from src.config import DEFAULT_LLM_MODEL

agent = create_deep_agent(
    model=init_chat_model(DEFAULT_LLM_MODEL),
    tools=[my_tool],                          # Optional — built-ins are always available
    system_prompt="You are...",
    # Optional:
    subagents=[...],
    backend=...,                          # file system backend (sandbox, state, etc.)
    skills=[...],                         # skill directories
    memory=[...],                         # AGENTS.md files
    response_format=MyPydanticModel,      # structured output
    interrupt_on={...},                   # human-in-the-loop
    checkpointer=...,                     # required for HITL
)

result = agent.invoke(
    {"messages": [{"role": "user", "content": "..."}]},
    config={"callbacks": [langfuse_handler]}
)
```

## Built-in Capabilities

These come for free with every DeepAgent — no configuration needed:

- **Planning**: `write_todos` tool for task decomposition and progress tracking
- **File system**: `ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
- **Shell execution**: `execute` tool for running shell commands — requires a `backend` implementing `SandboxBackendProtocol`; without one, returns an error. **None of our current invocations pass a backend**, so `execute` is non-functional in this project. We use the explicit `sandbox_execute` tool instead.
- **Context management**: Large tool results (>20k tokens) auto-evict to files
- **Subagent spawning**: `task` tool delegates work with context isolation — requires `subagents=` parameter. **None of our current invocations pass subagents**, so `task` is non-functional in this project.
- **Conversation summarization**: Auto-compresses old messages at 85% context usage

The `tools` parameter in `create_deep_agent()` is **optional** — it adds custom tools on top of the built-ins. If the agent only needs planning and file operations (e.g., task decomposition), omit it entirely. In this project, we pass `tools=[sandbox_execute]` when the agent needs Docker-isolated Python execution (adversarial probing, skill/agent evaluation).

## Integration Pattern: DeepAgent Inside a LangGraph Node

A DeepAgent is a complete graph. To use it inside the larger LangGraph pipeline, invoke it from within a node function. This is the only correct integration pattern — do not try to add a DeepAgent as a node directly.

```python
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from src.schemas.task import TaskState

# Create the agent once at module level
decomposition_agent = create_deep_agent(
    model=init_chat_model(DEFAULT_LLM_MODEL),  # reasoner lacks tool_choice support
    system_prompt="""You are a task decomposition specialist.

    Break the given task into independent subtasks.
    For each subtask, identify required capabilities.

    Use write_todos to plan your decomposition approach.
    Write your structured output to /output/subtasks.json.""",
    response_format=DecompositionResult,
)


def decomposition_node(state: TaskState) -> dict:
    """LangGraph node that delegates to a DeepAgent."""
    result = decomposition_agent.invoke(
        {"messages": [{"role": "user", "content": state.task_description}]},
        config={"callbacks": [langfuse_handler]}
    )
    subtasks = result.get("structured_response")
    return {"subtasks": subtasks.subtasks if subtasks else []}
```

## Integration Pattern: DeepAgent with Sandbox

For the adversarial probing tier, the DeepAgent needs to execute code in isolation. Use a sandbox backend, which gives the agent the `execute` tool for shell commands:

```python
adversarial_agent = create_deep_agent(
    model=init_chat_model(DEFAULT_LLM_MODEL),
    system_prompt="""You are a red-team agent testing whether a generated
    capability is genuinely functional or faking its outputs.

    1. Use write_todos to plan your attack approach
    2. Read the capability source from /capability/source.py
    3. Craft inputs to expose shortcut patterns
    4. Use execute to run tests in the sandbox
    5. Write findings to /output/findings.json

    Write intermediate analysis to files to manage context.""",
    backend=sandbox_backend,
    response_format=AdversarialFindings,
)
```

## Integration Pattern: Subagents for Context Isolation

DeepAgents can delegate focused work to subagents. The subagent's intermediate tool calls don't clutter the parent's context — only the final result comes back:

```python
research_subagent = {
    "name": "gap-analyzer",
    "description": "Analyzes capability gaps in depth",
    "system_prompt": "You analyze capability gaps and classify them...",
    "tools": [registry_search_tool],
    "model": DEFAULT_LLM_MODEL,
}

orchestrator = create_deep_agent(
    model=init_chat_model(DEFAULT_LLM_MODEL),
    system_prompt="You orchestrate the JIT capability pipeline...",
    subagents=[research_subagent],
)
```

For pre-built LangGraph graphs as subagents, use `CompiledSubAgent`:

```python
from deepagents import CompiledSubAgent

custom_subagent = CompiledSubAgent(
    name="custom-analyzer",
    description="Specialized analysis agent",
    runnable=my_compiled_langgraph,  # Must have "messages" in state
)
```

## DeepAgents Skill Ecosystem

DeepAgents consume skills at runtime from specific directories. This matters for the agent generator and skill generator in Pillar 1.

### Skill Locations (precedence low → high)

| # | Directory | Scope |
|---|-----------|-------|
| 1 | `~/.deepagents/<agent>/skills/` | User (deepagents alias) |
| 2 | `~/.agents/skills/` | User (shared) |
| 3 | `.deepagents/skills/` | Project (deepagents alias) |
| 4 | `.agents/skills/` | Project (shared) |

Project skills override user skills when names collide.

### Skill Structure

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter: name, description (required)
│   └── Markdown instructions
├── scripts/     (optional) - Executable code for deterministic tasks
├── references/  (optional) - Docs loaded into context as needed
└── assets/      (optional) - Files used in output (templates, etc.)
```

### Skill Validation Rules

These rules are enforced by `tools/quick_validate.py`:

- `name`: hyphen-case (`[a-z0-9-]+`), max 64 characters, no leading/trailing/consecutive hyphens
- `description`: string, max 1024 characters, no angle brackets (`<` or `>`)
- Allowed frontmatter keys: `name`, `description`, `license`, `compatibility`, `allowed-tools`, `metadata`
- Any other frontmatter keys cause validation failure

### Implications for Generators

- **Skill generator** (`src/orchestrator/generators/skill.py`): Must produce the canonical structure above. Run `tools/quick_validate.py` on output as a structural pre-check.
- **Agent generator** (`src/orchestrator/generators/agent.py`): When spawning DeepAgent-type agents, configure skill paths pointing to generated skills.
- **Verification gate** (`src/verification/gate.py`): For skill-type capabilities, run `quick_validate.py` as Tier 0 before the three-tier verification.

### Utilities

- `tools/init_skill.py <skill-name> --path <output-dir>` — Scaffolds a new skill with template SKILL.md and example resource directories
- `tools/quick_validate.py <skill-dir>` — Validates SKILL.md structure, frontmatter, naming

## Model Selection

All components use `DEFAULT_LLM_MODEL` from `src.config` (currently `openai:gpt-5-mini`, overridable via `JIT_LLM_MODEL` env var). Never hardcode a model string — always import from config.

```python
from langchain.chat_models import init_chat_model
from src.config import DEFAULT_LLM_MODEL

# For DeepAgents
agent_llm = init_chat_model(DEFAULT_LLM_MODEL)
agent = create_deep_agent(model=agent_llm, ...)

# For plain nodes (same model, single-step)
fast_llm = init_chat_model(DEFAULT_LLM_MODEL)
```

**Important:** The chosen model must support `tool_choice`, which DeepAgents requires for tool calling. Models with thinking/reasoning modes (e.g., `deepseek-reasoner`) may not support this — test before switching.

| Component | Model | Why |
|---|---|---|
| Task decomposition (DeepAgent) | `DEFAULT_LLM_MODEL` | Multi-step planning |
| Adversarial probing (DeepAgent) | `DEFAULT_LLM_MODEL` | Multi-step reasoning + sandbox |
| Failure analysis (DeepAgent, optional) | `DEFAULT_LLM_MODEL` | Investigation |
| Classifier, generators, behavioral testing | `DEFAULT_LLM_MODEL` | Single-step, speed matters |

## Token Budget

DeepAgents consume roughly 3-5x what a single LLM call costs because of built-in planning, file system operations, and the iterative tool-calling loop. Track costs via LangFuse. If costs are too high, consider whether the component really needs a DeepAgent or whether a plain LangGraph node with good prompting would suffice.

## LangFuse Tracing

Pass the callback handler in the invoke config. This traces the full execution including all tool calls, planning steps, and file operations:

```python
from langfuse.langchain import CallbackHandler

result = agent.invoke(
    {"messages": [...]},
    config={"callbacks": [CallbackHandler()]}
)
```

## Common Mistakes

**Wiring DeepAgents as LangGraph nodes directly.** `create_deep_agent()` returns a `CompiledStateGraph`, not a node function. Invoke it from within a node function.

**Skipping `response_format`.** Without it, you get raw messages back and have to parse them yourself. Use `response_format=YourPydanticModel` to get validated structured data in `result["structured_response"]`.

**Ignoring file system for context.** The auto-eviction handles tool outputs, but also instruct the agent in its system prompt to write intermediate results to files. This is the key mechanism for handling tasks that involve lots of data.

**Using DeepAgents for everything.** Most pipeline nodes don't need planning, file systems, or subagents. A single LLM call with good prompting is faster and cheaper. Reserve DeepAgents for genuinely multi-step reasoning tasks.

**Forgetting `checkpointer` for HITL.** If you use `interrupt_on`, you need a checkpointer for state persistence between the interrupt and resume.

**Generating skills that don't conform to format.** Always validate generated skills with `tools/quick_validate.py`. Common failures: name not hyphen-case, description too long, extra frontmatter keys.
