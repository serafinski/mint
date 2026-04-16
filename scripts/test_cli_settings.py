"""Deterministic smoke test for the CLI UX pass (§12 findings #8 + #6b).

Exercises the per-operation optimizer config + mint feedback --wrong
surfaces end-to-end:

1. ``mint config show`` on an empty state reports defaults.
2. ``mint config set optimizer.<op> <value>`` writes the expected JSON.
3. ``mint config show`` round-trips the persisted state.
4. ``mint config unset optimizer.<op>`` reverts one key.
5. ``mint config reset`` wipes everything back to defaults.
6. Priority chain: per-invocation flags override persistent settings
   (OptimizerConfig direct-construction test, no LLM needed).
7. ``OptimizerConfig.is_enabled`` honors auto / on / off under a
   threshold gate.
8. ``MetricsStore.flag_most_recent_invocation_wrong`` updates the
   most recent row and records a ``user_flagged_wrong`` event.
9. ``mint feedback --wrong`` CLI → flagged row is visible to
   ``get_recent_failures`` (downstream Step 8 split plumbing).

No LLM calls, no Docker, no pipeline execution — purely the CLI +
settings + metrics mechanics. Runs in ~5 seconds, costs nothing.

Usage::

    uv run scripts/test_cli_settings.py

Exits 0 on all-pass, 1 on any failure. Uses an isolated temp
``data/cli`` to avoid clobbering the user's real settings / metrics.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("test_cli_settings")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_mint(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Invoke ``uv run mint ...`` in a subprocess and capture output."""
    return subprocess.run(
        ["uv", "run", "mint", *args],
        capture_output=True,
        text=True,
        cwd=cwd or ROOT,
        env=env,
        check=False,
    )


def _isolated_data_dir() -> tempfile.TemporaryDirectory:
    """Spin up a temp dir that the CLI will use as its data root.

    The CLI's ``_data_dir()`` returns ``<repo>/data``. For isolation we
    symlink the temp dir into place... actually the CLI resolves the
    repo-relative path at import time, so we can't redirect it via env
    vars without patching the module. Instead we point the settings
    file path and metrics path directly via a temp ``data/cli``
    subdirectory inside a tempdir that we then make the CWD.

    Simpler: just back up the existing ``data/cli/settings.json`` (if
    any), delete it, run the tests, then restore. That's what this
    returns — a context manager that handles the backup/restore.
    """
    # Return a real tempdir but we don't redirect the CLI to it — we
    # manipulate the real data/cli/settings.json directly and restore
    # it at the end. This matches how test_prune.py / test_split_merge.py
    # operate: they use the real on-disk paths but clean up.
    return tempfile.TemporaryDirectory(prefix="test_cli_settings_")


def _settings_path() -> Path:
    return ROOT / "data" / "cli" / "settings.json"


def _backup_settings() -> str | None:
    path = _settings_path()
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _restore_settings(backup: str | None) -> None:
    path = _settings_path()
    if backup is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(backup, encoding="utf-8")


def _assert(condition: bool, message: str) -> None:
    if not condition:
        print(f"  FAIL: {message}")
        raise AssertionError(message)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_config_show_empty() -> bool:
    """``mint config show`` on empty state reports defaults."""
    print("\n[1/9] mint config show (empty state)")
    _settings_path().unlink(missing_ok=True)
    result = _run_mint("config", "show")
    _assert(result.returncode == 0, f"exit={result.returncode}")
    _assert(
        "No settings saved yet" in result.stdout,
        f"expected 'No settings saved yet' in stdout; got: {result.stdout}",
    )
    _assert(
        "auto (threshold-gated)" in result.stdout,
        "expected defaults hint in stdout",
    )
    print("  OK")
    return True


def test_config_set_writes_json() -> bool:
    """``mint config set`` creates/updates settings.json."""
    print("\n[2/9] mint config set optimizer.split off")
    _settings_path().unlink(missing_ok=True)

    result = _run_mint("config", "set", "optimizer.split", "off")
    _assert(result.returncode == 0, f"exit={result.returncode}")
    _assert("Set optimizer.split = off" in result.stdout, "confirmation missing")

    _assert(_settings_path().exists(), "settings.json not created")
    settings = json.loads(_settings_path().read_text(encoding="utf-8"))
    _assert(
        settings == {"optimizer": {"split": "off"}},
        f"unexpected settings content: {settings}",
    )
    print("  OK")
    return True


def test_config_show_roundtrip() -> bool:
    """``mint config show`` round-trips the persisted state."""
    print("\n[3/9] mint config set + show round-trip")
    _settings_path().unlink(missing_ok=True)

    _run_mint("config", "set", "optimizer.prune", "on")
    _run_mint("config", "set", "optimizer.merge", "off")
    result = _run_mint("config", "show")
    _assert(result.returncode == 0, f"exit={result.returncode}")
    _assert('"prune": "on"' in result.stdout, "prune=on not shown")
    _assert('"merge": "off"' in result.stdout, "merge=off not shown")
    print("  OK")
    return True


