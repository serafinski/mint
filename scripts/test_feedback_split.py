"""Deterministic smoke test for the CLI feedback → Pillar 3 split handoff.

Demonstrates end-to-end that user-flagged wrong outputs (§12 finding #6b)
flow correctly into Step 8's split wrong-output-case branch. Covers the
one piece of composition the unit tests prove only hop-by-hop:

    mint feedback --wrong (user-facing CLI)
        └─> MetricsStore.flag_most_recent_invocation_wrong
            └─> invocation row updated: success=False + error_message +
                actual_output preserved
                └─> MetricsStore.get_recent_failures returns the row
                    └─> Step 8 split consumes it as a failure case
                        (pulled here via the same DuckDB query; actual
                        LLM-driven split is exercised in
                        scripts/test_split_merge.py)

No LLM calls, no Docker, no pipeline execution — just the metrics layer
+ CLI wiring + split-side consumer query. Runs in ~3 seconds, costs
nothing.

Usage::

    uv run scripts/test_feedback_split.py

Exits 0 on all-pass, 1 on any failure. Uses an in-memory DuckDB so no
disk state is touched.

**Proposal alignment.** The CLI feedback loop is the §3.4-aligned user-
judgment oracle for Pillar 3's Pillar 3 success_rate metric. Without it,
CLI ``InvocationRecord``s only ever get ``success=False`` from crashes,
making Step 8's wrong-output-case branch CLI-dead-code (§12 finding #6b).
This script demonstrates the loop closes end-to-end.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.registry.metrics import MetricsStore  # noqa: E402
from src.schemas.metrics import InvocationRecord  # noqa: E402

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("test_feedback_split")


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------


def _banner(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def _step(n: int, total: int, text: str) -> None:
    print(f"\n  [{n}/{total}] {text}")


def _ok(detail: str) -> None:
    print(f"      ✓ {detail}")


def _fail(detail: str) -> None:
    print(f"      ✗ FAIL: {detail}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


def main() -> int:
    _banner("CLI FEEDBACK → SPLIT HANDOFF SMOKE (§12 findings #6b + split)")

    print("""
  Scenario: a user has run `mint run` several times against a buggy
  'clamp-integer' tool. The tool produces the wrong answer (out of
  range) for values > the default high bound, but never crashes. The
  pipeline records these as success=True because no oracle flags them.

  The user spots the wrong answers and flags them via `mint feedback
  --wrong --reason "value not clamped to high"`. The feedback path
  updates the invocation rows to success=False while preserving
  actual_output (so Step 8 split can diff variants against it) and
  setting error_message to the user's reason (so the error-case branch
  can also fire).

  This script demonstrates the full data flow end-to-end without any
  LLM cost. Actual LLM-driven split is exercised in
  scripts/test_split_merge.py; this one proves the handoff.
