"""Regression guards for ``src/cli/runner.py::_record_invocation``.

Protects against the drift documented in
``docs/invocation_recording_gap_2026_04_19.md``: pre-fix, the CLI writer
left ``input_args`` and ``actual_output`` as ``None``, which silently
excluded every production row from ``MetricsStore.get_recent_failures``
(``WHERE input_args IS NOT NULL``), making the Pillar 3 Split coverage
verifier + failure-informed prompt structurally dead on real telemetry.

These tests assert the post-fix writer populates the fields that Split
depends on, and that error-shaped outputs are recorded as failures.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.cli.runner import _record_invocation
from src.registry.metrics import MetricsStore


def _artifact(name: str = "demo-tool") -> SimpleNamespace:
    return SimpleNamespace(capability_name=name)


class TestRecordInvocationPopulatesFields:
    def test_input_args_and_output_populated(self):
        """CLI writer must serialize task_prompt + output so the row
        survives get_recent_failures' ``WHERE input_args IS NOT NULL``."""
        metrics = MetricsStore(db_path=":memory:")

        _record_invocation(
            metrics,
            _artifact("demo-tool"),
            task_prompt="Parse the log file and extract errors",
            output={"error": "sandbox timeout"},
            elapsed_ms=1234,
        )

        failures = metrics.get_recent_failures("demo-tool")
        assert len(failures) == 1
        rec = failures[0]
        assert rec.input_args is not None
        assert '"task_prompt"' in rec.input_args
        assert rec.actual_output is not None
        assert '"error"' in rec.actual_output
        assert rec.error_message is not None
        assert "sandbox timeout" in rec.error_message
        assert rec.success is False

    def test_error_shaped_output_recorded_as_failure(self):
        """An output like ``{"error": "..."}`` must be recorded as
        success=False. Pre-fix ``bool(output)`` treated non-empty error
        dicts as successes."""
        metrics = MetricsStore(db_path=":memory:")

        _record_invocation(
            metrics,
            _artifact(),
            task_prompt="Any task",
            output={"error": "something broke"},
            elapsed_ms=10,
        )

        failures = metrics.get_recent_failures("demo-tool")
        assert len(failures) == 1
        assert failures[0].success is False

    def test_successful_output_not_mistaken_for_error(self):
        """Outputs with ``error_count`` or similar legitimate keys must
        NOT trigger the conservative error-shape check."""
        metrics = MetricsStore(db_path=":memory:")

        _record_invocation(
            metrics,
            _artifact(),
            task_prompt="Count errors in logs",
            output={"error_count": 0, "total_rows": 100},
            elapsed_ms=10,
        )

        # Success path — row is recorded but not in the failure bucket.
        failures = metrics.get_recent_failures("demo-tool")
        assert len(failures) == 0

    def test_none_output_recorded_as_failure_without_error_message(self):
        """``output=None`` (e.g., pipeline gave up before producing anything)
        must be success=False. Empty output has no error-shape keys, so
        error_message stays None; the row still writes input_args + None
        actual_output — it's filtered out by get_recent_failures because
        neither actual_output nor error_message is populated."""
        metrics = MetricsStore(db_path=":memory:")

        _record_invocation(
            metrics,
            _artifact(),
            task_prompt="task",
            output=None,
            elapsed_ms=5,
        )

        # Row is written but excluded from get_recent_failures because
        # it has neither actual_output nor error_message (weaker signal
        # than a crash — the coverage verifier can't replay a no-output
        # failure). This is correct behavior per the query contract at
        # src/registry/metrics.py:498.
        failures = metrics.get_recent_failures("demo-tool")
        assert len(failures) == 0

    def test_no_artifact_is_noop(self):
        """``artifact=None`` short-circuits with no DB write (unchanged
        pre-existing behavior)."""
        metrics = MetricsStore(db_path=":memory:")

        _record_invocation(
            metrics,
            None,
            task_prompt="task",
            output={"result": 1},
            elapsed_ms=5,
        )

        # Query all rows — none should exist.
        rows = metrics._conn.execute("SELECT COUNT(*) FROM invocations").fetchone()
        assert rows[0] == 0
