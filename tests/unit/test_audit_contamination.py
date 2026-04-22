"""Unit tests for scripts/audit_contamination.py + M2 analyzer (Step 18.10)."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from scripts.analyze_results import (  # noqa: E402
    _clopper_pearson_ci,
    print_contamination_rate,
)
from scripts.audit_contamination import (  # noqa: E402
    AuditRow,
    fetch_unlabeled_rows,
    stratified_sample,
)
from src.registry.evaluation_store import EvaluationStore  # noqa: E402
from src.schemas.evaluation import EvaluationRecord  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_eval_run(
    store: EvaluationStore,
    *,
    run_id: str = "r1",
    condition: str = "full",
    task_id: str = "T04",
    capability_type: str = "agent",
    is_pilot: bool = False,
    human_label: str | None = None,
    generated_artifact_count: int = 1,
) -> str:
    rec = EvaluationRecord(
        run_id=run_id,
        condition=condition,
        task_id=task_id,
        task_tier="moderate",
        attempt_count=1,
        capability_type=capability_type,
        gate_passed=True,
        output_in_range=False,
        token_cost=100,
        attempt_latency_ms=1000,
        failure_reason="output_rejected",
        registry_snapshot_id="snap",
        seed=42,
        is_pilot=is_pilot,
        human_label=human_label,
        generated_artifact_count=generated_artifact_count,
    )
    return store.record_run(rec)


# ---------------------------------------------------------------------------
# fetch_unlabeled_rows
# ---------------------------------------------------------------------------


class TestFetchUnlabeledRows:
    def test_excludes_already_labeled_rows(self):
        store = EvaluationStore()
        _seed_eval_run(store, run_id="r1", human_label="correct")
        _seed_eval_run(store, run_id="r2", human_label=None)
        rows = fetch_unlabeled_rows(
            store._conn,  # noqa: SLF001
            conditions=["full"],
            types=["agent"],
        )
        assert len(rows) == 1
        assert rows[0].run_id == "r2"

    def test_excludes_pilot_rows(self):
        store = EvaluationStore()
        _seed_eval_run(store, run_id="pilot", is_pilot=True)
        _seed_eval_run(store, run_id="real", is_pilot=False)
        rows = fetch_unlabeled_rows(
            store._conn, conditions=["full"], types=["agent"]
        )
        assert {r.run_id for r in rows} == {"real"}

    def test_excludes_runs_with_zero_generated_artifacts(self):
        """Registry-matched tasks (T01-T03) produce 0 generated → nothing to audit."""
        store = EvaluationStore()
        _seed_eval_run(store, run_id="r1", generated_artifact_count=0)
        _seed_eval_run(store, run_id="r2", generated_artifact_count=1)
        rows = fetch_unlabeled_rows(
            store._conn, conditions=["full"], types=["agent"]
        )
        assert {r.run_id for r in rows} == {"r2"}

    def test_filters_by_condition(self):
        store = EvaluationStore()
        _seed_eval_run(store, run_id="a", condition="full")
        _seed_eval_run(store, run_id="b", condition="no_verification")
        _seed_eval_run(store, run_id="c", condition="static")
        rows = fetch_unlabeled_rows(
            store._conn,
            conditions=["full", "no_verification"],
            types=["agent"],
        )
        assert {r.run_id for r in rows} == {"a", "b"}

    def test_filters_by_type(self):
        store = EvaluationStore()
        _seed_eval_run(store, run_id="tool", capability_type="mcp_tool")
        _seed_eval_run(store, run_id="skill", capability_type="skill")
        _seed_eval_run(store, run_id="agent", capability_type="agent")
        rows = fetch_unlabeled_rows(
            store._conn, conditions=["full"], types=["mcp_tool", "skill"]
        )
        assert {r.run_id for r in rows} == {"tool", "skill"}


# ---------------------------------------------------------------------------
# stratified_sample
# ---------------------------------------------------------------------------


class TestStratifiedSample:
    def _pool(self):
        # 12 rows: 2 conditions × 3 types × 2 rows per stratum
        pool = []
        for condition in ("full", "no_verification"):
            for cap_type in ("mcp_tool", "skill", "agent"):
                for i in range(2):
                    pool.append(
                        AuditRow(
                            row_id=f"{condition}-{cap_type}-{i}",
                            run_id="r", condition=condition,
                            task_id="T", capability_type=cap_type,
                        )
                    )
        return pool

    def test_picks_target_from_each_stratum(self):
        sample = stratified_sample(
            self._pool(),
            conditions=["full", "no_verification"],
            types=["mcp_tool", "skill", "agent"],
            per_stratum=1, seed=42,
        )
        assert len(sample) == 6  # 2 conditions × 3 types × 1
        # every (condition, type) pair present exactly once
        pairs = {(r.condition, r.capability_type) for r in sample}
        assert len(pairs) == 6

    def test_deterministic_for_fixed_seed(self):
        s1 = stratified_sample(
            self._pool(),
            conditions=["full"], types=["agent"],
            per_stratum=2, seed=42,
        )
        s2 = stratified_sample(
            self._pool(),
            conditions=["full"], types=["agent"],
            per_stratum=2, seed=42,
        )
        assert [r.row_id for r in s1] == [r.row_id for r in s2]

    def test_different_seed_different_order(self):
        """Sanity — different seed yields different order (given >1 row in stratum)."""
        pool = []
        for i in range(10):
            pool.append(
                AuditRow(
                    row_id=f"x{i}", run_id="r", condition="full",
                    task_id="T", capability_type="agent",
                )
            )
        s1 = stratified_sample(pool, conditions=["full"], types=["agent"],
                               per_stratum=5, seed=42)
        s2 = stratified_sample(pool, conditions=["full"], types=["agent"],
                               per_stratum=5, seed=123)
        # With 10 rows and seed 42 vs 123, first-5 orders should differ
        assert [r.row_id for r in s1] != [r.row_id for r in s2]

    def test_stratum_smaller_than_target_returns_all_available(self):
        sample = stratified_sample(
            self._pool(),
            conditions=["full"], types=["agent"],
            per_stratum=100, seed=42,  # only 2 agents in full stratum
        )
        assert len(sample) == 2


# ---------------------------------------------------------------------------
# EvaluationStore.update_human_label
# ---------------------------------------------------------------------------


class TestUpdateHumanLabel:
    def test_round_trip_insert_update_read(self):
        store = EvaluationStore()
        rid = _seed_eval_run(store)
        assert store.update_human_label(rid, "contaminated") is True
        got = store.get_runs()
        assert len(got) == 1
        assert got[0].human_label == "contaminated"

    def test_update_to_different_label_overwrites(self):
        store = EvaluationStore()
        rid = _seed_eval_run(store)
        store.update_human_label(rid, "correct")
        store.update_human_label(rid, "partial")
        got = store.get_runs()
        assert got[0].human_label == "partial"

    def test_update_to_none_clears_label(self):
        store = EvaluationStore()
        rid = _seed_eval_run(store, human_label="contaminated")
        store.update_human_label(rid, None)
        got = store.get_runs()
        assert got[0].human_label is None

    def test_invalid_label_raises_valueerror(self):
        store = EvaluationStore()
        rid = _seed_eval_run(store)
        with pytest.raises(ValueError, match="label must be"):
            store.update_human_label(rid, "NOT_A_REAL_LABEL")

    def test_unknown_row_id_returns_false(self):
        store = EvaluationStore()
        _seed_eval_run(store)
        assert store.update_human_label("nonexistent-uuid", "correct") is False


# ---------------------------------------------------------------------------
# Clopper-Pearson CI
# ---------------------------------------------------------------------------


class TestClopperPearsonCI:
    def test_n_zero_returns_zero_zero(self):
        assert _clopper_pearson_ci(0, 0) == (0.0, 0.0)

    def test_zero_successes_lower_is_zero(self):
        lo, hi = _clopper_pearson_ci(0, 10)
        assert lo == 0.0
        assert 0.0 < hi < 1.0

    def test_all_successes_upper_is_one(self):
        lo, hi = _clopper_pearson_ci(10, 10)
        assert 0.0 < lo < 1.0
        assert hi == 1.0

    def test_interval_contains_point_estimate(self):
        # p = 3/10 = 0.3. Clopper-Pearson CI should contain it.
        lo, hi = _clopper_pearson_ci(3, 10)
        assert lo < 0.3 < hi

    def test_larger_sample_narrows_interval(self):
        _, hi_small = _clopper_pearson_ci(3, 10)
        _, hi_big = _clopper_pearson_ci(30, 100)  # same point estimate
        assert hi_big < hi_small


# ---------------------------------------------------------------------------
# print_contamination_rate
# ---------------------------------------------------------------------------


class TestPrintContaminationRate:
    def _capture(self, conn):
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_contamination_rate(conn)
        return buf.getvalue()

    def test_empty_db_prints_audit_not_performed(self):
        store = EvaluationStore()
        output = self._capture(store._conn)
        assert "Audit not performed" in output

    def test_reports_per_condition_counts(self):
        store = EvaluationStore()
        _seed_eval_run(store, run_id="a", condition="full", human_label="correct")
        _seed_eval_run(store, run_id="b", condition="full", human_label="correct")
        _seed_eval_run(store, run_id="c", condition="full", human_label="contaminated")
        _seed_eval_run(store, run_id="d", condition="no_verification", human_label="partial")
        output = self._capture(store._conn)
        # Full: 3 sampled, 1 contaminated → 33.3%
        assert "33.3" in output
        # no_verification: 1 sampled, 0 contaminated → 0%
        assert "0.0" in output

    def test_ci_bounds_present_in_output(self):
        store = EvaluationStore()
        for _ in range(10):
            _seed_eval_run(store, run_id=f"r{_}", human_label="correct")
        output = self._capture(store._conn)
        assert "95% CI" in output or "[" in output  # CI bracket rendered

    def test_pooled_row_only_when_multiple_conditions(self):
        store = EvaluationStore()
        _seed_eval_run(store, run_id="a", condition="full", human_label="correct")
        _seed_eval_run(store, run_id="b", condition="full", human_label="contaminated")
        output = self._capture(store._conn)
        # Single condition — no pooled row
        assert "Pooled" not in output

    def test_excludes_pilot_labels(self):
        store = EvaluationStore()
        _seed_eval_run(store, run_id="pilot", is_pilot=True, human_label="contaminated")
        output = self._capture(store._conn)
        # Pilot's contamination label must not surface.
        assert "Audit not performed" in output


# ---------------------------------------------------------------------------
# Step 18.10 Option B — DB-snapshot audit path
# ---------------------------------------------------------------------------


class TestDBSnapshotAuditPath:
    """Audit tool reads source from evaluation_runs.generated_artifact_sources
    rather than looking it up in the registry (which snapshot isolation
    wipes between runs)."""

    def test_fetch_unlabeled_includes_db_snapshots(self):
        from src.schemas.evaluation import (
            EvaluationRecord,
            GeneratedArtifactSnapshot,
        )
        from src.schemas.capability import CapabilityType

        store = EvaluationStore()
        snap = GeneratedArtifactSnapshot(
            capability_name="parse_csv_ab12",
            capability_type=CapabilityType.MCP_TOOL,
            source_body="from mcp.server.fastmcp import FastMCP\n@mcp.tool()\nasync def parse():\n    ...",
            domain="data-processing",
        )
        store.record_run(EvaluationRecord(
            run_id="r1", condition="full", task_id="T04", task_tier="moderate",
            attempt_count=1, capability_type="mcp_tool",
            gate_passed=True, output_in_range=False,
            token_cost=100, attempt_latency_ms=1000,
            failure_reason="output_rejected",
            registry_snapshot_id="s", seed=42,
            generated_artifact_count=1,
            generated_artifact_sources=[snap],
        ))
        rows = fetch_unlabeled_rows(
            store._conn,  # noqa: SLF001
            conditions=["full"], types=["mcp_tool"],
        )
        assert len(rows) == 1
        assert len(rows[0].source_snapshots) == 1
        assert rows[0].source_snapshots[0]["capability_name"] == "parse_csv_ab12"
        # Source body survives the round-trip intact.
        assert "from mcp.server.fastmcp" in rows[0].source_snapshots[0]["source_body"]

    def test_fetch_returns_empty_tuple_when_db_has_no_snapshot(self):
        """Legacy rows without the column populated get an empty tuple —
        audit tool falls back to registry lookup in that case."""
        store = EvaluationStore()
        _seed_eval_run(store, run_id="legacy")
        rows = fetch_unlabeled_rows(
            store._conn, conditions=["full"], types=["agent"]
        )
        assert len(rows) == 1
        assert rows[0].source_snapshots == ()

    def test_multiple_snapshots_preserved(self):
        """Compositional chain runs produce N artifacts; all must persist."""
        from src.schemas.evaluation import (
            EvaluationRecord,
            GeneratedArtifactSnapshot,
        )
        from src.schemas.capability import CapabilityType

        store = EvaluationStore()
        snaps = [
            GeneratedArtifactSnapshot(
                capability_name=f"cap_{i}",
                capability_type=CapabilityType.SKILL,
                source_body=f"skill content {i}",
            )
            for i in range(3)
        ]
        store.record_run(EvaluationRecord(
            run_id="r2", condition="full", task_id="T04", task_tier="moderate",
            attempt_count=1, capability_type="skill",
            gate_passed=True, output_in_range=False,
            token_cost=100, attempt_latency_ms=1000,
            failure_reason="output_rejected",
            registry_snapshot_id="s", seed=42,
            generated_artifact_count=3,
            generated_artifact_sources=snaps,
        ))
        rows = fetch_unlabeled_rows(
            store._conn, conditions=["full"], types=["skill"]
        )
        assert len(rows[0].source_snapshots) == 3
        assert [s["capability_name"] for s in rows[0].source_snapshots] == [
            "cap_0", "cap_1", "cap_2"
        ]


def test_harness_snapshot_artifacts_helper_mcp_tool():
    """_snapshot_artifacts extracts source_code for MCP_TOOL."""
    from src.schemas.capability import CapabilityType
    from src.schemas.task import GeneratedArtifact
    from tests.evaluation.harness import _snapshot_artifacts

    art = GeneratedArtifact(
        capability_name="tool_x",
        capability_type=CapabilityType.MCP_TOOL,
        source_code="def foo(): return 42",
        domain="test",
    )
    snaps = _snapshot_artifacts([art])
    assert len(snaps) == 1
    assert snaps[0].source_body == "def foo(): return 42"
    assert snaps[0].capability_type == CapabilityType.MCP_TOOL


def test_harness_snapshot_artifacts_helper_skill():
    from src.schemas.capability import CapabilityType
    from src.schemas.task import GeneratedArtifact
    from tests.evaluation.harness import _snapshot_artifacts

    art = GeneratedArtifact(
        capability_name="skill_x",
        capability_type=CapabilityType.SKILL,
        skill_content="---\nname: x\n---\n# Body",
    )
    snaps = _snapshot_artifacts([art])
    assert snaps[0].source_body == "---\nname: x\n---\n# Body"


def test_harness_snapshot_artifacts_helper_agent():
    """_snapshot_artifacts serializes agent_config to JSON string."""
    import json as _json
    from src.schemas.capability import CapabilityType
    from src.schemas.task import GeneratedArtifact
    from tests.evaluation.harness import _snapshot_artifacts

    art = GeneratedArtifact(
        capability_name="agent_x",
        capability_type=CapabilityType.AGENT,
        agent_config={"system_prompt": "You are X", "subagents": []},
    )
    snaps = _snapshot_artifacts([art])
    decoded = _json.loads(snaps[0].source_body)
    assert decoded["system_prompt"] == "You are X"


def test_harness_snapshot_artifacts_empty_list_returns_empty():
    from tests.evaluation.harness import _snapshot_artifacts
    assert _snapshot_artifacts([]) == []
    assert _snapshot_artifacts(None) == []
