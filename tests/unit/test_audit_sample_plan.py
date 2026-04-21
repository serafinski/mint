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

ext12 #121 (2026-04-21): plan path now keyed on a SHA-256 hash of the
full scope payload (``run_ids`` / ``since`` / ``conditions`` / ``types``
/ ``per_stratum`` / ``seed``). Two invocations with different scope
produce distinct plan files. Zero-intersection resume raises
SystemExit(3) rather than silently collapsing to "Nothing to audit".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.audit_contamination import (  # noqa: E402
    AuditRow,
    _sample_plan_path,
    get_or_create_sample_plan,
)


_DEFAULT_SCOPE = {
    "run_ids": None,
    "since": None,
    "conditions": ["full"],
    "types": ["mcp_tool"],
    "per_stratum": 5,
    "seed": 42,
}


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
        **_DEFAULT_SCOPE,
    )
    assert len(sample) == 5

    plan_path = _sample_plan_path(db_path, **_DEFAULT_SCOPE)
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
        rows, db_path=db_path, **_DEFAULT_SCOPE,
    )
    # Second call with IDENTICAL pool — plan should be reloaded, not re-drawn
    # (even though re-drawing with the same seed would produce the same ids).
    second = get_or_create_sample_plan(
        rows, db_path=db_path, **_DEFAULT_SCOPE,
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
        rows, db_path=db_path, **_DEFAULT_SCOPE,
    )
    first_ids = {r.row_id for r in first}

    # Simulate labeling 2 of the 5 — pool (= unlabeled) now has 10 - 2 = 8 rows.
    labeled_ids = list(first_ids)[:2]
    shrunk_pool = [r for r in rows if r.row_id not in labeled_ids]

    second = get_or_create_sample_plan(
        shrunk_pool, db_path=db_path, **_DEFAULT_SCOPE,
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
    plan_path = _sample_plan_path(db_path, **_DEFAULT_SCOPE)
    plan_path.write_text("not valid json {")

    rows = [_row(f"r{i}") for i in range(10)]
    sample = get_or_create_sample_plan(
        rows, db_path=db_path, **_DEFAULT_SCOPE,
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
    p1 = _sample_plan_path(db1, **_DEFAULT_SCOPE)
    p2 = _sample_plan_path(db2, **_DEFAULT_SCOPE)
    assert p1 != p2
    assert p1.parent == db1.parent


# ---------------------------------------------------------------------------
# ext12 #121 regression — scope-hash plan key + zero-intersection loud-fail
# ---------------------------------------------------------------------------


def test_plan_path_differs_by_run_ids(tmp_path):
    """Two invocations with different ``--run-ids`` must NOT share a plan file.

    Pre-ext12 the plan filename was keyed on ``db_path.stem`` alone, so
    a plan persisted by ``--run-ids run_old`` was silently reloaded when
    the user re-invoked with ``--run-ids run_new`` — the intersection
    with the new pool was often 0 and the script exited with "Nothing
    to audit". This test pins the scope-hash behaviour.
    """
    db = tmp_path / "x.duckdb"
    p1 = _sample_plan_path(
        db, run_ids=["a"], since=None, conditions=["full"],
        types=["mcp_tool"], per_stratum=10, seed=42,
    )
    p2 = _sample_plan_path(
        db, run_ids=["b"], since=None, conditions=["full"],
        types=["mcp_tool"], per_stratum=10, seed=42,
    )
    assert p1 != p2


def test_plan_path_differs_by_per_stratum_and_seed(tmp_path):
    db = tmp_path / "x.duckdb"
    base = dict(run_ids=None, since=None, conditions=["full"], types=["mcp_tool"])
    p_10_42 = _sample_plan_path(db, per_stratum=10, seed=42, **base)
    p_20_42 = _sample_plan_path(db, per_stratum=20, seed=42, **base)
    p_10_99 = _sample_plan_path(db, per_stratum=10, seed=99, **base)
    assert len({p_10_42, p_20_42, p_10_99}) == 3


def test_plan_path_identical_for_same_scope(tmp_path):
    """Same inputs → same hash → same plan file. Resumes within a scope
    must share their plan.
    """
    db = tmp_path / "x.duckdb"
    scope = dict(
        run_ids=["a", "b"], since=None, conditions=["full"],
        types=["mcp_tool"], per_stratum=10, seed=42,
    )
    # Scope order shouldn't matter (lists are sorted inside _sample_plan_path).
    p1 = _sample_plan_path(db, **scope)
    p2 = _sample_plan_path(
        db,
        run_ids=["b", "a"], since=None, conditions=["full"],
        types=["mcp_tool"], per_stratum=10, seed=42,
    )
    assert p1 == p2


def test_zero_intersection_resume_raises_system_exit(tmp_path, capsys):
    """Defence-in-depth: even if the scope-hash key somehow misses (user
    manually renamed files, DB underneath changed), a resume whose plan
    has ZERO overlap with the current pool must loud-fail rather than
    silently return nothing.
    """
    db = tmp_path / "metrics.duckdb"
    db.touch()

    # Persist a plan containing ids that don't exist in the fresh pool.
    plan_path = _sample_plan_path(db, **_DEFAULT_SCOPE)
    plan_path.write_text(json.dumps(["ghost-1", "ghost-2", "ghost-3"]))

    rows = [_row(f"r{i}") for i in range(10)]
    with pytest.raises(SystemExit) as excinfo:
        get_or_create_sample_plan(rows, db_path=db, **_DEFAULT_SCOPE)
    assert excinfo.value.code == 3
    captured = capsys.readouterr()
    assert "ZERO" in captured.err or "zero" in captured.err.lower()


def test_force_fresh_plan_wipes_existing(tmp_path, capsys):
    """``--force-fresh-plan`` drops the plan file before sampling, so a
    scope change that the hash can't capture (e.g., manual rename) still
    has an escape hatch.
    """
    db = tmp_path / "metrics.duckdb"
    db.touch()
    rows = [_row(f"r{i}") for i in range(10)]

    # Seed an initial plan.
    first = get_or_create_sample_plan(rows, db_path=db, **_DEFAULT_SCOPE)
    plan_path = _sample_plan_path(db, **_DEFAULT_SCOPE)
    assert plan_path.exists()
    first_ids = {r.row_id for r in first}

    # Force fresh: plan file is deleted, then redrawn. With the same seed
    # + same pool, the redraw yields the same ids — but the important
    # invariant is that the delete happened (visible in output).
    second = get_or_create_sample_plan(
        rows, db_path=db, force_fresh_plan=True, **_DEFAULT_SCOPE,
    )
    captured = capsys.readouterr()
    assert "wiped" in captured.out.lower()
    assert len(second) == len(first)
    # Seed-deterministic: the ids match, but the plan file was re-created.
    second_ids = {r.row_id for r in second}
    assert first_ids == second_ids
