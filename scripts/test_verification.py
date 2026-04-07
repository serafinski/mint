"""Integration test for the three-tier verification gate (Pillar 2).

Runs real code through all three verification tiers with a real Docker
sandbox and real adversarial DeepAgent. Tests both a known-good tool
(fibonacci) and a known-bad tool (hardcoded return values).

Usage::

    # Run all tests
    uv run scripts/test_verification.py

    # Run only specific test
    uv run scripts/test_verification.py --good-only
    uv run scripts/test_verification.py --bad-only
    uv run scripts/test_verification.py --skill-only

    # Verbose logging
    uv run scripts/test_verification.py --verbose

Requires Docker running (for Tier 2 + Tier 3 sandbox execution).
Makes 1-2 LLM calls per test for adversarial probing (~$0.02 total).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from src.schemas.capability import CapabilityType  # noqa: E402
from src.schemas.task import GeneratedArtifact  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Test artifacts
# ---------------------------------------------------------------------------

GOOD_MCP_TOOL = GeneratedArtifact(
    capability_name="fibonacci-test",
    capability_type=CapabilityType.MCP_TOOL,
    description="Compute the nth Fibonacci number",
    source_code=(
        'from typing import Annotated\n'
        'from mcp.server.fastmcp import FastMCP\n'
        'mcp = FastMCP("fibonacci")\n'
        '@mcp.tool()\n'
        'async def fibonacci(n: Annotated[int, "Non-negative integer"]) -> int:\n'
        '    """Compute the nth Fibonacci number (0-indexed)."""\n'
        '    a, b = 0, 1\n'
        '    for _ in range(n):\n'
        '        a, b = b, a + b\n'
        '    return a\n'
        'if __name__ == "__main__":\n'
        '    mcp.run()\n'
    ),
)

BAD_MCP_TOOL = GeneratedArtifact(
    capability_name="fake-data-tool",
    capability_type=CapabilityType.MCP_TOOL,
    description="Fetch and process data",
    source_code=(
        'from typing import Annotated\n'
        'from mcp.server.fastmcp import FastMCP\n'
        'mcp = FastMCP("fake-data")\n'
        '@mcp.tool()\n'
        'async def fetch_data(query: Annotated[str, "Search query"]) -> dict:\n'
        '    """Fetch data from external API."""\n'
        '    # Hardcoded return — should be caught by Tier 1 static scanner\n'
        '    return {\n'
        '        "id": 1, "name": "Alice", "score": 95,\n'
        '        "id2": 2, "name2": "Bob", "score2": 88,\n'
        '        "id3": 3, "name3": "Charlie", "score3": 72,\n'
        '    }\n'
        'if __name__ == "__main__":\n'
        '    mcp.run()\n'
    ),
)

GOOD_AGENT = GeneratedArtifact(
    capability_name="anomaly-investigator",
    capability_type=CapabilityType.AGENT,
    description="Autonomous agent that investigates anomalies in metrics data",
    agent_config={
        "system_prompt": (
            "You are an autonomous data analysis agent. You receive metrics "
            "data and must investigate anomalies by computing baselines, "
            "identifying outliers, and producing a structured findings report. "
            "Use sandbox_execute to run Python code for any computation."
        ),
        "is_deep_agent": True,
        "model": "openai:gpt-5-mini",
    },
)

BAD_SKILL = GeneratedArtifact(
    capability_name="bad--skill--name",
    capability_type=CapabilityType.SKILL,
    description="A skill with invalid naming",
    skill_content=(
        '---\n'
        'name: bad--skill--name\n'
        'description: "This skill has consecutive hyphens in the name"\n'
        '---\n'
        '\n'
        '# Bad Skill\n'
        '\n'
        'This skill has an invalid name with consecutive hyphens.\n'
    ),
)

GOOD_SKILL = GeneratedArtifact(
    capability_name="data-cleaning-steps",
    capability_type=CapabilityType.SKILL,
    description="Multi-step data cleaning workflow",
    skill_content=(
        '---\n'
        'name: data-cleaning-steps\n'
        'description: "Step-by-step workflow for cleaning messy CSV datasets"\n'
        '---\n'
        '\n'
        '# Data Cleaning Steps\n'
        '\n'
        '## Step 1: Load and Inspect\n'
        'Load the CSV file using pandas. Check shape, dtypes, and null counts.\n'
        '\n'
        '## Step 2: Handle Missing Values\n'
        'For numeric columns, impute with median. For categorical, impute with mode.\n'
        '\n'
        '## Step 3: Remove Duplicates\n'
        'Drop exact duplicate rows. Log how many were removed.\n'
        '\n'
        '## Step 4: Validate and Return\n'
        'Assert no nulls remain. Return cleaned row count and column list.\n'
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_findings(report) -> list[str]:
    """Format verification findings for display."""
    lines = []
    tier_names = {0: "Tier 0 (structural)", 1: "Tier 1 (static)", 2: "Tier 2 (behavioral)", 3: "Tier 3 (adversarial)"}
    for f in report.findings:
        sev = str(f.severity).upper()
        tier = tier_names.get(f.tier, f"Tier {f.tier}")
        loc = f" ({f.location})" if f.location else ""
        lines.append(f"    [{sev}] {tier}: {f.message}{loc}")
    return lines


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_good_mcp_tool() -> bool:
    """Test: a legitimate fibonacci tool should pass all three tiers."""
    from src.verification.gate import verify

    print("\n" + "=" * 70)
    print("  TEST: Good MCP Tool (fibonacci)")
    print("  Expected: PASS all tiers")
    print("=" * 70)

    report = verify(GOOD_MCP_TOOL)

    print(f"\n  Result: {'PASSED' if report.overall_passed else 'FAILED'}")
    print(f"  Tier reached: {report.tier_reached}")
    print(f"  Findings ({len(report.findings)}):")
    for line in _format_findings(report):
        print(line)

    if not report.overall_passed:
        print("\n  FAIL: Good tool should pass verification")
        return False

    if report.tier_reached < 3:
        print(f"\n  FAIL: Expected tier_reached=3, got {report.tier_reached} (not all tiers ran)")
        return False

    print("\n  PASS: Legitimate tool passed all three tiers")
    return True


def test_bad_mcp_tool() -> bool:
    """Test: a tool with hardcoded return values should be rejected at Tier 1."""
    from src.verification.gate import verify

    print("\n" + "=" * 70)
    print("  TEST: Bad MCP Tool (hardcoded data)")
    print("  Expected: FAIL at Tier 1 (static scanner)")
    print("=" * 70)

    report = verify(BAD_MCP_TOOL)

    print(f"\n  Result: {'PASSED' if report.overall_passed else 'FAILED'}")
    print(f"  Tier reached: {report.tier_reached}")
    print(f"  Findings ({len(report.findings)}):")
    for line in _format_findings(report):
        print(line)

    if report.overall_passed:
        print("\n  FAIL: Bad tool should NOT pass verification")
        return False

    # Should be caught by Tier 1 static scanner (hardcoded dict)
    has_critical = any(
        str(f.severity).lower() == "critical"
        for f in report.findings
    )
    if not has_critical:
        print("\n  FAIL: Expected CRITICAL finding for hardcoded return values")
        return False

    if report.tier_reached > 1:
        print(f"\n  WARN: Tier reached={report.tier_reached} — fail-fast should have stopped at Tier 1")
        # Not a hard failure — the gate might have run Tier 2 anyway

    print("\n  PASS: Bad tool correctly rejected (hardcoded data detected)")
    return True


def test_good_skill() -> bool:
    """Test: a well-formed SKILL.md should pass Tier 0 + Tier 1."""
    from src.verification.gate import verify

    print("\n" + "=" * 70)
    print("  TEST: Good SKILL (data-cleaning-steps)")
    print("  Expected: PASS Tier 0 (structural) + Tier 1 (regex)")
    print("=" * 70)

    report = verify(GOOD_SKILL)

    print(f"\n  Result: {'PASSED' if report.overall_passed else 'FAILED'}")
    print(f"  Tier reached: {report.tier_reached}")
    print(f"  Findings ({len(report.findings)}):")
    for line in _format_findings(report):
        print(line)

    if not report.overall_passed:
        print("\n  FAIL: Well-formed skill should pass verification")
        return False

    print("\n  PASS: Skill passed structural and static checks")
    return True


def test_bad_skill() -> bool:
    """Test: a SKILL with invalid name (consecutive hyphens) should be rejected at Tier 0."""
    from src.verification.gate import verify

    print("\n" + "=" * 70)
    print("  TEST: Bad SKILL (consecutive hyphens in name)")
    print("  Expected: FAIL at Tier 0 (structural pre-check)")
    print("=" * 70)

    report = verify(BAD_SKILL)

    print(f"\n  Result: {'PASSED' if report.overall_passed else 'FAILED'}")
    print(f"  Tier reached: {report.tier_reached}")
    print(f"  Findings ({len(report.findings)}):")
    for line in _format_findings(report):
        print(line)

    if report.overall_passed:
        print("\n  FAIL: Bad skill should NOT pass verification")
        return False

    has_structural = any(f.tier == 0 for f in report.findings)
    if not has_structural:
        print("\n  FAIL: Expected Tier 0 structural finding")
        return False

    print("\n  PASS: Bad skill correctly rejected at Tier 0")
    return True


def test_good_agent() -> bool:
    """Test: a well-formed AGENT config should pass verification."""
    from src.verification.gate import verify

    print("\n" + "=" * 70)
    print("  TEST: Good AGENT (anomaly-investigator)")
    print("  Expected: PASS (regex-only scan on serialised config)")
    print("=" * 70)

    report = verify(GOOD_AGENT)

    print(f"\n  Result: {'PASSED' if report.overall_passed else 'FAILED'}")
    print(f"  Tier reached: {report.tier_reached}")
    print(f"  Findings ({len(report.findings)}):")
    for line in _format_findings(report):
        print(line)

    if not report.overall_passed:
        print("\n  FAIL: Well-formed agent should pass verification")
        return False

    print("\n  PASS: Agent config passed verification")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Integration test for three-tier verification gate (Pillar 2)"
    )
    parser.add_argument("--good-only", action="store_true", help="Run only good MCP tool test")
    parser.add_argument("--bad-only", action="store_true", help="Run only bad MCP tool test")
    parser.add_argument("--skill-only", action="store_true", help="Run only skill tests")
    parser.add_argument("--agent-only", action="store_true", help="Run only agent test")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        stream=sys.stderr,
    )

    only_one = args.good_only or args.bad_only or args.skill_only or args.agent_only
    run_good = args.good_only or not only_one
    run_bad = args.bad_only or not only_one
    run_skill = args.skill_only or not only_one
    run_agent = args.agent_only or not only_one

    results = {}

    if run_bad:
        # Fast tests first — no Docker/LLM
        results["bad_mcp_tool"] = test_bad_mcp_tool()

    if run_skill:
        results["bad_skill"] = test_bad_skill()
        results["good_skill"] = test_good_skill()

    if run_agent:
        results["good_agent"] = test_good_agent()

    if run_good:
        # Good tool — slowest (runs all 3 tiers including adversarial DeepAgent)
        results["good_mcp_tool"] = test_good_mcp_tool()

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
