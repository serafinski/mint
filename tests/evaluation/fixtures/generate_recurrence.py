"""Recurrence fixture generator.

Builds repeated fixture variants for a selected subset of the existing taskbed
without modifying the canonical ``tests/evaluation/fixtures`` directory.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from tests.evaluation.fixtures import generate as canonical

ROOT = Path(__file__).resolve().parents[3]
RECURRENCE_FIXTURES_DIR = ROOT / "tests" / "evaluation" / "fixtures_recurrence"
RECURRENCE_MANIFEST_PATH = RECURRENCE_FIXTURES_DIR / "manifest.json"

_GENERATOR_MAP = {
    "T04": canonical._gen_t04,
    "T11": canonical._gen_t11,
    "T13": canonical._gen_t13,
    "T14": canonical._gen_t14,
    "T15": canonical._gen_t15,
    "T20": canonical._gen_t20,
}


def generate_recurrence_fixtures(
    *,
    task_ids: list[str] | None = None,
    variants_per_task: int = 3,
    base_seed: int = 4242,
    fixtures_root: Path = RECURRENCE_FIXTURES_DIR,
) -> list[dict]:
    """Generate recurrence fixtures and return the manifest rows.

    Each recurrence instance is written under:

    ``tests/evaluation/fixtures_recurrence/<task_id>/<variant_id>/``
    """
    selected = task_ids or list(_GENERATOR_MAP.keys())
    fixtures_root.mkdir(parents=True, exist_ok=True)

    manifest_rows: list[dict] = []
    for task_id in selected:
        if task_id not in _GENERATOR_MAP:
            raise KeyError(f"Unsupported recurrence task_id: {task_id}")
        generator = _GENERATOR_MAP[task_id]
        for variant_index in range(1, variants_per_task + 1):
            variant_id = f"v{variant_index:02d}"
            task_instance_id = f"{task_id}.{variant_id}"
            seed = base_seed + (1000 * int(task_id[1:])) + variant_index
            out_dir = fixtures_root / task_id / variant_id
            out_dir.mkdir(parents=True, exist_ok=True)

            expected = generator(out_dir, random.Random(seed))[task_id]
            fixture_files = sorted(
                str(p.relative_to(ROOT))
                for p in out_dir.iterdir()
                if p.is_file()
            )
            manifest_rows.append(
                {
                    "task_family_id": task_id,
                    "task_instance_id": task_instance_id,
                    "variant_id": variant_id,
                    "benchmark_track": "recurrence",
                    "seed": seed,
                    "fixture_files": fixture_files,
                    "expected_values": expected,
                }
            )

    manifest_path = fixtures_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_rows, indent=2))
    return manifest_rows


if __name__ == "__main__":
    rows = generate_recurrence_fixtures()
    print(f"Generated {len(rows)} recurrence fixture instances in {RECURRENCE_FIXTURES_DIR}")
    print(f"Manifest written to {RECURRENCE_MANIFEST_PATH}")
