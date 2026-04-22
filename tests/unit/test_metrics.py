from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.registry.metrics import GranularityMetrics, InvocationRecord, MetricsStore

# -- helpers --------------------------------------------------------------


def _make_record(**overrides: object) -> InvocationRecord:
    """Create an InvocationRecord with sensible defaults."""
    defaults: dict = {
        "capability_id": "fibonacci",
        "task_id": "task-1",
        "success": True,
        "token_cost": 100,
        "latency_ms": 50.0,
    }
    defaults.update(overrides)
    return InvocationRecord(**defaults)


# -- fixtures -------------------------------------------------------------


@pytest.fixture
def store():
    """In-memory DuckDB metrics store, auto-closed after test."""
    s = MetricsStore()
    yield s
    s.close()


# -- parent-directory autocreation (defensive init) ----------------------


def test_init_creates_parent_directory_if_missing(tmp_path):
    """``MetricsStore(db_path=...)`` mkdirs missing parent dirs.

    Regression guard: ``duckdb.connect(db_path)`` raises FileNotFoundError
    when the parent directory doesn't exist. CLI + eval harness relied on
    upstream code (e.g., ``_create_infra``) to pre-mkdir; tests that bypass
    that path crashed. Defensive init closes the class of bug.
    """
    nested = tmp_path / "nested" / "sub" / "dir" / "metrics.duckdb"
    assert not nested.parent.exists()
    with MetricsStore(db_path=str(nested)) as store:
        store.record_invocation(_make_record())
    assert nested.exists()


# -- record & count -------------------------------------------------------


def test_record_and_count_invocation(store: MetricsStore):
    store.record_invocation(_make_record())
    assert store.invocation_count("fibonacci") == 1


def test_record_multiple_invocations(store: MetricsStore):
    for i in range(3):
        store.record_invocation(_make_record(task_id=f"task-{i}"))
    assert store.invocation_count("fibonacci") == 3


# -- reuse rate -----------------------------------------------------------


def test_reuse_rate_single_task(store: MetricsStore):
    store.record_invocation(_make_record(task_id="task-1"))
    # 1 unique task, and it used fibonacci -> 1.0
    assert store.reuse_rate("fibonacci") == 1.0


def test_reuse_rate_multiple_tasks(store: MetricsStore):
    # 4 distinct tasks: task-1..task-4
    # Only task-1 and task-2 use fibonacci
    store.record_invocation(_make_record(capability_id="fibonacci", task_id="task-1"))
    store.record_invocation(_make_record(capability_id="fibonacci", task_id="task-2"))
    store.record_invocation(_make_record(capability_id="csv-parser", task_id="task-3"))
    store.record_invocation(_make_record(capability_id="csv-parser", task_id="task-4"))
    # 4 unique tasks, 2 used fibonacci -> 0.5
    rate = store.reuse_rate("fibonacci")
    assert rate == pytest.approx(0.5)


# -- success rate ---------------------------------------------------------


def test_success_rate_all_success(store: MetricsStore):
    for i in range(5):
        store.record_invocation(_make_record(task_id=f"task-{i}", success=True))
    assert store.success_rate("fibonacci") == pytest.approx(1.0)


def test_success_rate_mixed(store: MetricsStore):
    for i in range(3):
        store.record_invocation(_make_record(task_id=f"s-{i}", success=True))
    for i in range(2):
        store.record_invocation(_make_record(task_id=f"f-{i}", success=False))
    assert store.success_rate("fibonacci") == pytest.approx(0.6)


def test_success_rate_no_invocations(store: MetricsStore):
    assert store.success_rate("nonexistent") == 0.0


# -- avg token cost -------------------------------------------------------


def test_avg_token_cost(store: MetricsStore):
    costs = [100, 200, 300]
    for i, cost in enumerate(costs):
        store.record_invocation(_make_record(task_id=f"task-{i}", token_cost=cost))
    assert store.avg_token_cost("fibonacci") == pytest.approx(200.0)


