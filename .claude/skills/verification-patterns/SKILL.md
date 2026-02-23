---
name: verification-patterns
description: "AST scanning, behavioral testing, and adversarial probing patterns for the JIT verification gate (Pillar 2). Use this skill whenever implementing Tier 1 static scanner, Tier 2 behavioral testing, Tier 3 adversarial probing, or the verification gate in src/verification/. Also use when implementing the sandbox_execute tool for the adversarial DeepAgent, when wiring the retry loop into the pipeline (attempt_count / 3-strike system), or when a colleague mentions 'contamination detection', 'static scanner', 'behavioral testing', 'adversarial probing', 'verification gate', 'sandbox_execute', or 'strike system'."
---

# Verification Patterns

Pillar 2 is a three-tier gate that runs on every generated capability before it is registered. The tiers form a cost pyramid — cheap checks first, expensive checks last. **Never run Tier 2/3 on a capability that Tier 1 has condemned as Critical.**

## Tier Structure and Cost Gate

| Tier | Scope | Method | Cost | Gate condition |
|---|---|---|---|---|
| 0 | Skills only | `quick_validate.py` structural check | ~0 | Failure → REJECT, skip Tier 1/2/3 |
| 1 | All | AST + regex static scan | ~0 | Critical finding → REJECT immediately, skip Tier 2/3 |
| 2 | All (if Tier 1 passes) | Behavioral testing in sandbox | Moderate | Failure → REJECT, skip Tier 3 |
| 3 | All (if Tier 2 passes) | Adversarial DeepAgent | High | — |

The gate in `src/verification/gate.py` orchestrates this sequence and returns a `VerificationReport`.

---

## Tier 1: Static Scanner (`src/verification/static_scanner.py`)

Plain Python — **no LLM, no subprocess**. Uses `ast` and `re` only.

### Five Contamination Categories

| Category | Severity | Detection Pattern |
|---|---|---|
| Hardcoded data | CRITICAL | Literal lists/dicts of ≥3 items returned directly from a function body |
| Silent exception handling | HIGH | `except:` or `except Exception: pass` with no re-raise or logging |
| Mock/demo artifacts | CRITICAL | Variable names or string literals containing `mock`, `fake`, `sample`, `dummy`, `placeholder`, `DEMO`, `TEST_DATA` |
| Synthetic data indicators | HIGH | Comments or docstrings containing `# TODO`, `# FIXME`, `# placeholder`, `for testing only` |
| Missing external calls | MEDIUM | Tool claims to fetch/query/call external service but contains no `import requests`, `import httpx`, `import urllib`, `subprocess`, etc. |

### Implementation Pattern

```python
import ast
import re
from src.schemas.verification import Severity, VerificationFinding, VerificationReport

_MOCK_NAMES = re.compile(
    r'\b(mock|fake|sample|dummy|placeholder|DEMO|TEST_DATA)\b', re.IGNORECASE
)
_SYNTHETIC_COMMENTS = re.compile(
    r'(#\s*(TODO|FIXME|placeholder|for testing only))', re.IGNORECASE
)

def scan(source_code: str, capability_name: str) -> VerificationReport:
    findings: list[VerificationFinding] = []
    try:
        tree = ast.parse(source_code)
    except SyntaxError as exc:
        findings.append(VerificationFinding(
            tier=1,
            category="syntax",
            severity=Severity.CRITICAL,
            message=f"SyntaxError: {exc.msg} at line {exc.lineno}",
        ))
        return VerificationReport(
            capability_name=capability_name,
            findings=findings,
            overall_passed=False,
            tier_reached=1,
        )

    # Walk AST for contamination patterns
    for node in ast.walk(tree):
        _check_silent_except(node, findings)
        _check_hardcoded_return(node, findings)

    # Regex scan over raw source
    for m in _MOCK_NAMES.finditer(source_code):
        findings.append(VerificationFinding(
            tier=1, category="mock_artifact", severity=Severity.CRITICAL,
            message=f"Mock/demo indicator '{m.group()}' at position {m.start()}",
        ))
    for m in _SYNTHETIC_COMMENTS.finditer(source_code):
        findings.append(VerificationFinding(
            tier=1, category="synthetic_data", severity=Severity.HIGH,
            message=f"Synthetic data comment: {m.group().strip()}",
        ))

    has_critical = any(f.severity == Severity.CRITICAL for f in findings)
    return VerificationReport(
        capability_name=capability_name,
        findings=findings,
        overall_passed=not has_critical,
        tier_reached=1,
    )
```

### Cost Gate Check

```python
def should_proceed_to_tier2(report: VerificationReport) -> bool:
    """Tier 2 runs only if Tier 1 found no Critical findings."""
    return not any(f.severity == Severity.CRITICAL for f in report.findings)
```

---

## Tier 2: Behavioral Testing (`src/verification/behavioral.py`)

A plain LangGraph node — **no DeepAgent**. Generates edge-case inputs, executes in sandbox, checks outputs.

### Input Schema Extraction

