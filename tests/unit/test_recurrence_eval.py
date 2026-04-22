from __future__ import annotations

import json
from pathlib import Path

from src.registry.evaluation_store import EvaluationStore
from src.schemas.evaluation import EvaluationRecord, FailureReason
from tests.evaluation.conditions import get_condition_profile
from tests.evaluation.fixtures import generate_recurrence
from tests.evaluation.recurrence_tasks import build_recurrence_tasks


def test_condition_profiles_are_proposal_aligned():
    full = get_condition_profile("full")
    no_verification = get_condition_profile("no_verification")
    no_optimization = get_condition_profile("no_optimization")
    static = get_condition_profile("static")

    assert full.jit_enabled is True
    assert full.verification_enabled is True
    assert full.optimizer_config.prune == "auto"
    assert full.optimizer_config.split == "auto"
    assert full.optimizer_config.merge == "auto"

    assert no_verification.jit_enabled is True
    assert no_verification.verification_enabled is False
    assert no_verification.optimizer_enabled is True

    assert no_optimization.jit_enabled is True
    assert no_optimization.verification_enabled is True
    assert no_optimization.optimizer_enabled is False

    assert static.jit_enabled is False


def test_evaluation_store_roundtrips_variant_identity(tmp_path):
    store = EvaluationStore(db_path=str(tmp_path / "eval.duckdb"))
    record = EvaluationRecord(
        run_id="run-x",
        condition="full",
        task_id="T11",
        task_family_id="T11",
        task_instance_id="T11.v02",
        variant_id="v02",
        benchmark_track="recurrence",
        task_tier="complex",
        attempt_count=1,
        capability_type="mcp_tool",
        verification_tier_reached=3,
        gate_passed=True,
        output_in_range=True,
        token_cost=0,
        attempt_latency_ms=100,
        failure_reason=FailureReason.NONE,
        registry_snapshot_id="snap-1",
        invocation_record_id=None,
        seed=42,
        is_pilot=False,
    )
    store.record_run(record)

    rows = store.get_runs(task_id="T11")
    assert len(rows) == 1
    row = rows[0]
    assert row.task_family_id == "T11"
    assert row.task_instance_id == "T11.v02"
    assert row.variant_id == "v02"
    assert row.benchmark_track == "recurrence"

    store.record_invocation_events(
        run_id="run-x",
        condition="full",
        task_id="T11",
        task_family_id="T11",
        task_instance_id="T11.v02",
        variant_id="v02",
        benchmark_track="recurrence",
        events=[{"capability_id": "cap-1", "success": True}],
    )
    stored = store._conn.execute(  # noqa: SLF001
        """
        SELECT task_family_id, task_instance_id, variant_id, benchmark_track
        FROM invocation_events
        """
    ).fetchone()
    assert stored == ("T11", "T11.v02", "v02", "recurrence")
    store.close()


def test_generate_recurrence_manifest_for_selected_tasks(tmp_path, monkeypatch):
    root = tmp_path
    fixtures_root = root / "tests" / "evaluation" / "fixtures_recurrence"
    monkeypatch.setattr(generate_recurrence, "ROOT", root)

    rows = generate_recurrence.generate_recurrence_fixtures(
        task_ids=["T04", "T20"],
        variants_per_task=1,
        base_seed=99,
        fixtures_root=fixtures_root,
    )
    assert len(rows) == 2
    manifest = json.loads((fixtures_root / "manifest.json").read_text())
    assert {row["task_family_id"] for row in manifest} == {"T04", "T20"}
    assert all(row["variant_id"] == "v01" for row in manifest)


def test_build_recurrence_tasks_from_manifest(tmp_path, monkeypatch):
    root = tmp_path
    manifest_path = root / "tests" / "evaluation" / "fixtures_recurrence" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_dir = root / "tests" / "evaluation" / "fixtures_recurrence" / "T04" / "v01"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    for name in ("t04_inventory.csv", "t04_reorder_rules.yaml"):
        (fixture_dir / name).write_text("stub")

    manifest_path.write_text(json.dumps([
        {
            "task_family_id": "T04",
            "task_instance_id": "T04.v01",
            "variant_id": "v01",
            "benchmark_track": "recurrence",
            "seed": 123,
            "fixture_files": [
                "tests/evaluation/fixtures_recurrence/T04/v01/t04_inventory.csv",
                "tests/evaluation/fixtures_recurrence/T04/v01/t04_reorder_rules.yaml",
            ],
            "expected_values": {
                "product_count": 10,
                "items_below_threshold": 4,
                "total_reorder_cost": 12345.0,
            },
        }
    ], indent=2))

    import tests.evaluation.recurrence_tasks as recurrence_tasks

    monkeypatch.setattr(recurrence_tasks, "ROOT", root)
    monkeypatch.setattr(recurrence_tasks, "RECURRENCE_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(recurrence_tasks, "_rewrite_prompt", lambda task, fps: task.task_prompt)

    tasks = build_recurrence_tasks(task_ids=["T04"])
    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_id == "T04"
    assert task.family_id == "T04"
    assert task.instance_id == "T04.v01"
    assert task.variant_id == "v01"
    assert task.benchmark_track == "recurrence"
    assert task.fixture_paths[0] == fixture_dir / "t04_inventory.csv"
