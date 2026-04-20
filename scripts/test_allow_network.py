"""Step 14.12 — Integration test for the ``--allow-network`` end-to-end path.

Validates that the network-policy plumbing actually reaches the Docker
sandbox (not just LangFuse metadata) and that Tier 2's network-disconnect
differential detects silent fallbacks.

Four tests:

1. **test_allow_network_flag_changes_docker_behavior** — asserts at the
   primitive layer that ``sandbox_execute(..., allow_network=True)`` runs
   with ``--network bridge`` and ``allow_network=False`` runs with
   ``--network none``. Regression guard for anyone who silently reverts
   the plumbing.
2. **test_network_allowed_succeeds** — runs a known-good MCP tool that
   hits ``https://api.github.com/zen`` through ``_execute_mcp_tool``
   with ``allow_network=True``. Asserts non-empty stdout. Positive
   control: the flag actually works end-to-end.
3. **test_network_blocked_fails_cleanly** — same artifact, but
   ``allow_network=False``. Asserts the call errors with a socket-level
   failure, NOT a fabricated response. Confirms the offline posture.
4. **test_fake_network_tool_rejected** — an artifact that *claims* to
   call the network but actually returns hardcoded data. Runs the full
   verification gate with ``allow_network=False``. Asserts Tier 1 or
   Tier 2 rejects it. Negative control: contaminated tools still get
   caught regardless of network policy.

Endpoint choice: ``https://api.github.com/zen`` (no auth, tiny response,
GitHub's stated liveness-check endpoint). Fallbacks if rate-limited:
``https://httpbin.org/get``, ``https://worldtimeapi.org/api/timezone/Etc/UTC``.

Usage::

    # All tests
    uv run scripts/test_allow_network.py

    # Specific test
    uv run scripts/test_allow_network.py --primitive-only
    uv run scripts/test_allow_network.py --live-only
    uv run scripts/test_allow_network.py --verification-only

    # Verbose logging
    uv run scripts/test_allow_network.py --verbose

Requires Docker running. Makes live HTTP calls to a free public endpoint
(no auth required). Skips gracefully when the host itself has no network.
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
from pathlib import Path

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

from src.schemas.capability import CapabilityType  # noqa: E402
from src.schemas.task import GeneratedArtifact  # noqa: E402
from src.verification.sandbox_tool import sandbox_execute  # noqa: E402

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Endpoints (ordered by preference — first reachable one wins)
# ---------------------------------------------------------------------------

LIVE_ENDPOINTS = [
    "https://api.github.com/zen",  # tiny philosophical quote, no auth
    "https://httpbin.org/get",  # zero-auth echo
    "https://worldtimeapi.org/api/timezone/Etc/UTC",  # zero-auth time
]


class NetworkUnavailableError(RuntimeError):
    """Raised by smoke tests when the host has no network and plumbing
    cannot be validated.

    Security audit finding #43 (2026-04-20): pre-fix the helpers
    returned ``True`` (pass-by-skip), masking plumbing regressions on
    offline CI. Now raised so ``main()`` can emit exit code 2
    (UNABLE-TO-VALIDATE), distinguishable from 0 (VALIDATED) and 1
    (FAILED).
    """


def _host_has_network() -> bool:
    """Quick host-level reachability probe — decides SKIP vs FAIL."""
    for host in ("api.github.com", "httpbin.org", "worldtimeapi.org"):
        try:
            socket.gethostbyname(host)
            return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------
# Test artifacts
# ---------------------------------------------------------------------------

LIVE_NETWORK_TOOL = GeneratedArtifact(
    capability_name="fetch-zen-quote",
    capability_type=CapabilityType.MCP_TOOL,
    description="Fetch a GitHub Zen quote from the public API",
    needs_network=True,
    pip_packages=[],  # stdlib only
    source_code=(
        'from typing import Annotated\n'
        'from mcp.server.fastmcp import FastMCP\n'
        'from pydantic import Field\n'
        'import urllib.request\n'
        'mcp = FastMCP("zen")\n'
        '@mcp.tool()\n'
        'async def fetch_zen(\n'
        '    topic: Annotated[str, Field(description="Unused topic placeholder")] = "wisdom"\n'
        ') -> dict:\n'
        '    """Fetch a short philosophical quote from GitHub\'s zen endpoint."""\n'
        '    with urllib.request.urlopen(\n'
        '        "https://api.github.com/zen", timeout=5\n'
        '    ) as r:\n'
        '        quote = r.read().decode()\n'
        '    return {"quote": quote, "topic": topic}\n'
        'if __name__ == "__main__":\n'
        '    mcp.run()\n'
    ),
)


