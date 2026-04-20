"""Cross-runner lock on the shared eval registry directories (ext7 #51).

`run_pilot.py`, `run_longitudinal.py`, and `run_longitudinal_shift.py` all
mutate the same ``data/eval/chroma``, ``src/mcp_tools``, ``data/eval/skills``,
and ``tests/evaluation/snapshots`` directories — via ``rmtree`` at startup
(ext6 #31) and via ChromaDB collection writes throughout the run.

Running any two concurrently corrupts the shared state: one runner's rmtree
wipes the other's active registry; one runner's snapshot overwrites the
other's, and when the first runner calls ``restore_clean_snapshot`` later it
sees a different hash and raises ``RuntimeError: Snapshot ID mismatch``.

The fix: an advisory file lock at ``data/eval/.eval_runner.lock``. Any
subsequent runner that tries to start while another holds the lock refuses
with exit code 2 and a pointer to this finding. The run sheet documents
the serial execution order; this lock is belt-and-braces for when docs
are ignored.
"""

from __future__ import annotations

import fcntl
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def acquire_runner_lock(
    *,
    runner_name: str,
    data_dir: Path,
) -> object:
    """Acquire the shared eval-runner lock. Exits code 2 on contention.

    Returns a handle the caller must retain for the lifetime of the run
    (kept in a module-level variable or similar). Closing the process
    releases the lock via the OS.

    Separate locks per data directory: callers running against fresh
    per-runner ``--chroma-dir`` / ``--skills-dir`` overrides (the Chapter-7
    genuinely-parallel path) set a distinct ``data_dir`` and get a distinct
    lock file — so the mechanism doesn't block legitimate parallelism when
    shared state isn't the issue.
    """
    lock_path = data_dir / ".eval_runner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.error(
            "Another eval runner holds the lock at %s — per ext7 #51, the "
            "eval registry directories (%s, src/mcp_tools, snapshots) are "
            "NOT safe for concurrent access. Either wait for the other run "
            "to finish, or use per-runner --chroma-dir / --skills-dir / "
            "--snapshot-dir overrides (Chapter 7 future work).",
            lock_path, data_dir,
        )
        sys.exit(2)

    # Record who holds the lock for diagnostics.
    try:
        lock_file.write(f"runner={runner_name}\npid={__import__('os').getpid()}\n")
        lock_file.flush()
    except OSError:
        pass

    logger.info("ext7 #51: acquired eval-runner lock (runner=%s)", runner_name)
    return lock_file