Generated MCP tools have `input_schema={}` in the registry (empty by design). Tier 2 must extract the parameter schema from the source code:

```python
import ast
import inspect

def extract_parameter_schema(source_code: str) -> dict[str, dict]:
    """Extract parameter names and types from the tool's async function signature.

    Returns a dict of {param_name: {"type": type_str, "description": str}}.
    Parse from source_code AST — do not rely on CapabilityDescriptor.input_schema.
    """
    tree = ast.parse(source_code)
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if node.name.startswith("_"):
            continue
        schema = {}
        for arg in node.args.args:
            # Annotated[type, Field(...)] → extract the first annotation
            annotation = arg.annotation
            if isinstance(annotation, ast.Subscript):
                # Annotated[X, ...]  → first arg is the type
                if isinstance(annotation.slice, ast.Tuple):
                    type_node = annotation.slice.elts[0]
                    schema[arg.arg] = {"type": ast.unparse(type_node)}
                else:
                    schema[arg.arg] = {"type": ast.unparse(annotation.slice)}
            elif annotation:
                schema[arg.arg] = {"type": ast.unparse(annotation)}
            else:
                schema[arg.arg] = {"type": "unknown"}
        return schema
    return {}
```

### Four Test Categories

| Category | What to test | Example |
|---|---|---|
| Nonexistent entity | Input that references something that does not exist | `entity_id=""`, unknown file path |
| Consistency | Same input twice → same output | Call twice, compare results |
| Boundary | Min/max values, empty strings, zero, negative numbers | `n=0`, `n=1000`, `text=""` |
| Latency | Response time within reasonable bound | `time.time()` before/after, warn if >5s |

### Execution via ToolLoader

```python
from src.orchestrator.tool_loader import ToolLoader

def run_behavioral_tests(artifact, loader: ToolLoader) -> list[VerificationFinding]:
    loaded = loader.load_tool(artifact)
    findings = []

    # Boundary: empty inputs
    result = loader.execute_tool(loaded, inputs={})
    if not result.success and "TypeError" in (result.error or ""):
        # Missing required args → expected; not a finding
        pass

    # Consistency test with valid inputs
    schema = extract_parameter_schema(artifact.source_code)
    test_inputs = _generate_boundary_inputs(schema)
    r1 = loader.execute_tool(loaded, inputs=test_inputs)
    r2 = loader.execute_tool(loaded, inputs=test_inputs)
    if r1.result != r2.result:
        findings.append(VerificationFinding(
            tier=2, category="consistency", severity=Severity.HIGH,
            message="Tool returned different results for identical inputs",
        ))
    return findings
```

---

## Tier 3: Adversarial Probing (`src/verification/adversarial.py`)

Implemented as a **DeepAgent** invoked from a plain LangGraph node. Focus: three attack patterns.

### Three Attack Patterns

1. **Hidden demo/sample mode detection** — Does the tool behave differently when it detects test-like inputs? Send inputs containing "test", "sample", "demo" keywords vs. neutral inputs. Compare outputs.
2. **Network-disconnect behavioral change** — Does the tool silently return fabricated results when network is absent? Run the tool twice: once normally, once with network-disabled environment variables (`no_proxy=*`, `http_proxy=invalid`). Compare.
3. **Ground-truth comparison** — For tools with known correct outputs (fibonacci, hash, temperature conversion), compare the tool's output against a reference implementation.

### The `sandbox_execute` Tool (CRITICAL: must use Docker)

The adversarial DeepAgent needs to run arbitrary Python snippets. **This tool MUST use Docker isolation — never `sys.executable` directly.**

```python
import subprocess
import json
import tempfile
from pathlib import Path
from typing import Annotated
from pydantic import Field

SANDBOX_IMAGE = "jit-sandbox"  # Built from sandbox/Dockerfile

def sandbox_execute(
    code: Annotated[str, Field(description="Python code snippet to execute in the sandbox")],
    timeout: Annotated[int, Field(default=10, description="Timeout in seconds")] = 10,
) -> dict:
    """Execute a Python snippet in an isolated Docker container.

    Returns {"stdout": str, "stderr": str, "exit_code": int, "success": bool}.
    ALWAYS use this for adversarial probing — never subprocess on the host.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        script_path = f.name

    try:
        proc = subprocess.run(
            [
                "docker", "run", "--rm",
                "--network", "none",          # No network access
                "--memory", "128m",           # Memory limit
                "--cpus", "0.5",              # CPU limit
                "-v", f"{script_path}:/sandbox/script.py:ro",
                SANDBOX_IMAGE,
                "python", "/sandbox/script.py",
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
            "success": proc.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "Timeout", "exit_code": -1, "success": False}
    finally:
        Path(script_path).unlink(missing_ok=True)
```

**Why Docker, not `sys.executable`:** The adversarial agent deliberately runs hostile code to probe capabilities. Running on the host risks host compromise. `tool_loader.execute_tool` uses `sys.executable` for smoke-testing your own generated code — that's a different trust level from adversarial probing.

### Adversarial DeepAgent Integration