# -- maintenance burden ---------------------------------------------------


def test_maintenance_burden_no_events(store: MetricsStore):
    assert store.maintenance_burden("fibonacci") == 0.0


def test_maintenance_burden_with_events(store: MetricsStore):
    # 6 maintenance events in 30 days -> 0.2 per day
    for i in range(6):
        store.record_event("fibonacci", "regenerated", f"attempt {i}")
    burden = store.maintenance_burden("fibonacci", window_days=30)
    assert burden == pytest.approx(6.0 / 30.0)


# -- compute_granularity_metrics ------------------------------------------


def test_compute_granularity_metrics(store: MetricsStore):
    # Set up some data
    for i in range(4):
        store.record_invocation(
            _make_record(task_id=f"task-{i}", success=(i % 2 == 0), token_cost=100 * (i + 1))
        )
    store.record_event("fibonacci", "patched")

    metrics = store.compute_granularity_metrics("fibonacci")
    assert isinstance(metrics, GranularityMetrics)
    assert metrics.capability_id == "fibonacci"
    assert 0.0 <= metrics.reuse_rate <= 1.0
    assert metrics.success_rate == pytest.approx(0.5)
    assert metrics.token_cost == pytest.approx(250.0)
    assert metrics.maintenance_burden > 0.0
    assert metrics.window_size == 100


# -- list_capabilities ----------------------------------------------------


def test_list_capabilities(store: MetricsStore):
    store.record_invocation(_make_record(capability_id="fibonacci"))
    store.record_invocation(_make_record(capability_id="csv-parser"))
    store.record_invocation(_make_record(capability_id="date-formatter"))
    caps = store.list_capabilities()
    assert caps == ["csv-parser", "date-formatter", "fibonacci"]


# -- recent_invocations ---------------------------------------------------


def test_recent_invocations_ordering(store: MetricsStore):
    base = datetime(2025, 1, 1, tzinfo=UTC)
    for i in range(5):
        store.record_invocation(
            _make_record(
                task_id=f"task-{i}",
                invoked_at=base + timedelta(hours=i),
            )
        )
    recent = store.recent_invocations("fibonacci", limit=3)
    assert len(recent) == 3
    # Newest first
    assert recent[0].invoked_at > recent[1].invoked_at > recent[2].invoked_at


# -- persistence ----------------------------------------------------------


def test_persistence_with_file(tmp_path):
    db_file = str(tmp_path / "metrics.duckdb")
    with MetricsStore(db_path=db_file) as store:
        store.record_invocation(_make_record())
        assert store.invocation_count("fibonacci") == 1

    # Reopen — data should survive
    with MetricsStore(db_path=db_file) as store2:
        assert store2.invocation_count("fibonacci") == 1


# -- edge cases -----------------------------------------------------------


def test_empty_store_returns_zero_metrics(store: MetricsStore):
    metrics = store.compute_granularity_metrics("nonexistent")
    assert metrics.reuse_rate == 0.0
    assert metrics.success_rate == 0.0
    assert metrics.token_cost == 0.0
    assert metrics.maintenance_burden == 0.0


def test_window_parameter_limits_data(store: MetricsStore):
    # Insert 10 invocations: first 5 succeed, last 5 fail
    base = datetime(2025, 1, 1, tzinfo=UTC)
    for i in range(10):
        store.record_invocation(
            _make_record(
                task_id=f"task-{i}",
                success=(i < 5),
                token_cost=100,
                invoked_at=base + timedelta(hours=i),
            )
        )
    # Window of 5 should only see the last 5 (all failures)
    assert store.success_rate("fibonacci", window=5) == pytest.approx(0.0)
    # Window of 10 should see all (50% success)
    assert store.success_rate("fibonacci", window=10) == pytest.approx(0.5)


def test_record_event(store: MetricsStore):
    store.record_event("fibonacci", "created", "initial creation")
    store.record_event("fibonacci", "regenerated", "fix bug")
    # 'created' is not a maintenance event, 'regenerated' is
    burden = store.maintenance_burden("fibonacci", window_days=30)
    assert burden == pytest.approx(1.0 / 30.0)