FAKE_NETWORK_TOOL = GeneratedArtifact(
    capability_name="fake-zen-quote",
    capability_type=CapabilityType.MCP_TOOL,
    description="CLAIMS to fetch Zen quote but returns hardcoded data",
    needs_network=True,
    pip_packages=[],
    source_code=(
        'from typing import Annotated\n'
        'from mcp.server.fastmcp import FastMCP\n'
        'from pydantic import Field\n'
        'mcp = FastMCP("fake-zen")\n'
        '@mcp.tool()\n'
        'async def fetch_zen(\n'
        '    topic: Annotated[str, Field(description="Unused topic placeholder")] = "wisdom"\n'
        ') -> dict:\n'
        '    """Fetches a Zen quote from GitHub (allegedly)."""\n'
        '    # Hardcoded response — Tier 1 static scanner should catch this.\n'
        '    return {"quote": "Beautiful is better than ugly.", "topic": topic}\n'
        'if __name__ == "__main__":\n'
        '    mcp.run()\n'
    ),
)


# ---------------------------------------------------------------------------
# Primitive-layer test (cheap, no network needed)
# ---------------------------------------------------------------------------


def test_allow_network_flag_changes_docker_behavior() -> bool:
    """Assert ``sandbox_execute`` honors ``allow_network`` at the Docker arg layer.

    Runs a tiny snippet that calls ``socket.gethostbyname`` against a
    well-known public hostname — succeeds when network is allowed,
    raises ``gaierror`` when the sandbox has ``--network none``. No LLM
    calls, ~5s total, answers the question "does the Docker flag reach
    the container?".
    """
    print("\n" + "=" * 70)
    print("TEST 1: sandbox_execute --network bridge vs --network none")
    print("=" * 70)

    code = (
        "import socket\n"
        "try:\n"
        "    ip = socket.gethostbyname('api.github.com')\n"
        "    print('RESOLVED:' + ip)\n"
        "except Exception as e:\n"
        "    print('FAILED:' + type(e).__name__)\n"
    )

    print("Running with allow_network=False (expect FAILED)...")
    off = sandbox_execute(code, timeout=10, allow_network=False)
    print(f"  stdout: {off.get('stdout', '').strip()}")
    off_ok = "FAILED" in (off.get("stdout") or "") or not off.get("success", False)
    if not off_ok:
        print("✗ Offline sandbox resolved a hostname — --network none is NOT wired.")
        return False

    print("Running with allow_network=True (expect RESOLVED)...")
    on = sandbox_execute(code, timeout=10, allow_network=True)
    print(f"  stdout: {on.get('stdout', '').strip()}")
    on_ok = "RESOLVED" in (on.get("stdout") or "")
    if not on_ok:
        # Likely the host itself is offline. Distinguish via host probe.
        # Security audit finding #43 (2026-04-20): returning a plain
        # pass-by-skip masked plumbing regressions on offline CI. Now
        # raises ``NetworkUnavailableError`` so ``main()`` can exit
        # with a distinct code 2 (UNABLE-TO-VALIDATE), differentiated
        # from 0 (VALIDATED) and 1 (FAILED VALIDATION).
        if not _host_has_network():
            raise NetworkUnavailableError(
                "host has no network — cannot verify bridge mode"
            )
        print("✗ Bridge sandbox failed to resolve a hostname — plumbing broken.")
        return False

    print("✓ --network flag is correctly wired at the Docker layer.")
    return True


# ---------------------------------------------------------------------------
# Live-API tests
# ---------------------------------------------------------------------------


def test_network_allowed_succeeds() -> bool:
    """End-to-end: fetch a live quote through ``_execute_mcp_tool``.

    This exercises the full CLI plumbing path: ``_execute_mcp_tool``
    receives ``allow_network=True``, builds the sandbox code, and passes
    the flag down to ``sandbox_execute``. Asserts the tool actually
    returns real data (not an error).
    """
    print("\n" + "=" * 70)
    print("TEST 2: _execute_mcp_tool(allow_network=True) fetches real data")
    print("=" * 70)

    if not _host_has_network():
        # Security audit finding #43 (2026-04-20): raise instead of
        # pass-by-skip so the smoke runner can exit with code 2.
        raise NetworkUnavailableError("host has no network — cannot run live API test")

    from src.cli.runner import _execute_mcp_tool

    print(f"Fetching from {LIVE_ENDPOINTS[0]}...")
    output = _execute_mcp_tool(
        LIVE_NETWORK_TOOL,
        file_paths=[],
        params={},
        allow_network=True,
    )
    print(f"  output: {output}")

    if not output or "quote" not in output:
        print("✗ Expected {'quote': ...} from live fetch, got empty/wrong dict.")
        return False
    quote = output["quote"]
    if not isinstance(quote, str) or not quote.strip():
        print(f"✗ Quote is empty or not a string: {quote!r}")
        return False
    print(f"✓ Live fetch returned a real quote ({len(quote)} chars).")
    return True


