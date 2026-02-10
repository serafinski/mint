# JIT Capability Synthesis for Self-Developing Multi-Agent Systems

**Master's Thesis Project - February 2026**

## Overview

This project explores just-in-time capability synthesis in multi-agent systems. The goal is to build a system that can generate, verify, and optimize its own capabilities on-demand.

**Research question:** How can we balance autonomy vs correctness, specificity vs generality, and context economy in self-developing multi-agent systems?

## Proposed Architecture (Three Pillars)

1. **JIT Capability Pipeline** - Generate capabilities on-demand (MCP tools, agent skills, or new agents)
2. **Verification Layer** - Three-tier verification to catch LLM generation failures
3. **Granularity Optimizer** - Evolutionary feedback loop to optimize skill granularity

See `docs/thesis_proposal.md` for full details.

## Tech Stack

- **LangGraph** - Multi-agent orchestration
- **LangChain DeepAgents** - Autonomous research agents with planning and critique
- **Claude Code** - Code generation
- **MCP SDK** - Tool serving
- **ChromaDB** - Skill registry (semantic search)
- **DuckDB** - Metrics storage
- **LangFuse** - Observability and tracing (local or cloud)
- **Docker** - Sandboxed execution
- **uv** - Python package management

## Package Management

This project uses **uv** for Python management.

### Common Commands

```bash
# Add a dependency
uv add <package-name>

# Remove a dependency
uv remove <package-name>

# Run a script
uv run script.py

# Run a command in project environment
uv run python -m pytest

# Sync dependencies (updates .venv to match pyproject.toml)
uv sync
```

## Observability with LangFuse

LangFuse is used for tracing LLM calls, agent execution, and debugging workflows.

### Integration with LangChain/LangGraph

LangFuse integrates via the `CallbackHandler`:

```python
from langfuse.langchain import CallbackHandler

# Initialize handler
langfuse_handler = CallbackHandler()

# Add to LangGraph/LangChain invocation
response = agent.invoke(
    {"messages": [...]},
    config={"callbacks": [langfuse_handler]}
)
```

### Environment Variables

```bash
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000  # or https://cloud.langfuse.com
```

**Note:** LangFuse deployment (Docker/cloud) is managed separately. See project docs for setup details.

## Development Guidelines

### State Management

Use **structured state schemas** (TypedDict, Pydantic) instead of conversation histories for agent handoffs. This reduces token costs and prevents hallucination.

**Example:**
```python
class TaskState(TypedDict):
    task_description: str
    subtasks: list[dict]  # Structured, not natural language
    errors: list[str]
```

### Verification First

Any generated capability (MCP tool, skill, agent) should pass verification before being used. The specifics of verification are TBD but will include static analysis, behavioral testing, and adversarial probing.

### Track Metrics

Keep track of:
- Which capabilities get generated
- Which ones pass/fail verification
- How often capabilities are reused
- Token costs

This data feeds into the granularity optimizer and thesis evaluation.

## Notes for AI Agents

When working on this project:

1. **Use uv** - All Python package operations should use `uv`, not `pip` directly
2. **Trace with LangFuse** - Agent execution and LLM calls should be traced for debugging
3. **Ask before making architectural decisions** - Many design choices are still being explored
4. **Document decisions** - Update this file or project docs when important choices are made
5. **Work incrementally** - Build and test one component at a time

## Resources

- **LangGraph docs**: https://langchain-ai.github.io/langgraph/
- **uv docs**: https://docs.astral.sh/uv/
- **MCP specification**: https://modelcontextprotocol.io/
- **Agent Skills spec**: https://agentskills.io/
- **LangFuse docs**: https://langfuse.com/docs

---

**Last updated:** February 2026
