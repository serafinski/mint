"""Tests for ext7 #51 — eval-registry advisory lock.

Prevents concurrent invocation of ``run_pilot.py`` + ``run_longitudinal.py``
against shared ``data/eval/`` registry dirs, which pre-fix corrupted state
(one runner's ``rmtree`` wipes the other's active registry; snapshot-hash
mismatches raise RuntimeError mid-run).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts._eval_registry_lock import acquire_runner_lock  # noqa: E402


def test_first_runner_acquires_lock(tmp_path):
    """The first acquirer gets the lock and a writable file handle."""
    handle = acquire_runner_lock(runner_name="first", data_dir=tmp_path)
    try:
        assert handle is not None
        lock_path = tmp_path / ".eval_runner.lock"
        assert lock_path.exists()
        # File records the holder.
        content = lock_path.read_text()
        assert "runner=first" in content
        assert "pid=" in content
    finally:
        handle.close()


def test_second_runner_exits_2_when_lock_held(tmp_path, capsys):
    """A second concurrent acquirer fails loud rather than silently racing."""
    first = acquire_runner_lock(runner_name="first", data_dir=tmp_path)
    try:
        with pytest.raises(SystemExit) as exc:
            acquire_runner_lock(runner_name="second", data_dir=tmp_path)
        assert exc.value.code == 2
    finally:
        first.close()


def test_released_lock_can_be_reacquired(tmp_path):
    """Closing the handle releases the lock; next acquirer succeeds."""
    first = acquire_runner_lock(runner_name="first", data_dir=tmp_path)
    first.close()

    second = acquire_runner_lock(runner_name="second", data_dir=tmp_path)
    try:
        assert second is not None
        content = (tmp_path / ".eval_runner.lock").read_text()
        assert "runner=second" in content
    finally:
        second.close()


def test_distinct_data_dirs_do_not_contend(tmp_path):
    """Per-runner data-dir overrides get distinct lock files; no contention."""
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"

    handle_a = acquire_runner_lock(runner_name="runner_a", data_dir=dir_a)
    # Should NOT raise — separate lock file.
    handle_b = acquire_runner_lock(runner_name="runner_b", data_dir=dir_b)
    try:
        assert handle_a is not None
        assert handle_b is not None
    finally:
        handle_a.close()
        handle_b.close()