```python
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from src.schemas.verification import VerificationReport

ADVERSARIAL_RECURSION_LIMIT = 35
ADVERSARIAL_TIMEOUT_S = 180

def adversarial_probe_node(state, artifact) -> VerificationReport:
    """Invoke the adversarial DeepAgent on a generated artifact."""
    agent = create_deep_agent(
        model=init_chat_model("deepseek:deepseek-chat"),
        tools=[sandbox_execute],
        system_prompt=ADVERSARIAL_SYSTEM_PROMPT,
        response_format=AdversarialFindings,  # Pydantic model
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            agent.invoke,
            {"messages": [{"role": "user", "content": artifact.source_code}]},
            {"recursion_limit": ADVERSARIAL_RECURSION_LIMIT},
        )
        try:
            result = future.result(timeout=ADVERSARIAL_TIMEOUT_S)
        except TimeoutError:
            return _partial_report(artifact.capability_name, "Adversarial probing timed out")
    # Convert AdversarialFindings → VerificationReport
    ...
```

---

## Verification Gate (`src/verification/gate.py`)

```python
def verify(artifact, loader, registry) -> VerificationReport:
    # Tier 0 (Skills only)
    if artifact.capability_type == CapabilityType.SKILL:
        ok, msg = validate_skill_content(artifact.skill_content, artifact.capability_name)
        if not ok:
            return _critical_report(artifact.capability_name, msg, tier_reached=0)

    # Tier 1
    report = static_scanner.scan(artifact.source_code or "", artifact.capability_name)
    if not should_proceed_to_tier2(report):
        return report  # Critical → stop here

    # Tier 2
    tier2_findings = behavioral.run(artifact, loader)
    report.findings.extend(tier2_findings)
    report.tier_reached = 2
    if any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in tier2_findings):
        report.overall_passed = False
        return report

    # Tier 3
    tier3_report = adversarial.probe(artifact, loader)
    report.findings.extend(tier3_report.findings)
    report.tier_reached = 3
    report.overall_passed = not any(
        f.severity == Severity.CRITICAL for f in report.findings
    )
    return report
```

---

## Retry Loop: The 3-Strike System

`TaskState.attempt_count` tracks how many generation+verification cycles have run. The verification node increments it and routes backward on failure.

```python
MAX_ATTEMPTS = 3

def verification_node(state: TaskState, ...) -> Command:
    new_attempt_count = state.attempt_count + 1
    reports = []
    all_passed = True

    for artifact in state.generated_artifacts:
        report = gate.verify(artifact, loader, registry)
        reports.append(report)
        if not report.overall_passed:
            all_passed = False

    if all_passed or new_attempt_count >= MAX_ATTEMPTS:
        # Proceed to loading (pass or exhausted retries)
        goto = "loading_node"
    else:
        # Route back to generation with feedback injected into classified_gaps
        goto = "generate_mcp_tool"  # first generator in the chain

    return Command(
        update={
            "verification_results": reports,
            "attempt_count": new_attempt_count,
            "status": TaskStatus.VERIFYING,
            "errors": list(state.errors) + _collect_errors(reports),
        },
        goto=goto,
    )
```

### Injecting Feedback Into the Retry

Pass `VerificationReport` findings back to the generators. The generators' prompts should include:

```python
if state.verification_results:
    last_failures = [
        r for r in state.verification_results if not r.overall_passed
    ]
    feedback_section = "\n".join(
        f"Previous attempt failed: {r.capability_name}\n"
        + "\n".join(f"  - [{f.severity}] {f.message}" for f in r.findings)
        for r in last_failures
    )
else:
    feedback_section = "No previous attempts."
```

Include `feedback_section` in the user prompt as a `## Previous Verification Failures` section.

### Fail Open at Strike 3

If `attempt_count >= MAX_ATTEMPTS` and the capability still fails verification, proceed to `loading_node` with the failure recorded in `state.errors`. Do NOT loop forever. Log a LangFuse event.

---

## Pipeline Topology (Iteration 3)

```
START → decomposition_node → gap_detection → classifier_node
      → generate_mcp_tool → generate_skill → generate_agent
      → verification_node  ←──────────────────┘ (retry on failure, max 3)
      → loading_node → END
```

The verification node is inserted between the generator chain and loading_node. It routes forward on pass (or exhausted retries) and backward to `generate_mcp_tool` on failure.

---

## Key Constraints Summary

| Concern | Rule |
|---|---|
| Tier 1 Critical | Reject immediately. Never invoke Tier 2/3. |
| sandbox_execute | Always Docker (`--network none`). Never `sys.executable`. |
| input_schema | Empty in registry. Parse from `source_code` AST in Tier 2. |
| attempt_count | Increment in verification_node. Stop at 3. Fail open, not forever. |
| Adversarial DeepAgent model | `deepseek-chat` only. `deepseek-reasoner` lacks `tool_choice`. |
| Adversarial recursion_limit | 35. Timeout: 180s. Fresh agent per invocation. |
| Skills (Tier 0) | Run `quick_validate.py` before Tier 1. |
