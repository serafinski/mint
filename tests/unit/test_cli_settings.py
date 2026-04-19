"""Unit tests for ``src.cli.settings``."""

from __future__ import annotations

import json

from src.cli.settings import (
    get_network_allow_from_settings,
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


# ---------------------------------------------------------------------------
# Step 14.12 — persistent network.allow toggle
# ---------------------------------------------------------------------------


class TestGetNetworkAllowFromSettings:
    def test_empty_settings_returns_none(self):
        assert get_network_allow_from_settings({}) is None

    def test_missing_network_key_returns_none(self):
        assert get_network_allow_from_settings({"optimizer": {"prune": "on"}}) is None

    def test_explicit_true(self):
        assert get_network_allow_from_settings({"network": {"allow": True}}) is True

    def test_explicit_false(self):
        assert get_network_allow_from_settings({"network": {"allow": False}}) is False

    def test_non_dict_network_returns_none(self):
        assert get_network_allow_from_settings({"network": "on"}) is None

    def test_malformed_value_returns_none_and_warns(self, caplog):
        with caplog.at_level("WARNING"):
            result = get_network_allow_from_settings({"network": {"allow": "maybe"}})
        assert result is None
        assert any(
            "network.allow must be a bool" in r.getMessage()
            for r in caplog.records
        )

    def test_network_allow_persists_across_invocations(self, tmp_path):
        """Round-trip: save, reload, get_network_allow_from_settings."""
        path = tmp_path / "settings.json"
        save_settings(path, {"network": {"allow": True}})
        reloaded = load_settings(path)
        assert get_network_allow_from_settings(reloaded) is True


class TestMintConfigCLINetworkToggle:
    """``mint config set/unset network.allow`` through the Typer app."""

    def _run(self, *args, tmp_path, monkeypatch):
        """Invoke the Typer app with ``_settings_path`` redirected to tmp_path."""
        from typer.testing import CliRunner

        from src.cli import app as app_module

        # Redirect the settings path helper so the test is hermetic.
        monkeypatch.setattr(
            app_module,
            "_settings_path",
            lambda: tmp_path / "settings.json",
        )

        runner = CliRunner()
        return runner.invoke(app_module.app, list(args))

    def test_set_network_allow_true_persists(self, tmp_path, monkeypatch):
        result = self._run(
            "config", "set", "network.allow", "true",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert result.exit_code == 0, result.output + (result.stderr or "")
        settings_path = tmp_path / "settings.json"
        assert settings_path.exists()
        data = json.loads(settings_path.read_text())
        assert data["network"]["allow"] is True

    def test_set_network_allow_false_persists(self, tmp_path, monkeypatch):
        result = self._run(
            "config", "set", "network.allow", "false",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert result.exit_code == 0, result.output
        data = json.loads((tmp_path / "settings.json").read_text())
        assert data["network"]["allow"] is False

    def test_set_invalid_value_rejected(self, tmp_path, monkeypatch):
        result = self._run(
            "config", "set", "network.allow", "maybe",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert result.exit_code == 1
        combined = (result.output or "") + (result.stderr or "")
        assert "true" in combined.lower() or "false" in combined.lower()

    def test_set_unknown_network_subkey_rejected(self, tmp_path, monkeypatch):
        result = self._run(
            "config", "set", "network.nonexistent", "true",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert result.exit_code == 1

    def test_unset_network_allow_removes_key(self, tmp_path, monkeypatch):
        # First set, then unset.
        self._run(
            "config", "set", "network.allow", "true",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        result = self._run(
            "config", "unset", "network.allow",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert result.exit_code == 0, result.output
        settings_path = tmp_path / "settings.json"
        if settings_path.exists():
            data = json.loads(settings_path.read_text())
            assert "network" not in data or "allow" not in data.get("network", {})


# ---------------------------------------------------------------------------
# Step 13.6 Part C (Option A) — CLI-surface tests migrated from the deleted
# ``scripts/test_cli_settings.py`` smoke script. The script's 9 subprocess
# tests covered three redundant-with-library branches (OptimizerConfig
# factories / is_enabled / flag_most_recent_invocation_wrong — all already
# in test_optimizer_config.py and test_metrics.py) plus six CLI-surface
# branches with no unit coverage. The classes below port those six
# branches to Typer ``CliRunner`` + ``monkeypatch``, gaining hermeticity
# (no real-disk settings.json backup/restore, no subprocess boot time) and
# milliseconds-scale execution.
# ---------------------------------------------------------------------------


def _invoke_mint_cli(*args, tmp_path, monkeypatch, infra=None):
    """Invoke the ``mint`` Typer app with settings path + infra redirected.

    ``infra`` — optional ``(registry, loader, metrics_store)`` tuple. When
    provided, ``src.cli.runner._create_infra`` is patched to return it and
    ``_load_env`` is stubbed out. Needed for subcommands that open the
    real metrics DB (e.g. ``feedback``).
    """
    from typer.testing import CliRunner

    from src.cli import app as app_module

    monkeypatch.setattr(
        app_module,
        "_settings_path",
        lambda: tmp_path / "settings.json",
    )
    if infra is not None:
        from src.cli import runner as runner_module

        monkeypatch.setattr(runner_module, "_create_infra", lambda *, seed=True: infra)
        monkeypatch.setattr(runner_module, "_load_env", lambda: None)

    cli_runner = CliRunner()
    return cli_runner.invoke(app_module.app, list(args))


class TestMintConfigShow:
    """``mint config show`` empty-state + persisted-state output."""

    def test_empty_state_reports_defaults(self, tmp_path, monkeypatch):
        result = _invoke_mint_cli(
            "config", "show", tmp_path=tmp_path, monkeypatch=monkeypatch
        )
        assert result.exit_code == 0, result.output
        assert "No settings saved yet" in result.output
        assert "auto (threshold-gated)" in result.output

    def test_persisted_settings_roundtrip(self, tmp_path, monkeypatch):
        _invoke_mint_cli(
            "config", "set", "optimizer.prune", "on",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        _invoke_mint_cli(
            "config", "set", "optimizer.merge", "off",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        result = _invoke_mint_cli(
            "config", "show", tmp_path=tmp_path, monkeypatch=monkeypatch
        )
        assert result.exit_code == 0, result.output
        assert '"prune": "on"' in result.output
        assert '"merge": "off"' in result.output


class TestMintConfigReset:
    """``mint config reset`` deletes the file + is idempotent on empty state."""

    def test_reset_deletes_settings_file(self, tmp_path, monkeypatch):
        _invoke_mint_cli(
            "config", "set", "optimizer.prune", "on",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        settings_path = tmp_path / "settings.json"
        assert settings_path.exists()

        result = _invoke_mint_cli(
            "config", "reset", tmp_path=tmp_path, monkeypatch=monkeypatch
        )
        assert result.exit_code == 0, result.output
        assert "Reset all settings to defaults" in result.output
        assert not settings_path.exists()

    def test_reset_idempotent_on_empty_state(self, tmp_path, monkeypatch):
        result = _invoke_mint_cli(
            "config", "reset", tmp_path=tmp_path, monkeypatch=monkeypatch
        )
        assert result.exit_code == 0, result.output
        assert "No settings file exists" in result.output


class TestMintConfigSetUnsetOptimizer:
    """``mint config set/unset optimizer.*`` branches.

    Mirrors ``TestMintConfigCLINetworkToggle`` but for the ``optimizer.*``
    namespace (prune / split / merge). Previously covered only at the
    subprocess level by the deleted smoke script.
    """

    def test_set_optimizer_prune_persists(self, tmp_path, monkeypatch):
        result = _invoke_mint_cli(
            "config", "set", "optimizer.prune", "on",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert result.exit_code == 0, result.output
        assert "Set optimizer.prune = on" in result.output
        data = json.loads((tmp_path / "settings.json").read_text())
        assert data == {"optimizer": {"prune": "on"}}

    def test_set_optimizer_invalid_value_rejected(self, tmp_path, monkeypatch):
        result = _invoke_mint_cli(
            "config", "set", "optimizer.prune", "sometimes",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert result.exit_code == 1
        combined = (result.output or "") + (result.stderr or "")
        assert "Invalid value" in combined

    def test_unset_optimizer_key_leaves_others(self, tmp_path, monkeypatch):
        # Seed two keys.
        _invoke_mint_cli(
            "config", "set", "optimizer.prune", "on",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        _invoke_mint_cli(
            "config", "set", "optimizer.split", "off",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )

        result = _invoke_mint_cli(
            "config", "unset", "optimizer.prune",
            tmp_path=tmp_path, monkeypatch=monkeypatch,
        )
        assert result.exit_code == 0, result.output
        assert "Unset optimizer.prune" in result.output

        data = json.loads((tmp_path / "settings.json").read_text())
        assert data == {"optimizer": {"split": "off"}}


class TestMintFeedbackErrors:
    """``mint feedback`` error paths — missing --wrong + unknown capability."""

    def test_missing_wrong_flag_exits_1(self, tmp_path, monkeypatch):
        result = _invoke_mint_cli(
            "feedback", tmp_path=tmp_path, monkeypatch=monkeypatch
        )
        assert result.exit_code == 1
        combined = (result.output or "") + (result.stderr or "")
        assert "requires --wrong" in combined

    def test_unknown_capability_exits_1(self, tmp_path, monkeypatch):
        from src.registry.metrics import MetricsStore

        metrics = MetricsStore()  # in-memory
        try:
            result = _invoke_mint_cli(
                "feedback", "--wrong", "--capability", "nonexistent-capability-xyz",
                tmp_path=tmp_path,
                monkeypatch=monkeypatch,
                infra=(None, None, metrics),
            )
            assert result.exit_code == 1
            combined = (result.output or "") + (result.stderr or "")
            assert "No recent invocation found" in combined
        finally:
            metrics.close()
