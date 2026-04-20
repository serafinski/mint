"""ext8 #57 — audit_contamination sample plan pre-commit.

Chapter-6 M2 ``n_sampled / n_contaminated`` is reported with a
Clopper-Pearson CI that formally assumes the labeled set is a single
pre-committed uniform random sample. Pre-ext8 the audit tool re-drew
via ``stratified_sample`` on every resume against the shrinking pool of
unlabeled rows — the union-of-per-session samples doesn't satisfy the
CI's assumption.

Post-fix: ``get_or_create_sample_plan`` persists the first-draw row ids
to a sidecar file next to the DB; resumes read the plan and return the
subset of its rows that are still unlabeled.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.audit_contamination import (  # noqa: E402
    AuditRow,
    _sample_plan_path,
    get_or_create_sample_plan,
)


def _row(rid: str, condition: str = "full", cap_type: str = "mcp_tool") -> AuditRow:
    return AuditRow(
        row_id=rid,
        run_id=f"run-{rid}",
        task_id="T01",
        condition=condition,
        capability_type=cap_type,
    )


def test_first_call_persists_a_plan_file(tmp_path):
    """First invocation draws + persists ids to disk."""
    db_path = tmp_path / "metrics.duckdb"
    db_path.touch()
    rows = [_row(f"r{i}") for i in range(10)]

    sample = get_or_create_sample_plan(
        rows,
        db_path=db_path,
        conditions=["full"],
        types=["mcp_tool"],
        per_stratum=5,
        seed=42,
    )
    assert len(sample) == 5

    plan_path = _sample_plan_path(db_path)
    assert plan_path.exists()
    persisted = json.loads(plan_path.read_text())
    assert set(persisted) == {r.row_id for r in sample}


def test_resume_returns_same_ids_not_a_fresh_draw(tmp_path):
    """Second call against the same DB reads the plan; ids must match
    the first draw even when the pool is identical.
    """
    db_path = tmp_path / "metrics.duckdb"
    db_path.touch()
    rows = [_row(f"r{i}") for i in range(10)]

    first = get_or_create_sample_plan(
        rows, db_path=db_path, conditions=["full"],
        types=["mcp_tool"], per_stratum=5, seed=42,
    )
    # Second call with IDENTICAL pool — plan should be reloaded, not re-drawn
    # (even though re-drawing with the same seed would produce the same ids).
    second = get_or_create_sample_plan(
        rows, db_path=db_path, conditions=["full"],
        types=["mcp_tool"], per_stratum=5, seed=42,
    )
    assert {r.row_id for r in first} == {r.row_id for r in second}


def test_resume_with_shrunk_pool_returns_plan_intersection(tmp_path):
    """After some rows are labeled (removed from the pool), the plan-based
    resume returns only the plan's ids that remain unlabeled — NOT a fresh
    sample from the shrunk pool (which would violate CI assumptions).
    """
    db_path = tmp_path / "metrics.duckdb"
    db_path.touch()
    rows = [_row(f"r{i}") for i in range(10)]

    first = get_or_create_sample_plan(
        rows, db_path=db_path, conditions=["full"],
        types=["mcp_tool"], per_stratum=5, seed=42,
    )
    first_ids = {r.row_id for r in first}

    # Simulate labeling 2 of the 5 — pool (= unlabeled) now has 10 - 2 = 8 rows.
    labeled_ids = list(first_ids)[:2]
    shrunk_pool = [r for r in rows if r.row_id not in labeled_ids]

    second = get_or_create_sample_plan(
        shrunk_pool, db_path=db_path, conditions=["full"],
        types=["mcp_tool"], per_stratum=5, seed=42,
    )
    second_ids = {r.row_id for r in second}
    # The resume returns the plan minus the just-labeled rows (= 3 rows).
    assert second_ids == first_ids - set(labeled_ids)
    # Specifically NOT a fresh 5-row sample from the 8-row pool.
    assert len(second) == 3


def test_corrupt_plan_file_falls_back_with_warning(tmp_path, capsys):
    """An unreadable plan file triggers a WARN + fresh draw. The fix
    prioritizes making progress + loud-signaling rather than crashing.
    """
    db_path = tmp_path / "metrics.duckdb"
    db_path.touch()
    plan_path = _sample_plan_path(db_path)
    plan_path.write_text("not valid json {")

    rows = [_row(f"r{i}") for i in range(10)]
    sample = get_or_create_sample_plan(
        rows, db_path=db_path, conditions=["full"],
        types=["mcp_tool"], per_stratum=5, seed=42,
    )
    assert len(sample) == 5
    captured = capsys.readouterr()
    assert "WARNING" in captured.err or "WARNING" in captured.out
    assert "plan" in captured.err.lower() or "plan" in captured.out.lower()


def test_plan_path_is_db_scoped(tmp_path):
    """Plan lives alongside the DB with a DB-stem prefix so multiple
    campaigns in the same dir don't cross-pollute.
    """
    db1 = tmp_path / "campaign_a.duckdb"
    db2 = tmp_path / "campaign_b.duckdb"
    assert _sample_plan_path(db1) != _sample_plan_path(db2)
    assert _sample_plan_path(db1).parent == db1.parent