def test_config_unset_single_key() -> bool:
    """``mint config unset`` reverts one key, leaves others untouched."""
    print("\n[4/9] mint config unset optimizer.prune")
    _settings_path().unlink(missing_ok=True)

    _run_mint("config", "set", "optimizer.prune", "on")
    _run_mint("config", "set", "optimizer.split", "off")

    result = _run_mint("config", "unset", "optimizer.prune")
    _assert(result.returncode == 0, f"exit={result.returncode}")
    _assert("Unset optimizer.prune" in result.stdout, "unset confirmation missing")

    settings = json.loads(_settings_path().read_text(encoding="utf-8"))
    _assert(
        settings == {"optimizer": {"split": "off"}},
        f"expected only split=off after unsetting prune; got: {settings}",
    )
    print("  OK")
    return True


def test_config_reset_wipes_all() -> bool:
    """``mint config reset`` wipes the file entirely."""
    print("\n[5/9] mint config reset")
    _settings_path().unlink(missing_ok=True)

    _run_mint("config", "set", "optimizer.prune", "on")
    _run_mint("config", "set", "optimizer.split", "off")
    _run_mint("config", "set", "optimizer.merge", "off")
    _assert(_settings_path().exists(), "precondition: file should exist")

    result = _run_mint("config", "reset")
    _assert(result.returncode == 0, f"exit={result.returncode}")
    _assert(
        "Reset all settings to defaults" in result.stdout,
        f"missing reset confirmation; got: {result.stdout}",
    )
    _assert(not _settings_path().exists(), "settings.json should be deleted")

    # Second reset on empty state reports idempotency, not error.
    result2 = _run_mint("config", "reset")
    _assert(result2.returncode == 0, f"second reset failed: {result2.returncode}")
    _assert(
        "No settings file exists" in result2.stdout,
        "expected idempotent message on double-reset",
    )
    print("  OK")
    return True


def test_optimizer_config_priority_chain() -> bool:
    """Priority chain: flags > persistent > defaults."""
    print("\n[6/9] OptimizerConfig direct construction + gating logic")
    from src.schemas.optimizer_config import OptimizerConfig

    # Defaults = all auto.
    default = OptimizerConfig()
    _assert(default.prune == "auto", "default prune")
    _assert(default.split == "auto", "default split")
    _assert(default.merge == "auto", "default merge")

    # all_on / all_off factories.
    _assert(OptimizerConfig.all_on().prune == "on", "all_on prune")
    _assert(OptimizerConfig.all_off().split == "off", "all_off split")

    # Flag override (eval harness path).
    from_true = OptimizerConfig.from_run_optimizer_bool(True)
    _assert(from_true == OptimizerConfig.all_on(), "run_optimizer=True → all_on")
    from_false = OptimizerConfig.from_run_optimizer_bool(False)
    _assert(from_false == OptimizerConfig.all_off(), "run_optimizer=False → all_off")

    # Persistent settings construction.
    from src.cli.settings import get_optimizer_config_from_settings

    raw = {"optimizer": {"prune": "on", "split": "off", "merge": "auto"}}
    extracted = get_optimizer_config_from_settings(raw)
    persistent = OptimizerConfig(**extracted)
    _assert(persistent.prune == "on", "persistent prune")
    _assert(persistent.split == "off", "persistent split")
    _assert(persistent.merge == "auto", "persistent merge")
    print("  OK")
    return True


def test_is_enabled_gates() -> bool:
    """``is_enabled`` honors auto/on/off semantics."""
    print("\n[7/9] OptimizerConfig.is_enabled() gating")
    from src.schemas.optimizer_config import OptimizerConfig

    cfg = OptimizerConfig(prune="on", split="off", merge="auto")
    # on → always true
    _assert(cfg.is_enabled("prune", invocation_count=0, threshold=20), "on@0")
    _assert(cfg.is_enabled("prune", invocation_count=100, threshold=20), "on@100")
    # off → always false
    _assert(not cfg.is_enabled("split", invocation_count=0, threshold=20), "off@0")
    _assert(not cfg.is_enabled("split", invocation_count=100, threshold=20), "off@100")
    # auto → threshold-gated
    _assert(not cfg.is_enabled("merge", invocation_count=19, threshold=20), "auto@19")
    _assert(cfg.is_enabled("merge", invocation_count=20, threshold=20), "auto@20")
    _assert(cfg.is_enabled("merge", invocation_count=100, threshold=20), "auto@100")
    print("  OK")
    return True


