"""Unit tests for the fibonacci MCP tool logic."""

import pytest

from src.mcp_tools.fibonacci import fibonacci


@pytest.mark.asyncio
async def test_fibonacci_zero():
    assert await fibonacci(0) == 0


@pytest.mark.asyncio
async def test_fibonacci_one():
    assert await fibonacci(1) == 1


@pytest.mark.asyncio
async def test_fibonacci_ten():
    assert await fibonacci(10) == 55


@pytest.mark.asyncio
async def test_fibonacci_twenty():
    assert await fibonacci(20) == 6765