""")

    total_steps = 7
    store = MetricsStore()  # in-memory
    try:
        # --- Step 1: seed success-classified invocations -----------------
        _step(1, total_steps, "Record 5 invocations of 'clamp-integer' (pipeline marks success=True)")
        for i, (inputs, output) in enumerate([
            ('{"value": 50}', '{"clamped": 50}'),           # correct
            ('{"value": -10}', '{"clamped": -10}'),         # correct (low ok)
            ('{"value": 150}', '{"clamped": 150}'),         # WRONG — not clamped
            ('{"value": 200}', '{"clamped": 200}'),         # WRONG — not clamped
            ('{"value": 0}', '{"clamped": 0}'),             # correct
        ]):
            store.record_invocation(InvocationRecord(
                capability_id="clamp-integer",
                task_id=f"task-{i}",
                success=True,
                token_cost=50,
                latency_ms=1.5,
                input_args=inputs,
                actual_output=output,
            ))
        _ok("5 rows inserted, all success=True (pipeline has no oracle for semantic correctness)")

        # --- Step 2: pre-feedback check — no failure cases ---------------
        _step(2, total_steps, "get_recent_failures — expect 0 rows (no failures recorded yet)")
        failures = store.get_recent_failures("clamp-integer", limit=10)
        if len(failures) != 0:
            _fail(f"expected 0 failure rows; got {len(failures)}")
        _ok("0 rows returned — split would have nothing to specialize on")

        # --- Step 3: user flags the first wrong answer -------------------
        _step(3, total_steps, "User runs: mint feedback --wrong --reason 'value 200 not clamped to 100'")
        flagged = store.flag_most_recent_invocation_wrong(
            reason="value 200 not clamped to 100",
            capability_id="clamp-integer",
        )
        if flagged is None:
            _fail("flag_most_recent_invocation_wrong returned None")
        if flagged.capability_id != "clamp-integer":
            _fail(f"wrong capability flagged: {flagged.capability_id}")
        if flagged.task_id != "task-4":
            _fail(f"wrong task flagged (expected task-4, most recent): {flagged.task_id}")
        _ok(f"Flagged task-4 — note it was the MOST RECENT, not a wrong one")
        _ok("(user intent was task-3 which was the wrong one; this shows the --capability filter's limits)")

        # --- Step 4: flag the earlier wrong rows via targeted --capability
        _step(4, total_steps, "User re-runs buggy task explicitly then flags again (most-recent wrong)")
        # Re-insert the wrong case as a fresh invocation to simulate another run
        store.record_invocation(InvocationRecord(
            capability_id="clamp-integer",
            task_id="task-5-retry",
            success=True,
            token_cost=50,
            latency_ms=1.5,
            input_args='{"value": 150}',
            actual_output='{"clamped": 150}',
        ))
        flagged2 = store.flag_most_recent_invocation_wrong(
            reason="value 150 not clamped to 100",
            capability_id="clamp-integer",
        )
        if flagged2 is None or flagged2.task_id != "task-5-retry":
            _fail(f"expected task-5-retry flagged; got {flagged2.task_id if flagged2 else 'None'}")
        _ok("Flagged task-5-retry (value=150 case)")

        # --- Step 5: downstream visibility — split's data source sees them
        _step(5, total_steps, "get_recent_failures — Step 8 split's failure-case query")
        failures = store.get_recent_failures("clamp-integer", limit=10)
        if len(failures) != 2:
            _fail(f"expected 2 failure rows; got {len(failures)}")
        _ok(f"{len(failures)} flagged rows visible to split")
        for f in failures:
            _ok(f"  {f.task_id}: success={f.success}, reason={f.error_message!r}")
            if f.actual_output is None:
                _fail(f"actual_output missing on {f.task_id} — wrong-output-case branch blocked")
            if f.input_args is None:
                _fail(f"input_args missing on {f.task_id} — replay blocked")

        # --- Step 6: Chapter 6 analytics event -------------------------
        _step(6, total_steps, "capability_events table — Chapter 6 user-feedback analytics")
        rows = store._conn.execute(
            "SELECT event_type, details, capability_id FROM capability_events "
            "WHERE event_type = 'user_flagged_wrong' ORDER BY occurred_at"
        ).fetchall()
        if len(rows) != 2:
            _fail(f"expected 2 user_flagged_wrong events; got {len(rows)}")
        _ok(f"{len(rows)} user_flagged_wrong events recorded for cross-run analytics")
        for event_type, details, cap_id in rows:
            _ok(f"  {cap_id}: {details!r}")

        # --- Step 7: split candidacy signal -----------------------------
        _step(7, total_steps, "Verify split candidacy signal (success_rate drop)")
        success_rate = store.success_rate("clamp-integer", window=100)
        # 6 rows total, 2 flagged wrong → 4/6 success = 0.667
        expected = 4 / 6
        if abs(success_rate - expected) > 0.01:
            _fail(f"expected success_rate ≈ {expected:.3f}; got {success_rate:.3f}")
        _ok(f"success_rate = {success_rate:.3f} (below 0.7 split threshold)")
        _ok("Step 8 split would pull these 2 failure cases via get_recent_failures")
        _ok("and replay them against variants in verify_coverage (MCP_TOOL scope)")

        print()
        print("=" * 70)
        print(" ALL 7 STEPS PASSED — CLI feedback loop closes end-to-end")
        print("=" * 70)
        print("""
  Coverage notes:
  - Unit tests (TestFlagMostRecentInvocationWrong) prove the row-update
    mechanics in isolation.
  - scripts/test_split_merge.py::test_split_mcp_tool proves the split
    consumer side with directly-seeded failure rows (same shape).
  - This script proves the two halves compose: a CLI-level
    flag_most_recent_invocation_wrong call produces rows that the
    split-side query picks up and that split's wrong-output-case branch
    can replay.

  Proposal alignment: §3.4 Structured State Communication — the
  user-judgment oracle now flows as typed data into Pillar 3's metrics.
  Without this loop, Step 8's wrong-output-case branch was CLI-dead-code
  (§12 finding #6b).
""")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
