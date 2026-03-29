"""Integration test for the full JIT capability pipeline (Pillar 1).

Runs a single task through the complete 9-node pipeline and verifies
every intermediate state: decomposition → gap detection → classification
→ generation → verification → loading → registration.

Uses T04 (pipe-delimited CSV parsing) as the test task — requires JIT
generation (no seeded capability matches) and produces a deterministically
verifiable output.

Usage::

    uv run scripts/test_pipeline.py
    uv run scripts/test_pipeline.py --verbose

Requires Docker running (for verification + execution).
Makes ~5-10 LLM calls (~$0.05). Takes ~2-5 minutes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

logger = logging.getLogger(__name__)

FIXTURE_PATH = ROOT / "tests" / "evaluation" / "fixtures" / "t04_pipe_delimited.csv"

TASK_PROMPT = (
    f"Parse the pipe-delimited file at {FIXTURE_PATH}. "
    "Auto-detect the delimiter. Return a dict with keys "
    "'row_count' (int, number of data rows excluding the header), "
    "'delimiter' (str), and 'parse_errors' (bool, True if any errors occurred)."
)

# Expected output for T04 fixture (seed=42, 30 data rows, pipe delimiter)
EXPECTED_ROW_COUNT = 30
EXPECTED_DELIMITER = "|"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Integration test for full JIT pipeline (Pillar 1)"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        stream=sys.stderr,
    )

    print("=" * 70)
    print("  PILLAR 1: Full Pipeline Integration Test")
    print("  Task: T04 (parse pipe-delimited CSV)")
    print("=" * 70)

    # --- Setup: fresh registry in temp dir ---
    from src.orchestrator.pipeline import create_pipeline
    from src.orchestrator.tool_loader import ToolLoader
    from src.registry.store import SkillRegistry, seed_registry
    from src.schemas.capability import CapabilityType
    from src.schemas.task import TaskState

    with tempfile.TemporaryDirectory() as tmp_dir:
        registry = SkillRegistry(persist_directory=tmp_dir, collection_name="test_pipeline")
        seed_registry(registry)
        loader = ToolLoader()

        initial_count = len(registry.list_all())
        print(f"\n  Registry: {initial_count} seeded capabilities")

        # --- Build and run pipeline ---
        pipeline = create_pipeline(registry, loader).compile()

        initial_state = TaskState(task_description=TASK_PROMPT)

        print(f"  Task: {TASK_PROMPT[:80]}...")
        print("\n  Running pipeline...", flush=True)

        try:
            result = pipeline.invoke(initial_state)
        except Exception as exc:
            print(f"\n  FAIL: Pipeline raised: {exc}")
            return 1

        # result is a dict from LangGraph
        _get = result.get if isinstance(result, dict) else lambda k, d=None: getattr(result, k, d)

        # --- Verify each stage ---
        all_pass = True

        # 1. Decomposition
        subtasks = _get("subtasks", []) or []
        print(f"\n  1. Decomposition: {len(subtasks)} subtask(s)")
        if not subtasks:
            print("     FAIL: No subtasks produced")
            all_pass = False
        else:
            for i, st in enumerate(subtasks):
                desc = getattr(st, "description", str(st))[:80]
                caps = getattr(st, "required_capabilities", [])
                print(f"     Subtask {i+1}: {desc}")
                print(f"       Required capabilities: {caps}")
            print("     OK")

        # 2. Gap detection
        matches = _get("matches", []) or []
        gaps = _get("gaps", []) or []
        print(f"\n  2. Gap detection: {len(matches)} match(es), {len(gaps)} gap(s)")
        if not gaps and not matches:
            print("     FAIL: Neither matches nor gaps detected")
            all_pass = False
        elif gaps:
            for g in gaps:
                desc = getattr(g, "description", str(g))
                print(f"     Gap: {desc}")
            print("     OK (gaps detected — JIT generation needed)")
        else:
            print("     OK (registry match found — no generation needed)")

        # 3. Classification
        classified = _get("classified_gaps", []) or []
        print(f"\n  3. Classification: {len(classified)} classified gap(s)")
        for g in classified:
            ctype = getattr(g, "classified_type", "?")
            conf = getattr(g, "confidence", "?")
            rationale = getattr(g, "rationale", "")[:80]
            print(f"     Type: {ctype} (confidence: {conf})")
            print(f"     Rationale: {rationale}")
        if classified:
            first_type = getattr(classified[0], "classified_type", None)
            if first_type == CapabilityType.MCP_TOOL:
                print("     OK (classified as MCP_TOOL — correct for T04)")
            else:
                print(f"     WARN: Expected MCP_TOOL, got {first_type}")
        elif gaps:
            print("     FAIL: Gaps exist but none were classified")
            all_pass = False
        else:
            print("     OK (no gaps to classify)")

        # 4. Generation
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

        # 5. Verification
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
        if artifacts and not verif_results:
            print("     WARN: Artifacts generated but no verification reports")
        elif verif_results:
            print("     OK")

        # 6. Loading + registration
        status = _get("status", "?")
        attempt_count = _get("attempt_count", 0)
        errors = _get("errors", []) or []
        print(f"\n  6. Loading: status={status}, attempts={attempt_count}, errors={len(errors)}")
        if errors:
            for e in errors:
                print(f"     Error: {e}")

        # Check registry grew
        final_count = len(registry.list_all())
        new_caps = final_count - initial_count
        print(f"     Registry: {initial_count} → {final_count} ({new_caps} new)")

        if artifacts and new_caps > 0:
            # Verify the new capability has source_code in metadata
            for a in artifacts:
                if getattr(a, "capability_type", None) == CapabilityType.MCP_TOOL:
                    cap = registry.get(a.capability_name)
                    if cap:
                        has_source = bool((cap.metadata or {}).get("source_code"))
                        print(f"     '{a.capability_name}' in registry: source_code={has_source}")
                        if not has_source:
                            print("     FAIL: Registered capability missing source_code in metadata")
                            all_pass = False
            print("     OK")
        elif gaps and new_caps == 0:
            print("     FAIL: Gaps existed but no new capabilities registered")
            all_pass = False
        else:
            print("     OK")

        # 7. Execution (sandbox)
        print(f"\n  7. Execution (sandbox):")
        artifact = artifacts[-1] if artifacts else None
        if artifact and getattr(artifact, "source_code", None):
            from src.verification.behavioral import _extract_function_name
            from src.verification.sandbox_tool import resolve_extra_packages, sandbox_execute

            source_code = artifact.source_code
            func_name = _extract_function_name(source_code) or "tool"

            # Read fixture and embed in sandbox
            fixture_content = FIXTURE_PATH.read_text()
            sandbox_code = f"""
