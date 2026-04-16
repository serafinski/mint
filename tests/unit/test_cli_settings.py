"""Unit tests for ``src.cli.settings``."""

from __future__ import annotations

import json

from src.cli.settings import (
    get_optimizer_config_from_settings,
    load_settings,
    save_settings,
)


class TestLoadSettings:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_settings(tmp_path / "absent.json") == {}

    def test_valid_file_loads(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text('{"optimizer": {"prune": "on"}}')
        assert load_settings(path) == {"optimizer": {"prune": "on"}}

    def test_malformed_json_returns_empty(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("{not valid json")
        assert load_settings(path) == {}

    def test_non_object_returns_empty(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("[1, 2, 3]")
        assert load_settings(path) == {}


class TestSaveSettings:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "settings.json"
        payload = {"optimizer": {"prune": "on", "split": "off", "merge": "auto"}}
        save_settings(path, payload)
        assert path.exists()
        assert load_settings(path) == payload

    def test_creates_parent_directory(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "settings.json"
        save_settings(path, {"a": 1})
        assert path.exists()

    def test_overwrites_existing(self, tmp_path):
        path = tmp_path / "settings.json"
        save_settings(path, {"optimizer": {"prune": "on"}})
        save_settings(path, {"optimizer": {"prune": "off"}})
        assert load_settings(path)["optimizer"]["prune"] == "off"

    def test_atomic_write_leaves_no_temp_files(self, tmp_path):
        path = tmp_path / "settings.json"
        save_settings(path, {"x": 1})
        # No stray temp files from the write-to-temp + rename protocol.
        temps = [p for p in tmp_path.iterdir() if p.name.startswith(".settings.json.")]
        assert temps == []


class TestGetOptimizerConfigFromSettings:
    def test_empty_settings_returns_empty(self):
        assert get_optimizer_config_from_settings({}) == {}

    def test_missing_optimizer_key(self):
        assert get_optimizer_config_from_settings({"other": "x"}) == {}

    def test_extracts_optimizer_dict(self):
        settings = {"optimizer": {"prune": "on", "split": "off", "merge": "auto"}}
        assert get_optimizer_config_from_settings(settings) == {
            "prune": "on", "split": "off", "merge": "auto",
        }

    def test_filters_unknown_operations(self):
        settings = {"optimizer": {"prune": "on", "nonexistent": "on"}}
        assert get_optimizer_config_from_settings(settings) == {"prune": "on"}

    def test_filters_non_string_values(self):
        settings = {"optimizer": {"prune": "on", "split": 42}}
        assert get_optimizer_config_from_settings(settings) == {"prune": "on"}

    def test_non_dict_optimizer_returns_empty(self):
        assert get_optimizer_config_from_settings({"optimizer": "on"}) == {}
