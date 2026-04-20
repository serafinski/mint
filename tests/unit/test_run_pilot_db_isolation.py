"""Tests for ext7 #49 — ``run_pilot.py`` DB isolation semantics.

Pre-fix, ``run_pilot.py`` hardcoded ``data/eval/metrics.duckdb``. Every
invocation appended into the same file, silently pooling across sessions.

Post-fix:
- ``--db`` CLI arg (default: a fresh timestamped path).
- ``--fresh-db`` wipes an existing path.
- ``--append`` opts into appending to an existing path.
- Refuses (exit 2) to run against an existing non-empty DB without either flag.

The tests below invoke the runner's CLI arg plumbing without actually running
the harness (which would require Docker + LLM). We import ``main`` and drive
it through ``monkeypatch.setattr(sys, 'argv', ...)`` + mock the downstream
imports so the exit happens before the expensive paths.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


def _make_stub_harness(monkeypatch):
    """Stub the harness + tasks imports so main() short-circuits."""
    import scripts.run_pilot as rp

    class _StubHarness:
        def __init__(self, config):
            self._config = config

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def prepare(self):
            return "snap-stub"

        def run_pilot(self, tasks, condition):
            return []

        def run_condition(self, condition, tasks):
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
    return rp


def test_refuses_to_run_against_non_empty_db_without_flag(tmp_path, monkeypatch):
    """An existing non-empty DB + no --fresh-db + no --append → exit 2."""
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
    """No --db → a fresh timestamped path is announced at startup."""
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
