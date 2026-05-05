"""Unit coverage for the supplemental recurrence evaluation track.

These tests guard the Stage 3 condition matrix, recurrence fixture generation,
task-instance construction, validator picklability, and persistence of
recurrence identity fields.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import replace

import pytest

from src.registry.evaluation_store import EvaluationStore
from src.schemas.evaluation import EvaluationRecord, FailureReason
from tests.evaluation.conditions import get_condition_profile
from tests.evaluation.fixtures import generate_recurrence
from tests.evaluation.recurrence_tasks import build_recurrence_tasks
from tests.evaluation.tasks import get_task


def test_condition_profiles_match_eval_protocol():
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
    assert no_verification.optimizer_config.prune == "auto"
    assert no_verification.optimizer_config.split == "auto"
    assert no_verification.optimizer_config.merge == "auto"

    assert no_optimization.jit_enabled is True
    assert no_optimization.verification_enabled is True
    assert no_optimization.optimizer_config.prune == "off"
    assert no_optimization.optimizer_config.split == "off"
    assert no_optimization.optimizer_config.merge == "off"

    assert static.jit_enabled is False
    assert static.verification_enabled is False
    assert static.optimizer_config.prune == "off"
    assert static.optimizer_config.split == "off"
    assert static.optimizer_config.merge == "off"


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


def test_recurrence_tasks_use_picklable_validators(tmp_path, monkeypatch):
    root = tmp_path
    manifest_path = root / "tests" / "evaluation" / "fixtures_recurrence" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_dir = root / "tests" / "evaluation" / "fixtures_recurrence" / "T11" / "v01"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    for name in ("t11_sales.csv", "t11_products.xlsx", "t11_targets.csv"):
        (fixture_dir / name).write_text("stub")

    manifest_path.write_text(json.dumps([
        {
            "task_family_id": "T11",
            "task_instance_id": "T11.v01",
            "variant_id": "v01",
            "benchmark_track": "recurrence",
            "seed": 123,
            "fixture_files": [
                "tests/evaluation/fixtures_recurrence/T11/v01/t11_sales.csv",
                "tests/evaluation/fixtures_recurrence/T11/v01/t11_products.xlsx",
                "tests/evaluation/fixtures_recurrence/T11/v01/t11_targets.csv",
            ],
            "expected_values": {
                "summary_row_count": 4,
                "region_revenues": {"North": 100.0},
                "regions_meeting_target": 1,
                "revenue_tolerance_pct": 20,
            },
        }
    ], indent=2))

    import tests.evaluation.recurrence_tasks as recurrence_tasks

    monkeypatch.setattr(recurrence_tasks, "ROOT", root)
    monkeypatch.setattr(recurrence_tasks, "RECURRENCE_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(recurrence_tasks, "_rewrite_prompt", lambda task, fps: task.task_prompt)

    task = build_recurrence_tasks(task_ids=["T11"])[0]

    pickle.dumps(task)
    pickle.dumps(task.validator)
    assert task.file_validator is not None
    pickle.dumps(task.file_validator)


@pytest.mark.parametrize("task_id", ["T11", "T13", "T14", "T15"])
def test_build_recurrence_tasks_rebinds_fixture_roles_by_filename(
    tmp_path, monkeypatch, task_id
):
    root = tmp_path
    manifest_path = root / "tests" / "evaluation" / "fixtures_recurrence" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    import tests.evaluation.recurrence_tasks as recurrence_tasks

    source_root = recurrence_tasks.ROOT
    canonical = get_task(task_id)
    canonical_fixture_paths = [
        root / fp.relative_to(source_root)
        for fp in canonical.fixture_paths
    ]
    patched_task = replace(canonical, fixture_paths=canonical_fixture_paths)

    fixture_dir = root / "tests" / "evaluation" / "fixtures_recurrence" / task_id / "v01"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    recurrence_fixture_paths = []
    for name in sorted(fp.name for fp in canonical_fixture_paths):
        path = fixture_dir / name
        path.write_text("stub")
        recurrence_fixture_paths.append(path)

    expected_values_by_task = {
        "T11": {
            "summary_row_count": 4,
            "region_revenues": {"North": 100.0},
            "regions_meeting_target": 1,
            "revenue_tolerance_pct": 20,
        },
        "T13": {"anomaly_count": 5},
        "T14": {
            "department_count": 5,
            "department_spending": {"Engineering": 100.0},
            "over_budget_count": 1,
            "flagged_transactions": 2,
            "spending_tolerance_pct": 20,
        },
        "T15": {
            "n_days": 7,
            "metrics_aggregated": 4,
            "alert_days": 3,
        },
    }
    manifest_path.write_text(json.dumps([
        {
            "task_family_id": task_id,
            "task_instance_id": f"{task_id}.v01",
            "variant_id": "v01",
            "benchmark_track": "recurrence",
            "seed": 123,
            "fixture_files": [
                str(path.relative_to(root))
                for path in recurrence_fixture_paths
            ],
            "expected_values": expected_values_by_task[task_id],
        }
    ], indent=2))

    monkeypatch.setattr(recurrence_tasks, "ROOT", root)
    monkeypatch.setattr(recurrence_tasks, "RECURRENCE_MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(recurrence_tasks, "get_task", lambda _task_id: patched_task)

    task = build_recurrence_tasks(task_ids=[task_id])[0]

    assert [fp.name for fp in task.fixture_paths] == [
        fp.name for fp in canonical_fixture_paths
    ]

    for old_fp, rebound_fp in zip(canonical_fixture_paths, task.fixture_paths, strict=False):
        old_rel = str(old_fp.relative_to(root))
        new_rel = str(rebound_fp.relative_to(root))
        assert old_rel not in task.task_prompt
        assert new_rel in task.task_prompt