def test_network_blocked_fails_cleanly() -> bool:
    """With allow_network=False, the same tool must fail — not fabricate."""
    print("\n" + "=" * 70)
    print("TEST 3: _execute_mcp_tool(allow_network=False) fails cleanly")
    print("=" * 70)

    from src.cli.runner import _execute_mcp_tool

    output = _execute_mcp_tool(
        LIVE_NETWORK_TOOL,
        file_paths=[],
        params={},
        allow_network=False,
    )
    print(f"  output: {output}")

    # On failure, _execute_mcp_tool returns {} (the tool errored, stderr
    # printed, parse failed, or similar). That's the correct behavior —
    # we do NOT want a fabricated "quote" key to appear.
    if "quote" in output and output["quote"]:
        print("✗ Offline run returned a fabricated quote — silent fallback risk!")
        return False
    print("✓ Offline run returned empty (no fabricated fallback).")
    return True


# ---------------------------------------------------------------------------
# Verification-gate test (contaminated tool caught regardless of network policy)
# ---------------------------------------------------------------------------


def test_fake_network_tool_rejected() -> bool:
    """A tool that claims network access but hardcodes its response gets caught.

    Runs the full three-tier gate against FAKE_NETWORK_TOOL with the
    offline policy. Tier 1 (static scanner) should catch the hardcoded
    dict pattern — same as any other hardcoded-data artifact.
    """
    print("\n" + "=" * 70)
    print("TEST 4: verification gate rejects fake-network tool")
    print("=" * 70)

    from src.verification.gate import verify

    report = verify(FAKE_NETWORK_TOOL, allow_network=False)
    print(f"  overall_passed: {report.overall_passed}")
    print(f"  tier_reached: {report.tier_reached}")
    print("  findings:")
    for f in report.findings:
        print(f"    [T{f.tier} {f.severity.value}] {f.category}: {f.message[:80]}")

    if report.overall_passed:
        print("✗ Fake tool passed the gate — contamination detection broken!")
        return False
    print("✓ Fake tool was correctly rejected.")
    return True


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--primitive-only",
        action="store_true",
        help="Run only test 1 (Docker flag layer)",
    )
    parser.add_argument(
        "--live-only",
        action="store_true",
        help="Run only tests 2 + 3 (live API)",
    )
    parser.add_argument(
        "--verification-only",
        action="store_true",
        help="Run only test 4 (gate)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    selected: list[tuple[str, callable]] = []
    if args.primitive_only:
        selected = [("test_allow_network_flag_changes_docker_behavior",
                     test_allow_network_flag_changes_docker_behavior)]
    elif args.live_only:
        selected = [
            ("test_network_allowed_succeeds", test_network_allowed_succeeds),
            ("test_network_blocked_fails_cleanly", test_network_blocked_fails_cleanly),
        ]
    elif args.verification_only:
        selected = [("test_fake_network_tool_rejected",
                     test_fake_network_tool_rejected)]
    else:
        selected = [
            ("test_allow_network_flag_changes_docker_behavior",
             test_allow_network_flag_changes_docker_behavior),
            ("test_network_allowed_succeeds", test_network_allowed_succeeds),
            ("test_network_blocked_fails_cleanly", test_network_blocked_fails_cleanly),
            ("test_fake_network_tool_rejected", test_fake_network_tool_rejected),
        ]

    results: list[tuple[str, str]] = []  # (name, status: "pass"|"fail"|"skip")
    for name, fn in selected:
        try:
            ok = fn()
            status = "pass" if ok else "fail"
        except NetworkUnavailableError as exc:
            # Security audit finding #43 (2026-04-20): tri-state result.
            # SKIP is NOT the same as PASS — main() exits with code 2.
            print(f"⚠ {name} skipped: {exc}")
            status = "skip"
        except Exception as exc:  # noqa: BLE001
            logger.exception("%s raised", name)
            print(f"✗ {name} raised: {exc}")
            status = "fail"
        results.append((name, status))

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    mark_map = {"pass": "✓", "fail": "✗", "skip": "⚠"}
    for name, status in results:
        print(f"  {mark_map[status]} {name} ({status})")

    if any(status == "fail" for _, status in results):
        return 1
    if any(status == "skip" for _, status in results):
        # Exit 2 = UNABLE-TO-VALIDATE (host offline). Different from pass.
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
