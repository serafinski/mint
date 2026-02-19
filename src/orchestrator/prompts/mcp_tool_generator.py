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
- Use only Python standard library imports plus `mcp` and `pydantic`
- Do NOT import external packages beyond `mcp` and `pydantic`
- Parameter types must be simple: str, int, float, bool, list[str], etc.
- Return type should be a simple type (str, int, float, bool, dict, list)
- Tool name should be hyphen-case in FastMCP() and snake_case for the function
- Include appropriate `annotations` for tool hints
- The tool must be self-contained — no dependencies on external state or files\
"""

MCP_TOOL_USER_PROMPT = """\
Generate a complete MCP tool implementation for the following capability:

Description: {description}
Search query: {query}
Task context: {task_context}

Generate a complete, working Python file following the FastMCP pattern shown \
in the system prompt. The tool must be async, use Annotated parameters with \
Field descriptions, and include a docstring.\
"""

MCP_TOOL_REPAIR_PROMPT = """\
The generated MCP tool has a validation error. Fix the issues and return \
the corrected version.

## Original Request

Description: {gap_description}
Search query: {gap_query}
Task context: {task_context}

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
