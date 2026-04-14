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
    (data / "last_run.json").write_text('{"task": "fake"}')

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
        """Full reset: chroma, metrics, last_run, and taxonomy all go."""
        _invoke_reset(fake_data_dir)

        assert not (fake_data_dir / "cli" / "chroma").exists()
        assert not (fake_data_dir / "cli" / "metrics.duckdb").exists()
        assert not (fake_data_dir / "last_run.json").exists()
        assert not (fake_data_dir / "cli" / "domains.json").exists()

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
