"""Unit tests for tests/evaluation/snapshots.py."""

from __future__ import annotations

import json

import pytest

from tests.evaluation.snapshots import (
    _hash_snapshot,
    restore_clean_snapshot,
    snapshot_exists,
    take_clean_snapshot,
)


@pytest.fixture
def tmp_dirs(tmp_path):
    chroma = tmp_path / "chroma"
    mcp_tools = tmp_path / "mcp_tools"
    skills = tmp_path / "skills"
    for d in (chroma, mcp_tools, skills):
        d.mkdir()
    (chroma / "collection.db").write_bytes(b"chroma_data")
    (mcp_tools / "tool_a.py").write_text("# tool a")
    (skills / "skill_x" / "SKILL.md").mkdir(parents=True, exist_ok=True)
    (skills / "skill_x" / "SKILL.md").rmdir()
    (skills / "skill_x").mkdir(parents=True, exist_ok=True)
    (skills / "skill_x" / "SKILL.md").write_text("---\nname: skill-x\n---\nbody")
    return chroma, mcp_tools, skills


class TestTakeSnapshot:
    def test_returns_snap_id(self, tmp_dirs, tmp_path):
        chroma, mcp_tools, skills = tmp_dirs
        snap_dir = tmp_path / "snapshots"
        snap_id = take_clean_snapshot(chroma, mcp_tools, skills, snapshot_dir=snap_dir)
        assert snap_id.startswith("snap_")
        assert len(snap_id) == 13  # "snap_" + 8 hex chars

    def test_meta_written(self, tmp_dirs, tmp_path):
        chroma, mcp_tools, skills = tmp_dirs
        snap_dir = tmp_path / "snapshots"
        snap_id = take_clean_snapshot(chroma, mcp_tools, skills, snapshot_dir=snap_dir)
        meta = json.loads((snap_dir / "clean" / "meta.json").read_text())
        assert meta["id"] == snap_id

    def test_files_copied(self, tmp_dirs, tmp_path):
        chroma, mcp_tools, skills = tmp_dirs
        snap_dir = tmp_path / "snapshots"
        take_clean_snapshot(chroma, mcp_tools, skills, snapshot_dir=snap_dir)
        assert (snap_dir / "clean" / "chroma" / "collection.db").exists()
        assert (snap_dir / "clean" / "mcp_tools" / "tool_a.py").exists()
        assert (snap_dir / "clean" / "skills" / "skill_x" / "SKILL.md").exists()

    def test_idempotent(self, tmp_dirs, tmp_path):
        chroma, mcp_tools, skills = tmp_dirs
        snap_dir = tmp_path / "snapshots"
        id1 = take_clean_snapshot(chroma, mcp_tools, skills, snapshot_dir=snap_dir)
        id2 = take_clean_snapshot(chroma, mcp_tools, skills, snapshot_dir=snap_dir)
        assert id1 == id2


class TestRestoreSnapshot:
    def test_restores_files(self, tmp_dirs, tmp_path):
        chroma, mcp_tools, skills = tmp_dirs
        snap_dir = tmp_path / "snapshots"
        take_clean_snapshot(chroma, mcp_tools, skills, snapshot_dir=snap_dir)

        # Simulate dirty state
        (mcp_tools / "dirty_tool.py").write_text("# contaminated")

        restore_clean_snapshot(chroma, mcp_tools, skills, snapshot_dir=snap_dir)
        assert not (mcp_tools / "dirty_tool.py").exists()
        assert (mcp_tools / "tool_a.py").exists()

    def test_returns_same_snap_id(self, tmp_dirs, tmp_path):
        chroma, mcp_tools, skills = tmp_dirs
        snap_dir = tmp_path / "snapshots"
        snap_id = take_clean_snapshot(chroma, mcp_tools, skills, snapshot_dir=snap_dir)
        restored_id = restore_clean_snapshot(chroma, mcp_tools, skills, snapshot_dir=snap_dir)
        assert restored_id == snap_id

    def test_raises_if_no_snapshot(self, tmp_dirs, tmp_path):
        chroma, mcp_tools, skills = tmp_dirs
        snap_dir = tmp_path / "no_snapshot_here"
        with pytest.raises(FileNotFoundError):
            restore_clean_snapshot(chroma, mcp_tools, skills, snapshot_dir=snap_dir)


class TestSnapshotExists:
    def test_false_before_taking(self, tmp_path):
        assert not snapshot_exists(snapshot_dir=tmp_path / "snapshots")

    def test_true_after_taking(self, tmp_dirs, tmp_path):
        chroma, mcp_tools, skills = tmp_dirs
        snap_dir = tmp_path / "snapshots"
        take_clean_snapshot(chroma, mcp_tools, skills, snapshot_dir=snap_dir)
        assert snapshot_exists(snapshot_dir=snap_dir)


class TestHashSnapshot:
    def test_deterministic(self, tmp_path):
        d = tmp_path / "snap"
        d.mkdir()
        (d / "file.txt").write_bytes(b"hello")
        h1 = _hash_snapshot(d)
        h2 = _hash_snapshot(d)
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path):
        d1 = tmp_path / "s1"
        d1.mkdir()
        (d1 / "a.txt").write_bytes(b"hello")
        d2 = tmp_path / "s2"
        d2.mkdir()
        (d2 / "a.txt").write_bytes(b"world")
        assert _hash_snapshot(d1) != _hash_snapshot(d2)

    def test_meta_json_excluded(self, tmp_path):
        d = tmp_path / "snap"
        d.mkdir()
        (d / "file.txt").write_bytes(b"data")
        h1 = _hash_snapshot(d)
        (d / "meta.json").write_text('{"id": "snap_xyz"}')
        h2 = _hash_snapshot(d)
        assert h1 == h2
