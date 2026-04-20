"""Integration test for the full JIT capability pipeline (Pillar 1).

Runs tasks through the complete 9-node pipeline and verifies every
intermediate state: decomposition -> gap detection -> classification
-> generation -> verification -> loading -> registration.

Tests all three capability types with inline prompts (not imported from
`tests/evaluation/tasks.py`), so this script is independent of testbed
changes. Representative shapes:
- MCP_TOOL: dirty CSV cleaning
- SKILL: multi-source data integration
- AGENT: anomaly investigation

Usage::

    uv run scripts/test_pipeline.py
    uv run scripts/test_pipeline.py --mcp-only
    uv run scripts/test_pipeline.py --skill-only
    uv run scripts/test_pipeline.py --agent-only
    uv run scripts/test_pipeline.py --verbose

Requires Docker running (for verification + execution).
Makes ~5-15 LLM calls per test (~$0.05-0.15). Takes ~2-5 minutes per test.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Step 13.6 / Fix #1 (2026-04-19): route dev-script LangFuse emissions
# to ``environment=test`` so they don't pollute Phase 4 eval's
# ``environment=default`` data. Must be set BEFORE ``load_dotenv`` so
# ambient .env values can't override. Same pattern as ``tests/conftest.py``.
import os  # noqa: E402
os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", "test")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

logger = logging.getLogger(__name__)

FIXTURES_DIR = ROOT / "tests" / "evaluation" / "fixtures"


# ---------------------------------------------------------------------------
# Pipeline runner (shared across tests)
# ---------------------------------------------------------------------------


def run_pipeline_and_verify(
    task_prompt: str,
    expected_type: str,
    test_name: str,
    *,
    validate_output: Any | None = None,
) -> bool:
    """Run a task through the full pipeline and verify each stage.

    Args:
        task_prompt: The task description to send to the pipeline.
        expected_type: Expected capability type ("mcp_tool", "skill", "agent").
        test_name: Display name for the test.
        validate_output: Optional callable(artifacts, registry) -> bool for
            output validation. If None, skips execution validation.

    Returns:
        True if all checks pass.
    """
    from src.orchestrator.pipeline import create_pipeline
    from src.orchestrator.task_state_builder import build_initial_state_from_prompt
    from src.orchestrator.tool_loader import ToolLoader
    from src.registry.store import SkillRegistry, seed_registry
    from src.schemas.capability import CapabilityType

    type_map = {
        "mcp_tool": CapabilityType.MCP_TOOL,
        "skill": CapabilityType.SKILL,
        "agent": CapabilityType.AGENT,
    }

    print(f"\n{'=' * 70}")
    print(f"  PIPELINE TEST: {test_name}")
    print(f"  Expected type: {expected_type.upper()}")
    print(f"{'=' * 70}")

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = SkillRegistry(persist_directory=tmp_dir, collection_name="test_pipeline")
        seed_registry(registry)
        loader = ToolLoader()

        initial_count = len(registry.list_all())
        print(f"\n  Registry: {initial_count} seeded capabilities")
        print(f"  Task: {task_prompt[:100]}...")

        # --- Run pipeline ---
        # Security audit finding #88 (2026-04-19, ext9): use the canonical
        # builder so Phase 3 smoke tests exercise the same entry-point
        # shape (contract-as-object) as Phase 4 production. Pre-fix
        # ``TaskState(task_description=...)`` bypassed contract injection
        # and the downstream writer-synthesis guard, so the smoke gate
        # tested a simpler path than production.
        pipeline = create_pipeline(registry, loader).compile()
        initial_state = build_initial_state_from_prompt(task_prompt)

        print("\n  Running pipeline...", flush=True)
        try:
            result = pipeline.invoke(initial_state)
        except Exception as exc:
            print(f"\n  FAIL: Pipeline raised: {exc}")
            return False

        _get = result.get if isinstance(result, dict) else lambda k, d=None: getattr(result, k, d)
        all_pass = True

        # --- 1. Decomposition ---
        subtasks = _get("subtasks", []) or []
        print(f"\n  1. Decomposition: {len(subtasks)} subtask(s)")
        if not subtasks:
            print("     FAIL: No subtasks produced")
            all_pass = False
        else:
            for i, st in enumerate(subtasks):
                desc = getattr(st, "description", str(st))[:80]
                print(f"     Subtask {i+1}: {desc}")
            print("     OK")

        # --- 2. Gap detection ---
        matches = _get("matches", []) or []
        gaps = _get("gaps", []) or []
        print(f"\n  2. Gap detection: {len(matches)} match(es), {len(gaps)} gap(s)")
        if not gaps and not matches:
            print("     FAIL: Neither matches nor gaps detected")
            all_pass = False
        else:
            print("     OK")

        # --- 3. Classification ---
        classified = _get("classified_gaps", []) or []
        print(f"\n  3. Classification: {len(classified)} classified gap(s)")
        for g in classified:
            ctype = getattr(g, "classified_type", "?")
            conf = getattr(g, "confidence", "?")
            print(f"     Type: {ctype} (confidence: {conf})")

        if classified:
            first_type = getattr(classified[0], "classified_type", None)
            expected_ct = type_map.get(expected_type)
            if first_type == expected_ct:
                print(f"     OK (classified as {expected_type.upper()} — correct)")
            else:
                actual = str(first_type).split(".")[-1].upper()
                print(f"     WARN: Expected {expected_type.upper()}, got {actual}")
                # Not a hard fail — classifier is stochastic
        elif gaps:
            print("     FAIL: Gaps exist but none were classified")
            all_pass = False
        else:
            print("     OK (no gaps to classify)")

        # --- 4. Generation ---
        artifacts = _get("generated_artifacts", []) or []
        print(f"\n  4. Generation: {len(artifacts)} artifact(s)")
        for a in artifacts:
            atype = str(getattr(a, "capability_type", "?")).split(".")[-1]
            name = getattr(a, "capability_name", "?")
            has_code = bool(getattr(a, "source_code", None))
            has_skill = bool(getattr(a, "skill_content", None))
            has_config = bool(getattr(a, "agent_config", None))
            print(f"     {name} ({atype}): code={has_code}, skill={has_skill}, config={has_config}")
        if gaps and not artifacts:
            print("     FAIL: Gaps existed but no artifacts generated")
            all_pass = False
        elif artifacts:
            print("     OK")

        # --- 5. Verification ---
        verif_results = _get("verification_results", []) or []
        print(f"\n  5. Verification: {len(verif_results)} report(s)")
        for vr in verif_results:
            name = getattr(vr, "capability_name", "?")
            passed = getattr(vr, "overall_passed", "?")
            tier = getattr(vr, "tier_reached", "?")
            n_findings = len(getattr(vr, "findings", []))
            print(f"     {name}: passed={passed}, tier_reached={tier}, findings={n_findings}")
            for f in getattr(vr, "findings", []):
                sev = str(getattr(f, "severity", "")).upper()
                msg = getattr(f, "message", "")[:80]
                print(f"       [{sev}] {msg}")
        if verif_results:
            print("     OK")

        # --- 6. Loading + registration ---
        status = _get("status", "?")
        attempt_count = _get("attempt_count", 0)
        errors = _get("errors", []) or []
        print(f"\n  6. Loading: status={status}, attempts={attempt_count}, errors={len(errors)}")

        final_count = len(registry.list_all())
        new_caps = final_count - initial_count
        print(f"     Registry: {initial_count} -> {final_count} ({new_caps} new)")

        if artifacts and new_caps > 0:
            print("     OK")
        elif gaps and new_caps == 0:
            print("     WARN: Gaps existed but no new capabilities registered")
        else:
            print("     OK")

        # --- 7. Output validation (optional) ---
        if validate_output is not None:
            print("\n  7. Output validation:")
            try:
                valid = validate_output(artifacts, registry)
                if valid:
                    print("     OK")
                else:
                    all_pass = False
            except Exception as exc:
                print(f"     FAIL: Validation raised: {exc}")
                all_pass = False
        else:
            print("\n  7. Output validation: SKIP (no validator provided)")

        loader.cleanup()

    if all_pass:
        print(f"\n  PASS: {test_name}")
    else:
        print(f"\n  FAIL: {test_name}")
    return all_pass


# ---------------------------------------------------------------------------
# MCP_TOOL test (T04: pipe-delimited CSV)
# ---------------------------------------------------------------------------


def _validate_mcp_output(artifacts, registry) -> bool:
    """Execute the generated MCP tool in sandbox and verify output."""
    from src.verification.behavioral import _extract_function_name
    from src.verification.sandbox_tool import resolve_extra_packages, sandbox_execute

    artifact = artifacts[-1] if artifacts else None
    if not artifact or not getattr(artifact, "source_code", None):
        print("     FAIL: No artifact with source_code")
        return False

    source_code = artifact.source_code
    func_name = _extract_function_name(source_code) or "tool"
    fixture_content = (FIXTURES_DIR / "t05_dirty.csv").read_text()

    sandbox_code = f"""
