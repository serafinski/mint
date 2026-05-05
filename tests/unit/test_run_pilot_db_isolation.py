"""Tests for ``run_pilot.py`` DB and MetricsStore isolation semantics.

The runner must use a campaign-scoped DuckDB path by default, refuse accidental
reuse of non-empty DBs, and isolate optimizer MetricsStore files between
ablation conditions unless the caller explicitly chooses append/reuse behavior.

The tests below invoke the runner's argument plumbing without actually running
the harness (which would require Docker + LLM). We import ``main`` and drive
it through ``monkeypatch.setattr(sys, 'argv', ...)`` + mock the downstream
imports so the exit happens before the expensive paths.
"""

from __future__ import annotations

import sys

import pytest

from tests.unit._script_subprocess import run_project_script, run_wrapper, write_wrapper


def _make_stub_harness(monkeypatch):
    """Stub the harness + tasks imports so main() short-circuits."""
    import scripts.run_pilot as rp
    captured_configs = []
    condition_metrics_paths = []

    class _StubHarness:
        def __init__(self, config):
            self._config = config
            captured_configs.append(config)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def prepare(self):
            return "snap-stub"

        def run_pilot(self, tasks, condition):
            condition_metrics_paths.append((condition, self._config.metrics_store_path))
            return []

        def run_condition(self, condition, tasks):
            condition_metrics_paths.append((condition, self._config.metrics_store_path))
            return []

    class _StubConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    # Patch the harness class + tasks imports via module-level attrs.
    monkeypatch.setattr(
        "tests.evaluation.harness.EvaluationHarness", _StubHarness, raising=True,
    )
    monkeypatch.setattr(
        "tests.evaluation.harness.HarnessConfig", _StubConfig, raising=True,
    )
    rp._captured_configs = captured_configs  # type: ignore[attr-defined]
    rp._condition_metrics_paths = condition_metrics_paths  # type: ignore[attr-defined]
    return rp


def test_refuses_to_run_against_non_empty_db_without_flag(tmp_path, monkeypatch):
    """An existing non-empty DB without --fresh-db or --append exits 2."""
    rp = _make_stub_harness(monkeypatch)

    db = tmp_path / "existing.duckdb"
    db.write_bytes(b"x" * 1024)  # non-empty

    monkeypatch.setattr(sys, "argv", [
        "run_pilot.py", "--db", str(db), "--tasks", "T01",
    ])

    with pytest.raises(SystemExit) as exc:
        rp.main()
    assert exc.value.code == 2


def test_fresh_db_wipes_existing_path(tmp_path, monkeypatch, capsys):
    """--fresh-db on an existing non-empty DB unlinks it before the run."""
    rp = _make_stub_harness(monkeypatch)

    db = tmp_path / "existing.duckdb"
    db.write_bytes(b"x" * 1024)

    monkeypatch.setattr(sys, "argv", [
        "run_pilot.py", "--db", str(db), "--fresh-db", "--tasks", "T01",
    ])

    # main() may still raise downstream (harness is stubbed minimally), but
    # the early DB-check should have unlinked the file before any raise.
    try:
        rp.main()
    except SystemExit:
        pass
    except Exception:
        pass

    # The old file should be gone (new one may have been created by the store,
    # but the 1024-byte content is definitely overwritten).
    if db.exists():
        assert db.read_bytes() != b"x" * 1024
    out = capsys.readouterr().out
    assert "wiped existing DB" in out


def test_fresh_db_and_append_are_mutually_exclusive(tmp_path, monkeypatch):
    """Passing both --fresh-db and --append exits with code 2."""
    rp = _make_stub_harness(monkeypatch)
    db = tmp_path / "any.duckdb"
    db.write_bytes(b"x")

    monkeypatch.setattr(sys, "argv", [
        "run_pilot.py", "--db", str(db), "--fresh-db", "--append", "--tasks", "T01",
    ])

    with pytest.raises(SystemExit) as exc:
        rp.main()
    assert exc.value.code == 2


