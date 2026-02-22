"""Iteration 2 demo: full JIT pipeline with LangFuse tracing.

Shows two paths:
  PATH 1 — capability already in registry (fibonacci match, no generation needed)
  PATH 2 — gap detected, JIT generation triggered (Klingon translator)

Usage:
    uv run python demo.py
"""

from dotenv import load_dotenv
from langfuse import get_client
from langfuse.langchain import CallbackHandler
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from src.orchestrator.pipeline import create_pipeline
from src.orchestrator.tool_loader import ToolLoader
from src.registry.store import SkillRegistry, seed_registry
from src.schemas.task import TaskState


async def call_fibonacci_mcp(n: int) -> int:
    """Call the fibonacci MCP tool via stdio transport."""
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "src/mcp_tools/fibonacci.py"],
    )
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("fibonacci", {"n": n})
            return int(result.content[0].text)


async def main() -> None:
    load_dotenv()

    # --- Setup ---
    langfuse = get_client()
    langfuse_handler = CallbackHandler()
    registry = SkillRegistry()
    seed_registry(registry)
    loader = ToolLoader()
    graph = create_pipeline(registry, loader).compile()

    # --- Path 1: Capability found ---
    print("=" * 60)
    print("PATH 1: Capability found")
    print("=" * 60)
    result = graph.invoke(
        TaskState(task_description="compute fibonacci number"),
        config={"callbacks": [langfuse_handler]},
    )

    matches = result["matches"]
    print("Task: 'compute fibonacci number'")
    print(f"Matches: {len(matches)}")
    for m in matches:
        cap = m.capability
        print(f"  - {cap.name} (score: {m.score:.2f}, type: {cap.capability_type})")

    # Call the matched MCP tool
    fib_result = await call_fibonacci_mcp(10)
    print(f"MCP call fibonacci(10) = {fib_result}")

    # --- Path 2: Gap detected ---
    print()
    print("=" * 60)
    print("PATH 2: Gap detected")
    print("=" * 60)
    result = graph.invoke(
        TaskState(task_description="translate English to Klingon"),
        config={"callbacks": [langfuse_handler]},
    )

    gaps = result["gaps"]
    print("Task: 'translate English to Klingon'")
    print(f"Gaps: {len(gaps)}")
    for g in gaps:
        print(f"  - {g.description} (query: '{g.query}')")
    print()
    print("Gap logged — in later iterations this triggers JIT capability generation.")

    # --- Flush traces ---
    langfuse.flush()
    print()
    print("LangFuse traces flushed.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