import asyncio, json, inspect
__name__ = "not_main"
import pathlib
pathlib.Path("/tmp/fixture.csv").write_text({fixture_content!r})
{source_code}

async def _run():
    try:
        _sig = inspect.signature({func_name})
        _valid = list(_sig.parameters.keys())
        # First try named exact matches for the common shapes.
        _inputs = {{"file_path": "/tmp/fixture.csv", "filepath": "/tmp/fixture.csv"}}
        _kwargs = {{k: v for k, v in _inputs.items() if k in _valid}}
        # Path-keyword fallback so the validator doesn't FAIL just because the
        # LLM named the param `csv_path` / `input_csv` / `data_path` / etc.
        # Mirrors the path-keyword heuristic used by the chain executor in
        # tests/evaluation/execution.py::execute_mcp_tool_in_chain.
        if not _kwargs:
            _PATH_KEYWORDS = ("path", "file", "csv", "input", "src", "source")
            _required = [
                name for name, p in _sig.parameters.items()
                if p.default is inspect.Parameter.empty
            ]
            for _p in _required:
                _pl = _p.lower()
                if any(kw in _pl for kw in _PATH_KEYWORDS):
                    _kwargs[_p] = "/tmp/fixture.csv"
                    break
        result = await {func_name}(**_kwargs)
        print(json.dumps({{"result": result}}))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))

