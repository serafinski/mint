"""Atomic, multi-process-safe settings file for the MINT CLI.

Persists user preferences (currently: per-operation optimizer config per
§12 finding #8) across ``mint run`` invocations. Settings file lives at
``data/cli/settings.json`` and is written via write-to-temp + rename +
``fcntl.flock`` — the same pattern as
:class:`src.orchestrator.taxonomy.DomainTaxonomy`.

**Why a dedicated module.** The evolutionary taxonomy has its own file
(``domains.json``) and its own concurrency story (LLM-driven category
commits). Settings are simpler (small JSON blob, rare writes, single
writer in practice) but share the atomicity requirements — two ``mint``
invocations running in parallel should never corrupt the file or observe
a partially-written state.

**Schema.** The file is a free-form JSON object. Current keys:

- ``optimizer``: dict with ``prune`` / ``split`` / ``merge`` →
  ``"auto" | "on" | "off"`` (see
  :class:`src.schemas.optimizer_config.OptimizerConfig`).

Missing keys are interpreted as "use defaults" — a fresh install with no
settings file gets all-defaults behavior (all operations ``"auto"``).
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_settings(path: Path) -> dict[str, Any]:
    """Load settings from disk, returning an empty dict if the file is missing.

    Never raises. Malformed JSON or OS errors log a warning and return an
    empty dict — a corrupted settings file should not block ``mint run``.

    Args:
        path: Path to the settings JSON file (typically
            ``data/cli/settings.json``).

    Returns:
        The settings dict, or ``{}`` if the file is missing / malformed.
    """
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "CLI settings: failed to read %s (%s) — using defaults", path, exc,
        )
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "CLI settings: %s is not a JSON object — using defaults", path,
        )
        return {}
    return data


def save_settings(path: Path, settings: dict[str, Any]) -> None:
    """Atomically persist settings to disk.

    Writes to a temp file in the same directory, fsyncs, then atomic-
    renames into place. Parent directories are created if absent. An
    exclusive flock is held on the temp file during the write to
    coordinate with any concurrent reader (which holds a shared lock via
    :func:`load_settings`).

    Raises:
        OSError: If the parent directory can't be created or the rename
            fails. Callers should handle this — unlike ``load_settings``
            (which falls back to defaults), a failed save means the
            user's intent didn't persist and should be reported.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path_str = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                json.dump(settings, f, indent=2, sort_keys=True)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def get_optimizer_config_from_settings(settings: dict[str, Any]) -> dict[str, str]:
    """Extract the optimizer sub-dict from a settings blob.

    Returns the ``{"prune": ..., "split": ..., "merge": ...}`` mapping if
    present, or an empty dict otherwise. Does NOT construct an
    :class:`~src.schemas.optimizer_config.OptimizerConfig` here — leaves
    that to callers so they can merge per-invocation flag overrides before
    instantiating the typed model.
    """
    opt = settings.get("optimizer")
    if not isinstance(opt, dict):
        return {}
    return {
        k: v
        for k, v in opt.items()
        if k in ("prune", "split", "merge") and isinstance(v, str)
    }
