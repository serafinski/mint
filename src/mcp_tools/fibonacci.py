"""Hand-written Fibonacci MCP tool via FastMCP for Iteration 0."""

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
            description="Non-negative integer index into the Fibonacci sequence (e.g., 0, 1, 10)",
            ge=0,
            le=1000,
        ),
    ],
) -> int:
    """Compute the nth Fibonacci number (0-indexed).

    fibonacci(0) = 0, fibonacci(1) = 1, fibonacci(10) = 55.

    Args:
        n: Index into the Fibonacci sequence, 0 <= n <= 1000.

    Returns:
        The nth Fibonacci number.
    """
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


if __name__ == "__main__":
    mcp.run()