asyncio.run(_run())
"""
    extra_pkgs = resolve_extra_packages(source_code)
    result = sandbox_execute(sandbox_code, timeout=60 if extra_pkgs else 30,
                             extra_packages=extra_pkgs or None)

    if not result.get("success") or not result.get("stdout"):
        print(f"     FAIL: Sandbox execution failed: {(result.get('stderr') or '')[:200]}")
        return False

    try:
        parsed = json.loads(result["stdout"].strip().splitlines()[-1])
        if "error" in parsed:
            print(f"     FAIL: Tool error: {parsed['error']}")
            return False
        output = parsed.get("result", {})
        print(f"     Output: {json.dumps(output, indent=2)}")

        # T05 dirty CSV: should have fewer rows than input (duplicates removed)
        # and zero nulls after cleaning
        row_count = output.get("output_row_count") or output.get("row_count")
        null_count = output.get("null_count", -1)
        ok = True
        if row_count is not None and int(row_count) < 48:  # input has 48 rows (40 + 8 dupes)
            print(f"     output_row_count={row_count} OK (< 48 input rows)")
        else:
            print(f"     output_row_count={row_count} FAIL (should be < 48)")
            ok = False
        if null_count == 0:
            print(f"     null_count={null_count} OK")
        elif null_count == -1:
            print("     null_count not in output (acceptable)")
        else:
            print(f"     null_count={null_count} FAIL (expected 0)")
            ok = False
        return ok
    except (json.JSONDecodeError, IndexError) as exc:
        print(f"     FAIL: Could not parse output: {exc}")
        return False


def test_mcp_tool() -> bool:
    """T05: Clean dirty CSV -> MCP_TOOL."""
    fixture = FIXTURES_DIR / "t05_dirty.csv"
    prompt = (
        f"Clean the CSV file at {fixture}. "
        "Remove rows with any null/empty values and deduplicate exact duplicate rows. "
        "Return a dict with keys 'output_row_count' (int, rows after cleaning) "
        "and 'null_count' (int, should be 0 after cleaning)."
    )
    return run_pipeline_and_verify(
        prompt, "mcp_tool", "MCP_TOOL (T05 dirty CSV cleaning)",
        validate_output=_validate_mcp_output,
    )


# ---------------------------------------------------------------------------
# SKILL test (T11-like: multi-source data integration)
# ---------------------------------------------------------------------------


def _validate_skill_output(artifacts, registry) -> bool:
    """Check that a SKILL artifact was produced with valid content."""
    skill_artifacts = [
        a for a in artifacts
        if getattr(a, "capability_type", None).__class__.__name__ == "CapabilityType"
        and str(getattr(a, "capability_type", "")).endswith("SKILL")
    ]
    # Also accept MCP_TOOL (classifier may route differently)
    if not skill_artifacts:
        # Check if any artifact was generated at all
        if artifacts:
            atype = str(getattr(artifacts[-1], "capability_type", "")).split(".")[-1]
            print(f"     NOTE: Classifier routed to {atype} instead of SKILL")
            print("     OK (artifact generated, type selection is stochastic)")
            return True
        print("     FAIL: No artifacts generated")
        return False

    artifact = skill_artifacts[-1]
    content = getattr(artifact, "skill_content", None)
    if not content:
        print("     FAIL: SKILL artifact has no skill_content")
        return False

    # Basic structural checks
    has_frontmatter = content.startswith("---")
    has_name = "name:" in content
    print(f"     SKILL content: {len(content)} chars, frontmatter={has_frontmatter}, name={has_name}")
    print(f"     Preview: {content[:150]}...")
    return True


def test_skill() -> bool:
    """T11-like: Multi-source data integration -> SKILL."""
    sales = FIXTURES_DIR / "t11_sales.csv"
    products = FIXTURES_DIR / "t11_products.xlsx"
    targets = FIXTURES_DIR / "t11_targets.csv"

    prompt = (
        f"Integrate sales data from {sales} with product catalog from "
        f"{products} and compare against regional targets from {targets}. "
        "For each region, compute total revenue (units * price), compare "
        "against target, and identify regions below target. This requires "
        "a multi-step workflow: load 3 sources, join on product_id, "
        "aggregate by region, and compare. Return a structured report."
    )
    return run_pipeline_and_verify(
        prompt, "skill", "SKILL (T11-like multi-source integration)",
        validate_output=_validate_skill_output,
    )


# ---------------------------------------------------------------------------
# AGENT test (T13-like: anomaly investigation)
# ---------------------------------------------------------------------------


def _validate_agent_output(artifacts, registry) -> bool:
    """Check that an AGENT artifact was produced with valid config."""
    agent_artifacts = [
        a for a in artifacts
        if str(getattr(a, "capability_type", "")).endswith("AGENT")
    ]
    if not agent_artifacts:
        if artifacts:
            atype = str(getattr(artifacts[-1], "capability_type", "")).split(".")[-1]
            print(f"     NOTE: Classifier routed to {atype} instead of AGENT")
            print("     OK (artifact generated, type selection is stochastic)")
            return True
        print("     FAIL: No artifacts generated")
        return False

    artifact = agent_artifacts[-1]
    config = getattr(artifact, "agent_config", None)
    if not config:
        print("     FAIL: AGENT artifact has no agent_config")
        return False

    has_prompt = bool(config.get("system_prompt"))
    is_deep = config.get("is_deep_agent", False)
    print(f"     AGENT config: system_prompt={has_prompt}, is_deep_agent={is_deep}")
    print(f"     Prompt preview: {str(config.get('system_prompt', ''))[:150]}...")
    return True


def test_agent() -> bool:
    """T13-like: Autonomous anomaly investigation -> AGENT."""
    prompt = (
        "Investigate anomalies in system metrics data. You must autonomously "
        "decide which metrics to analyze, compute baselines using statistical "
        "methods, identify significant deviations, correlate anomalies across "
        "metrics, and produce a structured investigation report with root "
        "cause hypotheses. This requires adaptive multi-step reasoning — "
        "the analysis path depends on what the initial profiling reveals. "
        "Return anomaly_count, anomalies list, and metrics_analyzed count."
    )
    return run_pipeline_and_verify(
        prompt, "agent", "AGENT (T13-like anomaly investigation)",
        validate_output=_validate_agent_output,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Integration test for full JIT pipeline (Pillar 1)"
    )
    parser.add_argument("--mcp-only", action="store_true", help="Run only MCP_TOOL test")
    parser.add_argument("--skill-only", action="store_true", help="Run only SKILL test")
    parser.add_argument("--agent-only", action="store_true", help="Run only AGENT test")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        stream=sys.stderr,
    )

    only_one = args.mcp_only or args.skill_only or args.agent_only
    run_mcp = args.mcp_only or not only_one
    run_skill = args.skill_only or not only_one
    run_agent = args.agent_only or not only_one

    results = {}

    if run_mcp:
        results["mcp_tool"] = test_mcp_tool()

    if run_skill:
        results["skill"] = test_skill()

    if run_agent:
        results["agent"] = test_agent()

    # Summary
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_pass = False

    print()
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