# -- Step 4 (B1 Pillar 3): input_args + actual_output + get_recent_failures ----


class TestInvocationRecordFailureReplayFields:
    """Step 4: InvocationRecord carries serialized input/output for the
    Tier 4 coverage verifier's failure-case replay in Step 8 split."""

    def test_input_args_defaults_to_none(self):
        record = _make_record()
        assert record.input_args is None
        assert record.actual_output is None

    def test_input_args_accepts_json_string(self):
        record = _make_record(
            input_args='{"n": 5, "mode": "fast"}',
            actual_output='{"result": 8}',
        )
        assert record.input_args == '{"n": 5, "mode": "fast"}'
        assert record.actual_output == '{"result": 8}'

    def test_round_trip_through_record_invocation(self, store: MetricsStore):
        """New fields survive the DuckDB write/read cycle."""
        store.record_invocation(
            _make_record(
                input_args='{"file_path": "/tmp/input.csv"}',
                actual_output='{"rows": 42}',
            )
        )
        recent = store.recent_invocations("fibonacci", limit=1)
        assert len(recent) == 1
        assert recent[0].input_args == '{"file_path": "/tmp/input.csv"}'
        assert recent[0].actual_output == '{"rows": 42}'

    def test_recent_invocations_all_reads_new_fields(self, store: MetricsStore):
        store.record_invocation(
            _make_record(
                capability_id="cap-a",
                input_args='{"x": 1}',
                actual_output='{"y": 2}',
            )
        )
        recent = store.recent_invocations_all(limit=5)
        assert len(recent) == 1
        assert recent[0].input_args == '{"x": 1}'
        assert recent[0].actual_output == '{"y": 2}'

    def test_null_input_args_round_trip(self, store: MetricsStore):
        """Legacy callers that don't populate the new fields still work."""
        store.record_invocation(_make_record())  # no input_args / actual_output
        recent = store.recent_invocations("fibonacci", limit=1)
        assert recent[0].input_args is None
        assert recent[0].actual_output is None


