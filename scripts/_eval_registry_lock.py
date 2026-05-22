"""Advisory lock for shared eval registry state.

``run_pilot.py`` and ``run_longitudinal.py`` both mutate shared eval state:
``data/eval/chroma``, ``data/eval/skills``, and the clean snapshot under
``tests/evaluation/snapshots``.

Running both active eval runners concurrently can corrupt that state: one
runner's cleanup can wipe the other's active registry, and one runner's
snapshot can overwrite the other's expected clean baseline.

The fix: an advisory file lock at ``data/eval/.eval_runner.lock``. Any
subsequent runner that tries to start while another holds the lock refuses
with exit code 2. The protocol documents serial execution; this lock enforces
that constraint at runtime.
"""

from __future__ import annotations

import fcntl
import logging
import os
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

    Separate ``data_dir`` values get separate lock files. This keeps isolated
    tests or developer experiments from contending when they do not share
    runtime state.
    """
    lock_path = data_dir / ".eval_runner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.error(
            "Another eval runner holds the lock at %s; the "
            "eval registry state (%s plus shared snapshots) is NOT safe for "
            "concurrent access. Wait for the other run to finish, or run "
            "against a separate data directory for isolated experiments.",
            lock_path, data_dir,
        )
        sys.exit(2)

    # Record who holds the lock for diagnostics.
    try:
        lock_file.write(f"runner={runner_name}\npid={os.getpid()}\n")
        lock_file.flush()
    except OSError:
        pass

    logger.info("acquired eval-runner lock (runner=%s)", runner_name)
    return lock_file