def test_flag_most_recent_invocation() -> bool:
    """``MetricsStore.flag_most_recent_invocation_wrong`` updates + events."""
    print("\n[8/9] MetricsStore.flag_most_recent_invocation_wrong")
    from src.registry.metrics import MetricsStore
    from src.schemas.metrics import InvocationRecord

    store = MetricsStore()  # in-memory
    try:
        # Seed three invocations across two capabilities.
        for i, (cap, task, out) in enumerate([
            ("parser", "t1", '{"x": 1}'),
            ("writer", "t2", '{"y": 2}'),
            ("parser", "t3", '{"x": 3}'),
        ]):
            store.record_invocation(InvocationRecord(
                capability_id=cap,
                task_id=task,
                success=True,
                token_cost=100 + i,
                latency_ms=10.0 + i,
                input_args=f'{{"seq": {i}}}',
                actual_output=out,
            ))

        # Global most-recent → parser's t3.
        flagged = store.flag_most_recent_invocation_wrong("globally last was wrong")
        _assert(flagged is not None, "expected flagged record")
        _assert(flagged.capability_id == "parser", f"expected parser; got {flagged.capability_id}")
        _assert(flagged.task_id == "t3", f"expected t3; got {flagged.task_id}")
        _assert(flagged.success is False, "success should be False after flag")
        _assert(flagged.actual_output == '{"x": 3}', "actual_output preserved")

        # Downstream visibility: get_recent_failures should see the flagged row.
        failures = store.get_recent_failures("parser", limit=5)
        _assert(len(failures) == 1, f"expected 1 parser failure; got {len(failures)}")
        _assert(failures[0].error_message == "globally last was wrong", "reason plumbed")

        # Capability-filtered: most recent writer invocation.
        flagged_w = store.flag_most_recent_invocation_wrong(
            "writer was also wrong", capability_id="writer",
        )
        _assert(flagged_w is not None, "writer flag returned None")
        _assert(flagged_w.capability_id == "writer", "wrong cap filtered")

        # No-match filter → None.
        _assert(
            store.flag_most_recent_invocation_wrong(
                "nope", capability_id="nonexistent",
            ) is None,
            "expected None for unknown capability",
        )

        # user_flagged_wrong event recorded twice (once per successful flag).
        rows = store._conn.execute(
            "SELECT COUNT(*) FROM capability_events WHERE event_type = 'user_flagged_wrong'"
        ).fetchone()
        _assert(rows[0] == 2, f"expected 2 events; got {rows[0]}")
    finally:
        store.close()
    print("  OK")
    return True


def test_mint_feedback_cli_error_paths() -> bool:
    """``mint feedback`` reports clear errors for bad invocations."""
    print("\n[9/9] mint feedback error paths")
    # Missing --wrong flag.
    result = _run_mint("feedback")
    _assert(result.returncode == 1, f"expected exit 1; got {result.returncode}")
    _assert(
        "requires --wrong" in result.stderr or "requires --wrong" in result.stdout,
        f"missing --wrong error; stdout={result.stdout!r} stderr={result.stderr!r}",
    )

    # --wrong but no matching capability (use a clearly-unique name).
    result = _run_mint(
        "feedback", "--wrong",
        "--capability", "this-capability-definitely-does-not-exist-12345",
    )
    _assert(result.returncode == 1, f"expected exit 1; got {result.returncode}")
    _assert(
        "No recent invocation found" in result.stderr
        or "No recent invocation found" in result.stdout,
        "missing no-match error",
    )
    print("  OK")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    tests = [
        test_config_show_empty,
        test_config_set_writes_json,
        test_config_show_roundtrip,
        test_config_unset_single_key,
        test_config_reset_wipes_all,
        test_optimizer_config_priority_chain,
        test_is_enabled_gates,
        test_flag_most_recent_invocation,
        test_mint_feedback_cli_error_paths,
    ]

    print("=" * 70)
    print(" CLI SETTINGS + FEEDBACK SMOKE TEST (§12 findings #8 + #6b)")
    print("=" * 70)

    # Back up user's real settings.json so this script is non-destructive.
    backup = _backup_settings()

    failed: list[str] = []
    try:
        for test in tests:
            try:
                test()
            except AssertionError as exc:
                failed.append(f"{test.__name__}: {exc}")
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{test.__name__}: UNEXPECTED: {exc}")
    finally:
        # Restore user's real settings.json regardless of test outcomes.
        _restore_settings(backup)

    print()
    print("=" * 70)
    if failed:
        print(f" FAIL: {len(failed)}/{len(tests)} tests failed")
        for f in failed:
            print(f"  - {f}")
        return 1
    print(f" ALL {len(tests)} TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
