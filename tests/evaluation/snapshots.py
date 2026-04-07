"""Registry snapshot utilities for evaluation run isolation.

Strategy (per advisor doc):
- Take ONE clean snapshot before evaluation begins.
- Restore it at the START of every run (not per-task, not per-condition).
- Scope: ChromaDB persist dir + src/mcp_tools/ + .deepagents/skills/

The snapshot ID is a deterministic hash of the clean state contents.
All runs that start from the same clean slate share the same snap_id —
if a run has a different snap_id, restore logic broke.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

# Default snapshot storage location (within the test tree so it is gitignored)
_SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def take_clean_snapshot(
    chroma_dir: Path,
    mcp_tools_dir: Path,
    skills_dir: Path,
    *,
    snapshot_dir: Path = _SNAPSHOT_DIR,
) -> str:
    """Copy the three registry directories into a clean snapshot.

    Call ONCE before evaluation begins.  Returns the snapshot ID
    (same value for every run that starts from this state).

    The snapshot ID is written to ``meta.json`` inside the snapshot so that
    ``restore_clean_snapshot`` can verify integrity.
    """
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    for src, dst_name in [
        (chroma_dir, "chroma"),
        (mcp_tools_dir, "mcp_tools"),
        (skills_dir, "skills"),
    ]:
        dst = snapshot_dir / dst_name
        if dst.exists():
            shutil.rmtree(dst)
        if src.exists():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)

    snap_id = _hash_snapshot(snapshot_dir)
    (snapshot_dir / "meta.json").write_text(json.dumps({"id": snap_id}))
    return snap_id


def restore_clean_snapshot(
    chroma_dir: Path,
    mcp_tools_dir: Path,
    skills_dir: Path,
    *,
    snapshot_dir: Path = _SNAPSHOT_DIR,
) -> str:
    """Restore the clean snapshot into the three registry directories.

    Call at the START of every run, before processing Task 1.
    Returns the snapshot ID so callers can record it in EvaluationRecord.

    Raises FileNotFoundError if take_clean_snapshot was never called.
    """
    meta_path = snapshot_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(
            f"No clean snapshot found at {snapshot_dir}. "
            "Call take_clean_snapshot() before running evaluation."
        )

    snap_id: str = json.loads(meta_path.read_text())["id"]

    for dst, src_name in [
        (chroma_dir, "chroma"),
        (mcp_tools_dir, "mcp_tools"),
        (skills_dir, "skills"),
    ]:
        src = snapshot_dir / src_name
        if dst.exists():
            shutil.rmtree(dst)
        if src.exists():
            shutil.copytree(src, dst)
        else:
            dst.mkdir(parents=True, exist_ok=True)

    return snap_id


def get_snapshot_id(*, snapshot_dir: Path = _SNAPSHOT_DIR) -> str:
    """Return the ID of the existing clean snapshot without restoring."""
    meta_path = snapshot_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(
            "No clean snapshot found. Call take_clean_snapshot() first."
        )
    return json.loads(meta_path.read_text())["id"]


def snapshot_exists(*, snapshot_dir: Path = _SNAPSHOT_DIR) -> bool:
    """True if a clean snapshot has been taken."""
    return (snapshot_dir / "meta.json").exists()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _hash_snapshot(snap_path: Path) -> str:
    """SHA-256 of all file contents in the snapshot (excluding meta.json)."""
    h = hashlib.sha256()
    for f in sorted(snap_path.rglob("*")):
        if f.is_file() and f.name != "meta.json":
            h.update(f.read_bytes())
    return f"snap_{h.hexdigest()[:8]}"