def test_default_db_path_is_timestamped(tmp_path, monkeypatch, capsys):
    """No --db announces a fresh timestamped path at startup."""
    rp = _make_stub_harness(monkeypatch)
    monkeypatch.setattr(rp, "DATA_DIR", tmp_path)

    monkeypatch.setattr(sys, "argv", ["run_pilot.py", "--tasks", "T01"])

    try:
        rp.main()
    except Exception:
        pass

    out = capsys.readouterr().out
    assert "fresh timestamped DB path" in out
    # Timestamped path pattern: metrics_YYYYMMDD_HHMMSS.duckdb.
    assert "metrics_" in out and ".duckdb" in out


def test_metrics_store_defaults_to_campaign_scoped_path(tmp_path, monkeypatch):
    """Without --metrics-store, run_pilot derives a sibling DB-scoped store path."""
    rp = _make_stub_harness(monkeypatch)

    db = tmp_path / "campaign.duckdb"
    monkeypatch.setattr(sys, "argv", [
        "run_pilot.py", "--db", str(db), "--tasks", "T01",
    ])

    try:
        rp.main()
    except SystemExit:
        pass
    except Exception:
        pass

    cfg = rp._captured_configs[-1]  # type: ignore[attr-defined]
    assert cfg.metrics_store_path == str(tmp_path / "campaign_metrics_store.duckdb")


def test_metrics_store_explicit_path_is_passed_to_harness(tmp_path, monkeypatch):
    """Explicit --metrics-store flows through to HarnessConfig."""
    rp = _make_stub_harness(monkeypatch)

    db = tmp_path / "campaign.duckdb"
    metrics = tmp_path / "custom_metrics.duckdb"
    monkeypatch.setattr(sys, "argv", [
        "run_pilot.py",
        "--db", str(db),
        "--metrics-store", str(metrics),
        "--tasks", "T01",
    ])

    try:
        rp.main()
    except SystemExit:
        pass
    except Exception:
        pass

    cfg = rp._captured_configs[-1]  # type: ignore[attr-defined]
    assert cfg.metrics_store_path == str(metrics)


def test_all_conditions_use_isolated_metrics_store_paths(tmp_path, monkeypatch):
    """A multi-condition campaign must not share one MetricsStore file across conditions."""
    rp = _make_stub_harness(monkeypatch)

    db = tmp_path / "campaign.duckdb"
    metrics = tmp_path / "campaign_metrics.duckdb"
    monkeypatch.setattr(sys, "argv", [
        "run_pilot.py",
        "--db", str(db),
        "--metrics-store", str(metrics),
        "--all-conditions",
        "--tasks", "T01",
    ])

    try:
        rp.main()
    except SystemExit:
        pass
    except Exception:
        pass

    seen = dict(rp._condition_metrics_paths)  # type: ignore[attr-defined]
    assert set(seen) == {"full", "no_verification", "no_optimization", "static"}
    assert seen["full"].endswith("campaign_metrics_full.duckdb")
    assert seen["no_verification"].endswith("campaign_metrics_no_verification.duckdb")
    assert seen["no_optimization"].endswith("campaign_metrics_no_optimization.duckdb")
    assert seen["static"].endswith("campaign_metrics_static.duckdb")


def test_persisted_evaluation_run_count_reads_duckdb(tmp_path):
    """Watchdog summary should be able to recover persisted row counts."""
    import duckdb

    import scripts.run_pilot as rp

    db = tmp_path / "campaign.duckdb"
    with duckdb.connect(str(db)) as con:
        con.execute("create table evaluation_runs (task_id varchar)")
        con.execute("insert into evaluation_runs values ('T01'), ('T02')")

    assert rp._persisted_evaluation_run_count(str(db)) == 2


def test_persisted_evaluation_run_count_returns_none_without_table(tmp_path):
    import duckdb

    import scripts.run_pilot as rp

    db = tmp_path / "empty.duckdb"
    with duckdb.connect(str(db)):
        pass

    assert rp._persisted_evaluation_run_count(str(db)) is None


def test_run_pilot_help_exits_zero_via_entrypoint():
    """Subprocess smoke: argparse --help reaches the shell as exit 0."""
    result = run_project_script("run_pilot.py", "--help", timeout=10)

    assert result.returncode == 0
    assert "--condition" in result.stdout