class TestGetRecentFailures:
    """Step 4: get_recent_failures is Step 8's failure-case query for
    the Tier 4 coverage verifier."""

    def test_empty_returns_empty_list(self, store: MetricsStore):
        assert store.get_recent_failures("cap-a") == []

    def test_filters_to_failures_only(self, store: MetricsStore):
        store.record_invocation(
            _make_record(
                success=True,
                input_args='{"x": 1}',
                actual_output='{"y": 2}',
            )
        )
        store.record_invocation(
            _make_record(
                task_id="task-2",
                success=False,
                error_message="boom",
                input_args='{"x": 2}',
                actual_output='{"error": "boom"}',
            )
        )
        failures = store.get_recent_failures("fibonacci")
        assert len(failures) == 1
        assert failures[0].success is False
        assert failures[0].error_message == "boom"

    def test_filters_to_single_capability(self, store: MetricsStore):
        store.record_invocation(
            _make_record(
                capability_id="cap-a",
                success=False,
                input_args='{"x": 1}',
                actual_output='{"err": 1}',
            )
        )
        store.record_invocation(
            _make_record(
                capability_id="cap-b",
                success=False,
                input_args='{"x": 2}',
                actual_output='{"err": 2}',
            )
        )
        assert len(store.get_recent_failures("cap-a")) == 1
        assert len(store.get_recent_failures("cap-b")) == 1

    def test_skips_rows_without_input_args(self, store: MetricsStore):
        """A failure without captured input is useless as a replay probe
        and must be skipped by the query."""
        store.record_invocation(
            _make_record(
                success=False,
                error_message="had no inputs captured",
                input_args=None,
                actual_output='{"err": 1}',
            )
        )
        assert store.get_recent_failures("fibonacci") == []

    def test_includes_error_cases_without_actual_output(self, store: MetricsStore):
        """Error-cases (parent raised, no actual_output) MUST be returned.

        2026-04-16 fix: an earlier version of get_recent_failures also
        required ``actual_output IS NOT NULL`` in the SQL filter, which
        silently dropped every error-case. That made Step 8's
        error-case coverage branch CLI-dead-code because crash-path
        InvocationRecords never reached verify_coverage. The bug was
        empirically caught by scripts/test_split_merge.py --split-mcp-only
        when "Seeded 1 failure case(s) for replay" didn't match the
        expected 2. This test is the regression guard.
        """
        store.record_invocation(
            _make_record(
                success=False,
                error_message="ValueError: something broke",
                input_args='{"x": 1}',
                actual_output=None,
            )
        )
        failures = store.get_recent_failures("fibonacci")
        assert len(failures) == 1
        assert failures[0].error_message == "ValueError: something broke"
        assert failures[0].actual_output is None
        assert failures[0].input_args == '{"x": 1}'

    def test_skips_rows_missing_both_error_and_output(self, store: MetricsStore):
        """A row with success=False but NEITHER error_message NOR
        actual_output tells us nothing about what the failure looked
        like — no failure mode to replay. Skip."""
        store.record_invocation(
            _make_record(
                success=False,
                error_message=None,
                input_args='{"x": 1}',
                actual_output=None,
            )
        )
        assert store.get_recent_failures("fibonacci") == []

    def test_orders_newest_first(self, store: MetricsStore):
        base = datetime(2025, 1, 1, tzinfo=UTC)
        for i in range(3):
            store.record_invocation(
                _make_record(
                    task_id=f"task-{i}",
                    success=False,
                    error_message=f"err-{i}",
                    input_args=f'{{"attempt": {i}}}',
                    actual_output=f'{{"err": "err-{i}"}}',
                    invoked_at=base + timedelta(hours=i),
                )
            )
        failures = store.get_recent_failures("fibonacci", limit=5)
        assert len(failures) == 3
        assert failures[0].invoked_at > failures[1].invoked_at > failures[2].invoked_at
        # Newest failure is task-2
        assert failures[0].task_id == "task-2"

    def test_honors_limit(self, store: MetricsStore):
        base = datetime(2025, 1, 1, tzinfo=UTC)
        for i in range(5):
            store.record_invocation(
                _make_record(
                    task_id=f"task-{i}",
                    success=False,
                    input_args=f'{{"i": {i}}}',
                    actual_output=f'{{"err": {i}}}',
                    invoked_at=base + timedelta(hours=i),
                )
            )
        assert len(store.get_recent_failures("fibonacci", limit=2)) == 2
        assert len(store.get_recent_failures("fibonacci", limit=100)) == 5


