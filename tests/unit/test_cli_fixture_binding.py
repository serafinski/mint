"""Regression guards for fixture-to-parameter matching in ``src.cli.runner``."""

from pathlib import Path

from src.cli.runner import _match_files_to_params


def test_match_files_to_params_prefers_semantic_best_match(tmp_path):
    source_code = """
async def parse_aggregation_policy(hourly_csv_path: str, policy_json_path: str) -> dict:
    return {}
"""
    file_paths = [
        tmp_path / "t15_agg_config.json",
        tmp_path / "t15_hourly_metrics.csv",
    ]
    sandbox_paths = [f"/workspace/fixtures/{fp.name}" for fp in file_paths]

    matched = _match_files_to_params(source_code, file_paths, sandbox_paths)

    assert matched["hourly_csv_path"].endswith("t15_hourly_metrics.csv")
    assert matched["policy_json_path"].endswith("t15_agg_config.json")


def test_match_files_to_params_falls_back_by_position_when_ambiguous(tmp_path):
    source_code = """
async def parse_inputs(first_path: str, second_path: str) -> dict:
    return {}
"""
    file_paths = [
        tmp_path / "left.dat",
        tmp_path / "right.dat",
    ]
    sandbox_paths = [f"/workspace/fixtures/{fp.name}" for fp in file_paths]

    matched = _match_files_to_params(source_code, file_paths, sandbox_paths)

    assert matched["first_path"].endswith("left.dat")
    assert matched["second_path"].endswith("right.dat")
