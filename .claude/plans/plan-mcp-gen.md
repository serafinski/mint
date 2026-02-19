# Plan: MCP Tool Generator + Tool Loader

## Overview

Implement two modules for Iteration 2 Phase B:
1. `src/orchestrator/generators/mcp_tool.py` — LangGraph node that generates FastMCP tools via LLM
2. `src/orchestrator/tool_loader.py` — Dynamic tool loading and sandboxed execution via importlib

Plus unit tests and integration tests.

## File 1: `src/orchestrator/generators/mcp_tool.py`

### Structure (follows `classifier.py` pattern)

- **Lazy LLM singleton** (`_get_llm()`) using `init_chat_model("deepseek:deepseek-chat")`
- **Pydantic response model** `GeneratedToolCode` with fields: `tool_name: str`, `source_code: str`, `description: str`
- **Prompt** in `src/orchestrator/prompts/mcp_tool_generator.py`:
  - System prompt explaining FastMCP conventions
  - Few-shot example based on `fibonacci.py` (complete working tool)
  - User prompt with gap description/query
- **`generate_single_tool(gap: ClassifiedGap, task_context: str) -> GeneratedArtifact | None`**:
  1. Build prompt from gap description
  2. Call LLM with `with_structured_output(GeneratedToolCode)`
  3. Validate: `compile(source_code, "<generated>", "exec")` to check syntax
  4. **Repair pass**: if compile fails, send code + error back to LLM once
  5. If still invalid, log to LangFuse, return None
  6. Return `GeneratedArtifact(capability_name=..., capability_type=MCP_TOOL, source_code=...)`
- **`generate_mcp_tool(state: TaskState) -> Command`** (LangGraph node):
  1. Filter `state.classified_gaps` for `CapabilityType.MCP_TOOL`
  2. Call `generate_single_tool()` for each
  3. Collect artifacts and errors
  4. Return `Command(update={"generated_artifacts": [...], "errors": [...], "status": TaskStatus.GENERATING}, goto="__end__")`
  5. Uses `goto="__end__"` for now — Phase C will wire to verification

### LangFuse Integration
- Wrap each LLM call with `LangFuseCallbackHandler()` (same pattern as classifier.py)
- Log failures/repair attempts

## File 2: `src/orchestrator/prompts/mcp_tool_generator.py`

- `MCP_TOOL_SYSTEM_PROMPT` — instructions for generating FastMCP tools
- `MCP_TOOL_USER_PROMPT` — template with `{description}`, `{query}`, `{task_context}` placeholders
- `MCP_TOOL_REPAIR_PROMPT` — template with `{source_code}`, `{error}` placeholders
- Few-shot example embedded in system prompt (complete fibonacci-style tool)

## File 3: `src/orchestrator/tool_loader.py`

### `ToolLoader` class

- **`load_tool(artifact: GeneratedArtifact) -> LoadedTool`**:
  - Validate `artifact.source_code` is not None and `capability_type == MCP_TOOL`
  - Write source to `tempfile.NamedTemporaryFile` in a temp directory
  - Use `importlib.util.spec_from_file_location` + `module_from_spec` + `loader.exec_module` to load
  - Find the tool function: scan module for functions decorated with `@mcp.tool()` (look for a function that isn't `__init__` and is async)
  - Return a `LoadedTool` dataclass with `module`, `tool_fn`, `temp_path`

- **`execute_tool(tool_fn: callable, inputs: dict) -> ToolResult`**:
  - Run in subprocess for sandboxing:
    - Write a runner script to temp file that imports the module, calls `asyncio.run(tool_fn(**inputs))`, prints JSON result
    - `subprocess.run()` with timeout, capture stdout/stderr
  - Return `ToolResult(success=bool, result=any, error=str|None, stdout=str, stderr=str)`

- **`cleanup()`** — remove temp files. Also register with `atexit`.

### Helper types (in same file, private)
- `LoadedTool` — dataclass: `module`, `tool_fn`, `temp_path`
- `ToolResult` — Pydantic model: `success`, `result`, `error`, `stdout`, `stderr`

## File 4: `tests/unit/test_mcp_tool_generator.py`

Unit tests (mock LLM):
1. **TestGeneratedToolCodeSchema** — validate the response model
2. **TestGenerateSingleTool**:
   - Successful generation with valid Python code
   - Repair pass triggered when code has syntax error, then succeeds
   - Repair pass fails -> returns None
   - LLM call failure -> returns None with error logged
3. **TestGenerateMcpToolNode**:
   - Filters only MCP_TOOL gaps (skips SKILL/AGENT)
   - Empty gaps -> empty artifacts
   - Mixed success/failure -> partial artifacts + errors
   - Command has correct update keys and goto

## File 5: `tests/unit/test_tool_loader.py`

Unit tests (no LLM, real importlib):
1. **TestLoadTool**:
   - Load a simple generated tool (hand-crafted valid FastMCP code)
   - Reject artifact with no source_code
   - Reject non-MCP_TOOL artifact
2. **TestExecuteTool**:
   - Execute a loaded tool with valid inputs
   - Handle tool that raises exception
   - Handle timeout
3. **TestCleanup**:
   - Temp files are removed after cleanup

## File 6: `tests/integration/test_mcp_tool_generator.py`

Integration tests (real LLM, marked `@pytest.mark.integration`, skip without API key):
1. Generate "add two numbers" tool
2. Verify code compiles
3. Load via ToolLoader
4. Execute with test inputs, verify output

## Execution Order

1. Create prompt file `src/orchestrator/prompts/mcp_tool_generator.py`
2. Create generator `src/orchestrator/generators/mcp_tool.py`
3. Create loader `src/orchestrator/tool_loader.py`
4. Create unit tests
5. Create integration tests
6. Run `uv run pytest tests/unit/ -x` to verify
