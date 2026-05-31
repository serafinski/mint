"""Shared helpers for subprocess-level script tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import duckdb

from src.registry.evaluation_store import EvaluationStore
from src.schemas.evaluation import EvaluationRecord

ROOT = Path(__file__).parent.parent.parent


def run_project_script(
    script: str,
    *args: str,
    cwd: Path | None = None,
    env_extra: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """Run a script with the current test interpreter and capture output."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    cmd = [sys.executable, str(ROOT / "scripts" / script), *args]
    return subprocess.run(
        cmd,
        cwd=str(cwd or ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def seed_evaluation_runs(db_path: Path, *, rows: list[dict]) -> None:
    """Create an evaluation DB with real schema and the supplied rows."""
    store = EvaluationStore(db_path=str(db_path))
    try:
        for row in rows:
            record = EvaluationRecord(
                run_id=row["run_id"],
                condition=row["condition"],
                task_id=row.get("task_id", "T01"),
                task_tier=row.get("task_tier", "moderate"),
                attempt_count=row.get("attempt_count", 1),
                capability_type=row.get("capability_type", "mcp_tool"),
                verification_tier_reached=row.get("verification_tier_reached", 3),
                gate_passed=row.get("gate_passed", True),
                output_in_range=row.get("output_in_range", True),
                token_cost=row.get("token_cost", 100),
                attempt_latency_ms=row.get("attempt_latency_ms", 1000),
                failure_reason=row.get("failure_reason", "none"),
                registry_snapshot_id=row.get("registry_snapshot_id", "snap-1"),
                seed=row.get("seed", 42),
                is_pilot=row.get("is_pilot", False),
            )
            if "timestamp" in row:
                record.timestamp = row["timestamp"]
            store.record_run(record)
    finally:
        store.close()


def insert_invocation_events(db_path: Path, events: list[dict]) -> None:
    """Insert invocation events directly into an evaluation DB."""
    conn = duckdb.connect(str(db_path))
    try:
        for index, event in enumerate(events):
            conn.execute(
                """
                INSERT INTO invocation_events
                    (id, run_id, condition, task_id, capability_id,
                     capability_type, success, is_pilot, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event.get("id", f"ev-{index}"),
                    event["run_id"],
                    event["condition"],
                    event["task_id"],
                    event["capability_id"],
                    event.get("capability_type", "mcp_tool"),
                    event.get("success", True),
                    event.get("is_pilot", False),
                    event["occurred_at"],
                ],
            )
    finally:
        conn.close()


def write_wrapper(path: Path, body: str) -> None:
    """Write a subprocess wrapper script with the project root on sys.path."""
    path.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, r'{ROOT}')\n"
        + body
    )


def run_wrapper(wrapper: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a generated wrapper script and return its completed process."""
    return subprocess.run(
        [sys.executable, str(wrapper)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
        check=False,
    )
