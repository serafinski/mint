"""Unit tests for the ``mint reset`` CLI command.

Focused on the reset command's file-wiping behavior — in particular, Fix 2d
(2026-04-14): the reset command now wipes ``data/cli/domains.json`` so the
evolutionary domain taxonomy starts clean instead of carrying categories
forward from prior sessions. See docs/perf_fixes_2026_04_14.md §2d.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def fake_data_dir(tmp_path: Path) -> Path:
    """Create a fake data directory tree mirroring the CLI layout."""
    data = tmp_path / "data"
    cli = data / "cli"
    cli.mkdir(parents=True)

    # Simulate the files that mint reset should wipe
    (cli / "chroma").mkdir()
    (cli / "chroma" / "dummy.sqlite3").write_bytes(b"not a real chroma db")
    (cli / "metrics.duckdb").write_bytes(b"fake duckdb file")
    # DuckDB write-ahead log sits next to the main database file. Reset
    # should clean it up alongside metrics.duckdb so orphaned WALs from
    # crashed processes don't persist.
    (cli / "metrics.duckdb.wal").write_bytes(b"fake duckdb wal")
    # last_run.json lives at data/cli/last_run.json (written by
    # src/cli/runner.py). An earlier version of the reset command AND
    # this fixture used data/last_run.json (without the cli subdirectory),
    # which meant the test passed against the wrong-path production code
    # because they shared the same wrong assumption. Fixed 2026-04-14 —
    # see docs/perf_fixes_2026_04_14.md §4.9. The fixture now uses the
    # correct path so a regression in the production code would fail the
    # test instead of hiding behind a shared misassumption.
    (cli / "last_run.json").write_text('{"task": "fake"}')

    # The new file that Fix 2d is about — populate it so the test can
    # observe it being deleted.
    (cli / "domains.json").write_text(
        json.dumps(
            {
                "categories": {
                    "stale-category": {
                        "created_at": "2020-01-01T00:00:00+00:00",
                        "description": "left over from a prior session",
                    }
                },
                "version": 1,
            }
        )
    )

    # Generated skills directory — both SKILL and AGENT artifacts land here
    # via the set_skills_output_dir() mirror pattern. Populate with two
    # fake skill directories to verify reset wipes the entire tree.
    skills = cli / "skills"
    skills.mkdir()
    stale_skill_1 = skills / "stale-skill-1"
    stale_skill_1.mkdir()
    (stale_skill_1 / "SKILL.md").write_text(
        "---\nname: stale-skill-1\ndescription: left over from a prior session\n---\n\n# Stale Skill 1"
    )
    stale_skill_2 = skills / "stale-agent-1"
    stale_skill_2.mkdir()
    (stale_skill_2 / "SKILL.md").write_text(
        "---\nname: stale-agent-1\ndescription: agent artifact from a prior session\n---\n\n# Stale Agent 1"
    )

    return data


def _invoke_reset(fake_data_dir: Path, *, no_seed: bool = True) -> None:
    """Invoke the reset command with mocked _data_dir and _create_infra.

    ``no_seed=True`` skips the re-seeding path so the test doesn't require a
    real ChromaDB or LLM. We only care about the file-wiping behavior here.
    """
    from src.cli import app as app_module

    with (
        patch("src.cli.runner._data_dir", return_value=fake_data_dir),
        patch("src.cli.runner._load_env"),
        patch("src.cli.runner._create_infra"),
    ):
        app_module.reset(force=True, no_seed=no_seed)


class TestResetWipesTaxonomy:
    """Fix 2d: mint reset must wipe data/cli/domains.json."""

    def test_reset_deletes_domains_json(self, fake_data_dir: Path) -> None:
        domains_path = fake_data_dir / "cli" / "domains.json"
        assert domains_path.exists(), "precondition: fixture should create the file"

        _invoke_reset(fake_data_dir)

        assert not domains_path.exists(), (
            "mint reset must delete data/cli/domains.json — Fix 2d "
            "(docs/perf_fixes_2026_04_14.md §2d)"
        )

    def test_reset_deletes_all_expected_files(self, fake_data_dir: Path) -> None:
        """Full reset: chroma, metrics (+wal), last_run, taxonomy, skills all go."""
        _invoke_reset(fake_data_dir)

        assert not (fake_data_dir / "cli" / "chroma").exists()
        assert not (fake_data_dir / "cli" / "metrics.duckdb").exists()
        assert not (fake_data_dir / "cli" / "metrics.duckdb.wal").exists()
        assert not (fake_data_dir / "cli" / "last_run.json").exists()
        assert not (fake_data_dir / "cli" / "domains.json").exists()
        assert not (fake_data_dir / "cli" / "skills").exists()

    def test_reset_deletes_last_run_at_correct_path(self, fake_data_dir: Path) -> None:
        """Regression guard: last_run.json lives at data/cli/last_run.json.

        An earlier version of the reset command looked at data/last_run.json
        (without the cli subdirectory), which meant the reset silently failed
        to wipe the real file on every invocation. The unit test was complicit
        because the fixture shared the same wrong path assumption.

        This test explicitly creates the file at the CORRECT path and asserts
        reset wipes it there. If the production code regresses to the wrong
        path, the file stays and this test fails. See
        docs/perf_fixes_2026_04_14.md §4.9.
        """
        correct_path = fake_data_dir / "cli" / "last_run.json"
        assert correct_path.exists(), "precondition: fixture should create file at correct path"

        _invoke_reset(fake_data_dir)

        assert not correct_path.exists(), (
            "mint reset must delete data/cli/last_run.json — regression guard "
            "for the path bug where reset looked at data/last_run.json instead"
        )

    def test_reset_deletes_metrics_wal(self, fake_data_dir: Path) -> None:
        """Regression guard: DuckDB WAL file gets cleaned up alongside the DB."""
        wal_path = fake_data_dir / "cli" / "metrics.duckdb.wal"
        assert wal_path.exists()

        _invoke_reset(fake_data_dir)

        assert not wal_path.exists()

    def test_reset_deletes_generated_skills_directory(self, fake_data_dir: Path) -> None:
        """Regression guard for the skills directory wipe.

        Surfaced 2026-04-14 end of day after the 21:38 test_mint run left
        6 skill directories from 2-3 different runs in data/cli/skills/.
        The reset command must wipe the entire skills tree — both SKILL and
        AGENT artifacts land there via set_skills_output_dir()'s mirror
        pattern, so a single tree wipe handles both types.
        """
        skills_path = fake_data_dir / "cli" / "skills"
        assert skills_path.exists()
        assert (skills_path / "stale-skill-1" / "SKILL.md").exists()
        assert (skills_path / "stale-agent-1" / "SKILL.md").exists()

        _invoke_reset(fake_data_dir)

        assert not skills_path.exists(), (
            "mint reset must delete data/cli/skills/ tree — regression "
            "guard for the skills-directory wipe added 2026-04-14."
        )

    def test_reset_idempotent_when_taxonomy_absent(self, tmp_path: Path) -> None:
        """Reset must not crash when domains.json doesn't exist to begin with."""
        data = tmp_path / "data"
        (data / "cli").mkdir(parents=True)
        # No files at all — reset should be a clean no-op.

        # Must not raise.
        _invoke_reset(data)

    def test_reset_with_seed_also_deletes_taxonomy(self, fake_data_dir: Path) -> None:
        """Default reset (re-seeds registry) must still wipe taxonomy.

        The thesis framing is that the evolutionary taxonomy grows from
        generation, not from seed fixtures, so the seeded registry + empty
        taxonomy is the correct clean-slate state. gap_detection's fallback
        to unfiltered search handles seeded-cap matches while the taxonomy
        is empty.
        """
        domains_path = fake_data_dir / "cli" / "domains.json"
        assert domains_path.exists()

        _invoke_reset(fake_data_dir, no_seed=False)

        assert not domains_path.exists()