def test_run_pilot_exits_7_on_watchdog_abort(tmp_path):
    """A condition watchdog abort should surface as process exit code 7."""
    db = tmp_path / "pilot.duckdb"
    wrapper = tmp_path / "wrap_run_pilot_abort.py"
    write_wrapper(wrapper, f"""
from unittest.mock import MagicMock
import sys

import tests.evaluation.harness as _h
import scripts._eval_registry_lock as _lock
import scripts.run_pilot as _rp

_h.EvaluationHarness = MagicMock()
_h.HarnessConfig = MagicMock()
_lock.acquire_runner_lock = lambda **kw: MagicMock()

_inst = MagicMock()
_inst.prepare.return_value = "snap-id"
_inst.run_condition.side_effect = _rp.BudgetExceeded("stub abort")
_inst.run_pilot.side_effect = _rp.BudgetExceeded("stub abort")
_h.EvaluationHarness.return_value.__enter__.return_value = _inst
_h.EvaluationHarness.return_value.__exit__.return_value = False

sys.argv = [
    "run_pilot.py",
    "--db", r"{db}", "--fresh-db",
    "--condition", "full",
    "--n-runs", "1",
    "--condition-budget-hours", "1",
]
sys.exit(_rp.main() or 0)
""")

    result = run_wrapper(wrapper)

    assert result.returncode == 7, (
        f"Expected exit 7 on watchdog abort, got {result.returncode}. "
        f"stderr:\n{result.stderr[:2000]}\nstdout:\n{result.stdout[:2000]}"
    )


def test_run_pilot_exits_4_on_empty_records_no_abort(tmp_path):
    """Zero produced records without a watchdog abort should exit 4."""
    db = tmp_path / "pilot_empty.duckdb"
    wrapper = tmp_path / "wrap_run_pilot_empty.py"
    write_wrapper(wrapper, f"""
from unittest.mock import MagicMock
import sys

import tests.evaluation.harness as _h
import scripts._eval_registry_lock as _lock
import scripts.run_pilot as _rp

_h.EvaluationHarness = MagicMock()
_h.HarnessConfig = MagicMock()
_lock.acquire_runner_lock = lambda **kw: MagicMock()

_inst = MagicMock()
_inst.prepare.return_value = "snap-id"
_inst.run_condition.return_value = []
_inst.run_pilot.return_value = []
_h.EvaluationHarness.return_value.__enter__.return_value = _inst
_h.EvaluationHarness.return_value.__exit__.return_value = False

sys.argv = [
    "run_pilot.py",
    "--db", r"{db}", "--fresh-db",
    "--condition", "full",
    "--n-runs", "1",
    "--condition-budget-hours", "1",
]
sys.exit(_rp.main() or 0)
""")

    result = run_wrapper(wrapper)

    assert result.returncode == 4, (
        f"Expected exit 4 on empty records, got {result.returncode}. "
        f"stderr:\n{result.stderr[:2000]}"
    )


def test_run_pilot_exits_0_on_clean_success(tmp_path):
    """A one-record successful campaign should exit 0."""
    db = tmp_path / "pilot_ok.duckdb"
    wrapper = tmp_path / "wrap_run_pilot_ok.py"
    write_wrapper(wrapper, f"""
from unittest.mock import MagicMock
import sys

import tests.evaluation.harness as _h
import scripts._eval_registry_lock as _lock
import scripts.run_pilot as _rp

_h.EvaluationHarness = MagicMock()
_h.HarnessConfig = MagicMock()
_lock.acquire_runner_lock = lambda **kw: MagicMock()

_rec = MagicMock()
_rec.success = True
_rec.gate_passed = True
_rec.output_in_range = True
_rec.task_id = "T01"
_rec.failure_reason = None
_rec.capability_type = "mcp_tool"
_rec.verification_tier_reached = 3

_inst = MagicMock()
_inst.prepare.return_value = "snap-id"
_inst.run_pilot.return_value = [_rec]
_inst.run_condition.return_value = [_rec]
_h.EvaluationHarness.return_value.__enter__.return_value = _inst
_h.EvaluationHarness.return_value.__exit__.return_value = False

sys.argv = [
    "run_pilot.py",
    "--db", r"{db}", "--fresh-db",
    "--condition", "full",
    "--n-runs", "1",
    "--condition-budget-hours", "1",
]
sys.exit(_rp.main() or 0)
""")

    result = run_wrapper(wrapper)

    assert result.returncode == 0, (
        f"Expected clean exit 0, got {result.returncode}. "
        f"stderr:\n{result.stderr[:2000]}"
    )
