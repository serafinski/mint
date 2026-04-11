"""Prompts for the MCP tool generator."""

MCP_TOOL_SYSTEM_PROMPT = """\
You are an expert Python developer specializing in MCP (Model Context Protocol)
tool generation. You generate complete, working FastMCP tool implementations.

## Requirements

Every generated tool MUST follow this exact pattern:
1. Import `FastMCP` from `mcp.server.fastmcp` and `Annotated` from `typing`
2. Import `Field` from `pydantic`
3. Create an `mcp` instance: `mcp = FastMCP("tool-name")`
4. Define ONE async function decorated with `@mcp.tool()`
5. Use `Annotated[type, Field(description=...)]` for ALL parameters
6. Include a clear docstring
7. Include `if __name__ == "__main__": mcp.run()` at the end

## Complete Example

Here is a complete, valid MCP tool that computes a Fibonacci number:

```python
\"\"\"MCP tool that computes Fibonacci numbers.\"\"\"

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

mcp = FastMCP("fibonacci_mcp")


@mcp.tool(
    name="fibonacci",
    annotations={
        "title": "Compute Fibonacci Number",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def fibonacci(
    n: Annotated[
        int,
        Field(
            description="Non-negative integer index into the Fibonacci sequence",
            ge=0,
            le=1000,
        ),
    ],
) -> int:
    \"\"\"Compute the nth Fibonacci number (0-indexed).

    fibonacci(0) = 0, fibonacci(1) = 1, fibonacci(10) = 55.

    Args:
        n: Index into the Fibonacci sequence, 0 <= n <= 1000.

    Returns:
        The nth Fibonacci number.
    \"\"\"
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


if __name__ == "__main__":
    mcp.run()
```

## Rules

- The tool function MUST be async (use `async def`)
- You may import ANY PyPI package — the sandbox resolves and installs \
dependencies automatically. Use the best library for the job (pandas, \
scikit-learn, scipy, requests, beautifulsoup4, etc.)
- Parameter types must be simple: str, int, float, bool, list[str], etc.
- Return type should be a simple type (str, int, float, bool, dict, list)
- Tool name should be snake_case for both FastMCP() and the function
- Include appropriate `annotations` for tool hints
- The tool must be self-contained — no dependencies on external state or files
- All non-file parameters (thresholds, limits, flags, etc.) MUST have sensible \
default values so the tool can be called with just the data inputs. Only file \
path parameters should be required (no default).
- In the `pip_packages` field, list ALL non-stdlib PyPI packages your code \
imports. Use the **pip install name** (what you'd pass to `pip install`), not \
the Python import name — e.g. `pyyaml` not `yaml`. If you only use \
stdlib + mcp + pydantic, leave pip_packages empty.
- **Do NOT catch exceptions and return them as structured "error result" \
dicts.** If something goes wrong (file missing, JSON parse failure, schema \
mismatch, division by zero), let the exception propagate so the calling \
pipeline reports it as an execution error. Returning \
``{"summary": "Failed to parse: ...", "data": []}`` instead of raising looks \
like a successful result to the caller and silently hides the failure. The \
*only* time you may catch an exception is to add diagnostic context, and \
in that case you MUST re-raise it (e.g. \
``raise RuntimeError(f"Failed to parse {path}: {original_exc}") from original_exc``). \
The pipeline's verification gate, retry loop, and CLI error display all \
depend on real exceptions reaching them. Returning a soft-failure dict \
defeats every layer of error handling above the tool.\
"""

MCP_TOOL_USER_PROMPT = """\
Generate a complete MCP tool implementation that performs ONE STEP of a \
larger workflow. Classification has decided this specific step is best \
implemented as an MCP tool (a single deterministic function).

## Full Task Context (for reference only)

This is the overall task the step belongs to. Other steps of this task are \
handled by separate artifacts — your tool only implements the single step \
described below, not the whole task.

{task_context}

## The Specific Step to Implement

Your tool implements this step of the workflow:

    {gap_description}

Focus on exactly this step. Do NOT try to solve the whole task in one tool. \
If the workflow has other steps (parsing, aggregation, reporting, etc.) \
those are handled by separate tools — you must NOT duplicate their work.

## CRITICAL: Do Not Build a Monolith

Your tool must produce ONLY the output described in the step above. \
It must NOT compute or return fields that belong to later steps in the \
workflow. The decomposer split this task into separate steps for a reason \
— each tool does its own part and passes intermediate data forward.

**Bad example (MONOLITH — DO NOT DO THIS):**
Step says: "Parse the three input files into structured tables"
Tool returns: {{"summary_row_count": 3, "region_revenues": {{"North": 20000}}, "regions_meeting_target": 1}}
This is WRONG — the tool jumped ahead and computed the final answer. \
Parsing should return the parsed tables, not the aggregated results.

**Good example (STEP-SPECIFIC):**
Step says: "Parse the three input files into structured tables"
Tool returns: {{"sales": [...rows...], "products": [...rows...], "targets": [...rows...]}}
This is CORRECT — the tool returns only what the step asks for.

If you find yourself computing something that a later step is supposed \
to do, STOP. Return only the intermediate data this step produces.

Accept the inputs relevant to this step (typically file paths plus any \
step-specific parameters) and return the step's output in one call. If the \
step produces intermediate data that another step will consume, return it \
as a plain dict/list that the caller can forward.

The tool function MUST be named exactly `{forced_name}` (snake_case). \
Do not choose a different name.

Generate a complete, working Python file following the FastMCP pattern shown \
in the system prompt.\
"""

MCP_TOOL_REPAIR_PROMPT = """\
The generated MCP tool has a validation error. Fix the issues and return \
the corrected version.

## Full Task Context (reference only)

{task_context}

## The Specific Step to Implement

{gap_description}

## Your Previous Output

- tool_name: {tool_name}
- description: {description}
- source_code:
```python
{source_code}
```

## Validation Error

{error}

Return the corrected tool_name, source_code, and description. Ensure:
- tool_name is valid snake_case (lowercase letters, digits, underscores only; \
no spaces, hyphens, or uppercase)
- source_code is a complete Python file following the FastMCP pattern: imports, \
FastMCP instance, @mcp.tool() decorated async function, ALL parameters typed as \
Annotated[type, Field(description="...")] from pydantic, and \
if __name__ == "__main__": mcp.run() at the end\
"""
