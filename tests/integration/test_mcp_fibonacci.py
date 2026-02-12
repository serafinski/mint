"""Integration test: fibonacci MCP tool via stdio transport."""

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@pytest.mark.integration
async def test_fibonacci_mcp_call():
    """Start the MCP server as a subprocess, call fibonacci(10), verify result."""
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "src/mcp_tools/fibonacci.py"],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            result = await session.call_tool("fibonacci", {"n": 10})
            assert int(result.content[0].text) == 55


@pytest.mark.integration
async def test_fibonacci_mcp_list_tools():
    """Verify the server advertises the fibonacci tool."""
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "src/mcp_tools/fibonacci.py"],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            assert "fibonacci" in tool_names