import asyncio, json, inspect
__name__ = "not_main"
import pathlib
pathlib.Path("/tmp/fixture.csv").write_text({fixture_content!r})
{source_code}

async def _run():
    try:
        _sig = inspect.signature({func_name})
        _valid = set(_sig.parameters.keys())
        _inputs = {{"file_path": "/tmp/fixture.csv", "filepath": "/tmp/fixture.csv"}}
        _kwargs = {{k: v for k, v in _inputs.items() if k in _valid}}
        result = await {func_name}(**_kwargs)
        print(json.dumps({{"result": result}}))
    except Exception as e:
        print(json.dumps({{"error": str(e)}}))

asyncio.run(_run())
"""
            extra_pkgs = resolve_extra_packages(source_code)
            exec_timeout = 60 if extra_pkgs else 30
            result = sandbox_execute(sandbox_code, timeout=exec_timeout, extra_packages=extra_pkgs or None)

            if result.get("success") and result.get("stdout"):
                try:
                    parsed = json.loads(result["stdout"].strip().splitlines()[-1])
                    if "error" in parsed:
                        print(f"     Tool error: {parsed['error']}")
                        all_pass = False
                    else:
                        output = parsed.get("result", {})
                        print(f"     Output: {json.dumps(output, indent=2)}")

                        # Verify output values
                        row_count = output.get("row_count")
                        delimiter = output.get("delimiter")

                        if row_count == EXPECTED_ROW_COUNT:
                            print(f"     row_count={row_count} ✓")
                        else:
                            print(f"     row_count={row_count} ✗ (expected {EXPECTED_ROW_COUNT})")
                            all_pass = False

                        if delimiter == EXPECTED_DELIMITER:
                            print(f"     delimiter='{delimiter}' ✓")
                        else:
                            print(f"     delimiter='{delimiter}' ✗ (expected '{EXPECTED_DELIMITER}')")
                            all_pass = False

                        print("     OK" if all_pass else "     FAIL: Output mismatch")
                except (json.JSONDecodeError, IndexError) as exc:
                    print(f"     FAIL: Could not parse sandbox output: {exc}")
                    all_pass = False
            else:
                stderr = (result.get("stderr") or "")[:300]
                print(f"     FAIL: Sandbox execution failed: {stderr}")
                all_pass = False
        elif not gaps:
            print("     SKIP: Registry match path — no artifact to execute")
        else:
            print("     FAIL: No artifact with source_code available")
            all_pass = False

        # Cleanup
        loader.cleanup()

    # --- Summary ---
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    if all_pass:
        print("  PASS: All pipeline stages verified end-to-end")
    else:
        print("  FAIL: One or more stages failed")
    print()
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