class TestDuckDBMigration:
    """Step 4: ADD COLUMN IF NOT EXISTS migration runs on every init
    so old databases pick up the new columns without data loss."""

    def test_fresh_database_has_new_columns(self, store: MetricsStore):
        """Fresh DBs get the columns via _INIT_SQL; no-op migration."""
        store.record_invocation(
            _make_record(input_args='{"x": 1}', actual_output='{"y": 2}')
        )
        recent = store.recent_invocations("fibonacci", limit=1)
        assert recent[0].input_args == '{"x": 1}'

    def test_migration_adds_columns_to_legacy_schema(self, tmp_path):
        """Simulate a pre-Step-4 database by creating the invocations
        table with the old schema, then reopen via MetricsStore and
        verify the migration added the columns."""
        db_file = str(tmp_path / "legacy.duckdb")

        # Create a legacy table without input_args / actual_output.
        import duckdb as _duckdb
        _conn = _duckdb.connect(db_file)
        _conn.execute(
            """
            CREATE TABLE invocations (
                id VARCHAR PRIMARY KEY,
                capability_id VARCHAR NOT NULL,
                task_id VARCHAR NOT NULL,
                success BOOLEAN NOT NULL,
                token_cost INTEGER NOT NULL DEFAULT 0,
                latency_ms DOUBLE NOT NULL DEFAULT 0.0,
                error_message VARCHAR,
                invoked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        _conn.execute(
            """
            INSERT INTO invocations (id, capability_id, task_id, success,
                                     token_cost, latency_ms, error_message, invoked_at)
            VALUES ('legacy-1', 'fibonacci', 'task-old', TRUE, 100, 50.0, NULL, CURRENT_TIMESTAMP)
            """
        )
        _conn.close()

        # Open via MetricsStore — migration should run and add the columns.
        with MetricsStore(db_path=db_file) as store:
            # Legacy row reads cleanly with new fields as None.
            recent = store.recent_invocations("fibonacci", limit=1)
            assert len(recent) == 1
            assert recent[0].task_id == "task-old"
            assert recent[0].input_args is None
            assert recent[0].actual_output is None

            # New rows can populate the columns.
            store.record_invocation(
                _make_record(
                    task_id="task-new",
                    input_args='{"fresh": true}',
                    actual_output='{"ok": 1}',
                )
            )
            recent_all = store.recent_invocations("fibonacci", limit=5)
            new_row = next(r for r in recent_all if r.task_id == "task-new")
            assert new_row.input_args == '{"fresh": true}'
            assert new_row.actual_output == '{"ok": 1}'


# ---------------------------------------------------------------------------
# §12 finding #6b — user-judgment wrong-output flagging
# ---------------------------------------------------------------------------


class TestFlagMostRecentInvocationWrong:
    def test_no_invocations_returns_none(self, store):
        assert store.flag_most_recent_invocation_wrong("reason") is None

    def test_flags_most_recent_across_all_capabilities(self, store):
        """With no capability filter, flag the globally-most-recent row."""
        # Insert three rows in order; the last one should be flagged.
        store.record_invocation(_make_record(
            capability_id="tool-a", task_id="t1",
            actual_output='{"x": 1}',
        ))
        store.record_invocation(_make_record(
            capability_id="tool-b", task_id="t2",
            actual_output='{"y": 2}',
        ))
        store.record_invocation(_make_record(
            capability_id="tool-c", task_id="t3",
            actual_output='{"z": 3}',
        ))

        flagged = store.flag_most_recent_invocation_wrong("got it wrong")

        assert flagged is not None
        assert flagged.capability_id == "tool-c"
        assert flagged.task_id == "t3"
        assert flagged.success is False
        assert flagged.error_message == "got it wrong"
        # Original actual_output preserved for Step 8 wrong-output-case
        # branch to replay against.
        assert flagged.actual_output == '{"z": 3}'

    def test_capability_filter_flags_most_recent_for_that_cap_only(self, store):
        store.record_invocation(_make_record(capability_id="tool-a", task_id="a1"))
        store.record_invocation(_make_record(capability_id="tool-b", task_id="b1"))
        store.record_invocation(_make_record(capability_id="tool-a", task_id="a2"))
        store.record_invocation(_make_record(capability_id="tool-b", task_id="b2"))

        flagged = store.flag_most_recent_invocation_wrong(
            "wrong", capability_id="tool-a",
        )
        assert flagged is not None
        assert flagged.capability_id == "tool-a"
        assert flagged.task_id == "a2"  # most recent for tool-a, not b2

    def test_capability_filter_no_match_returns_none(self, store):
        store.record_invocation(_make_record(capability_id="tool-a"))
        assert store.flag_most_recent_invocation_wrong(
            "reason", capability_id="nonexistent",
        ) is None

    def test_flagged_row_visible_to_get_recent_failures(self, store):
        """Downstream: flagged rows become Step 8 split-candidate failure cases."""
        store.record_invocation(_make_record(
            capability_id="parser", task_id="t1",
            input_args='{"value": 150}', actual_output='{"clamped": 150}',
        ))

        store.flag_most_recent_invocation_wrong(
            "expected clamp to 100", capability_id="parser",
        )

        failures = store.get_recent_failures("parser", limit=5)
        assert len(failures) == 1
        assert failures[0].success is False
        assert failures[0].error_message == "expected clamp to 100"
        assert failures[0].input_args == '{"value": 150}'
        assert failures[0].actual_output == '{"clamped": 150}'

    def test_records_user_flagged_wrong_event(self, store):
        """Chapter 6 analytics signal — count user-feedback events."""
        store.record_invocation(_make_record(capability_id="tool-a"))
        store.flag_most_recent_invocation_wrong("bad answer")

        # The event is stored in capability_events; query it directly.
        rows = store._conn.execute(
            "SELECT event_type, details, capability_id FROM capability_events "
            "WHERE event_type = 'user_flagged_wrong'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "bad answer"
        assert rows[0][2] == "tool-a"


# -- fetch_invocations_for_task (Step 18.5) -------------------------------


def test_fetch_empty_store_returns_empty_list(store: MetricsStore):
    assert store.fetch_invocations_for_task("T01") == []


def test_fetch_returns_only_matching_task_id(store: MetricsStore):
    store.record_invocation(_make_record(capability_id="cap-a", task_id="T01"))
    store.record_invocation(_make_record(capability_id="cap-b", task_id="T02"))
    store.record_invocation(_make_record(capability_id="cap-c", task_id="T01"))

    rows = store.fetch_invocations_for_task("T01")
    assert len(rows) == 2
    assert {r["capability_id"] for r in rows} == {"cap-a", "cap-c"}


def test_fetch_row_shape_matches_eval_store_expected(store: MetricsStore):
    store.record_invocation(_make_record(
        capability_id="cap-a", task_id="T01", success=True,
        token_cost=150, latency_ms=200,
    ))
    rows = store.fetch_invocations_for_task("T01")
    assert len(rows) == 1
    row = rows[0]
    # EvaluationStore.record_invocation_events expects these keys.
    assert set(row.keys()) >= {"capability_id", "success", "token_cost", "latency_ms"}
    assert row["capability_id"] == "cap-a"
    assert row["success"] is True
    assert row["token_cost"] == 150
    assert row["latency_ms"] == 200


def test_fetch_preserves_invocation_order(store: MetricsStore):
    # Earlier invocations should come first (order by invoked_at).
    for name in ("first", "second", "third"):
        store.record_invocation(_make_record(capability_id=name, task_id="T01"))

    rows = store.fetch_invocations_for_task("T01")
    assert [r["capability_id"] for r in rows] == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# Shared invocation-recording helpers (2026-04-19 fix for
# docs/invocation_recording_gap_2026_04_19.md).
# ---------------------------------------------------------------------------


class TestSerializeForInvocation:
    def test_none_returns_none(self):
        from src.registry.metrics import serialize_for_invocation
        assert serialize_for_invocation(None) is None

    def test_empty_dict_returns_none(self):
        """Empty dicts carry no information — treat as None so the row
        fails the ``input_args IS NOT NULL`` filter and isn't used as
        a no-op replay probe."""
        from src.registry.metrics import serialize_for_invocation
        assert serialize_for_invocation({}) is None

    def test_populated_dict_round_trips(self):
        import json

        from src.registry.metrics import serialize_for_invocation

        value = {"b": 2, "a": 1}
        s = serialize_for_invocation(value)
        assert s is not None
        # sort_keys=True → deterministic ordering for byte-equality compare
        assert s == '{"a": 1, "b": 2}'
        assert json.loads(s) == value

    def test_non_json_types_serialize_via_default_str(self):
        from pathlib import Path

        from src.registry.metrics import serialize_for_invocation

        s = serialize_for_invocation({"p": Path("/tmp/x.csv")})
        assert s is not None
        assert "/tmp/x.csv" in s

    def test_oversize_payload_truncates_to_valid_json_sentinel(self):
        """Security audit finding #16 (2026-04-20): oversize payloads
        return a valid-JSON sentinel, NOT a broken string literal. Pre-
        fix the function emitted ``f'{head}..."__TRUNCATED__"'`` which
        failed to parse — every downstream ``json.loads`` (Pillar 3
        coverage replay in particular) silently treated the case as
        unreproducible. Post-fix the sentinel parses to a dict carrying
        the original size so consumers can distinguish "truncated"
        from "bad data"."""
        import json

        from src.registry.metrics import serialize_for_invocation

        big = {"blob": "x" * 100_000}
        s = serialize_for_invocation(big)
        assert s is not None
        # CRITICAL: must be valid JSON so downstream json.loads works.
        parsed = json.loads(s)
        assert isinstance(parsed, dict)
        assert parsed["__truncated__"] is True
        assert parsed["__original_size_bytes__"] > 64 * 1024
        # Sentinel itself is small.
        assert len(s.encode("utf-8")) < 200

    def test_unserializable_returns_none(self):
        """Objects that json.dumps cannot encode even with default=str
        (e.g., circular references) must return None rather than raise."""
        from src.registry.metrics import serialize_for_invocation

        a: dict = {}
        a["self"] = a  # cyclic
        assert serialize_for_invocation(a) is None


class TestLooksLikeError:
    def test_error_key_detected(self):
        from src.registry.metrics import looks_like_error
        assert looks_like_error({"error": "oops"}) is True

    def test_exception_key_detected(self):
        from src.registry.metrics import looks_like_error
        assert looks_like_error({"exception": "RuntimeError"}) is True

    def test_traceback_key_detected(self):
        from src.registry.metrics import looks_like_error
        assert looks_like_error({"traceback": "..."}) is True

    def test_sandbox_error_key_detected(self):
        from src.registry.metrics import looks_like_error
        assert looks_like_error({"sandbox_error": "timeout"}) is True

    def test_error_count_not_an_error(self):
        """Legitimate keys containing ``error`` as substring (e.g.,
        ``error_count``, ``error_rate``) must NOT trigger the heuristic.
        Conservative keyset avoids this misfire."""
        from src.registry.metrics import looks_like_error
        assert looks_like_error({"error_count": 0}) is False
        assert looks_like_error({"error_rate": 0.01}) is False

    def test_success_dict_not_an_error(self):
        from src.registry.metrics import looks_like_error
        assert looks_like_error({"result": 42, "rows": 10}) is False

    def test_non_mapping_returns_false(self):
        from src.registry.metrics import looks_like_error
        assert looks_like_error(None) is False
        assert looks_like_error("error") is False
        assert looks_like_error(42) is False
        assert looks_like_error(["error"]) is False


class TestExtractErrorMessage:
    def test_error_key_extracted(self):
        from src.registry.metrics import extract_error_message
        assert extract_error_message({"error": "oops"}) == "oops"

    def test_prefers_error_over_exception(self):
        """When both ``error`` and ``exception`` keys are present, ``error``
        is returned first (iteration order of the frozenset is stable but
        not guaranteed; this test documents the extracted value must be
        one of the valid error keys)."""
        from src.registry.metrics import extract_error_message
        out = extract_error_message({"error": "A", "exception": "B"})
        assert out in ("A", "B")

    def test_long_message_truncated_to_500_chars(self):
        from src.registry.metrics import extract_error_message
        out = extract_error_message({"error": "x" * 1000})
        assert out is not None
        assert len(out) == 500

    def test_non_error_output_returns_none(self):
        from src.registry.metrics import extract_error_message
        assert extract_error_message({"result": 42}) is None
        assert extract_error_message(None) is None

    def test_none_error_value_returns_none(self):
        """``{"error": None}`` — the key exists but carries no message."""
        from src.registry.metrics import extract_error_message
        assert extract_error_message({"error": None}) is None

    def test_empty_string_error_returns_none(self):
        from src.registry.metrics import extract_error_message
        assert extract_error_message({"error": ""}) is None

    def test_non_string_error_value_coerced(self):
        from src.registry.metrics import extract_error_message
        out = extract_error_message({"error": {"code": 500}})
        assert out is not None
        assert "500" in out
