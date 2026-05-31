"""Unit tests for ``src.schemas.optimizer_config.OptimizerConfig``."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.schemas.optimizer_config import OptimizerConfig


class TestOptimizerConfigSchema:
    def test_defaults_all_auto(self):
        cfg = OptimizerConfig()
        assert cfg.prune == "auto"
        assert cfg.split == "auto"
        assert cfg.merge == "auto"

    def test_explicit_values(self):
        cfg = OptimizerConfig(prune="on", split="off", merge="auto")
        assert cfg.prune == "on"
        assert cfg.split == "off"
        assert cfg.merge == "auto"

    def test_invalid_setting_rejected(self):
        with pytest.raises(ValidationError):
            OptimizerConfig(prune="sometimes")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            OptimizerConfig(prune="auto", unknown_field="x")


class TestIsEnabled:
    def test_on_always_true(self):
        cfg = OptimizerConfig(prune="on")
        assert cfg.is_enabled("prune", invocation_count=0, threshold=20) is True
        assert cfg.is_enabled("prune", invocation_count=1000, threshold=20) is True

    def test_off_always_false(self):
        cfg = OptimizerConfig(split="off")
        assert cfg.is_enabled("split", invocation_count=0, threshold=20) is False
        assert cfg.is_enabled("split", invocation_count=1000, threshold=20) is False

    def test_auto_gated_by_threshold(self):
        cfg = OptimizerConfig(merge="auto")
        assert cfg.is_enabled("merge", invocation_count=19, threshold=20) is False
        assert cfg.is_enabled("merge", invocation_count=20, threshold=20) is True
        assert cfg.is_enabled("merge", invocation_count=100, threshold=20) is True

    def test_invalid_operation_raises(self):
        cfg = OptimizerConfig()
        with pytest.raises(ValueError, match="prune / split / merge"):
            cfg.is_enabled("delete", invocation_count=0, threshold=20)


class TestFactories:
    def test_all_on(self):
        cfg = OptimizerConfig.all_on()
        assert cfg.prune == "on"
        assert cfg.split == "on"
        assert cfg.merge == "on"

    def test_all_off(self):
        cfg = OptimizerConfig.all_off()
        assert cfg.prune == "off"
        assert cfg.split == "off"
        assert cfg.merge == "off"

    def test_from_run_optimizer_bool_true(self):
        cfg = OptimizerConfig.from_run_optimizer_bool(True)
        assert cfg == OptimizerConfig.all_on()

    def test_from_run_optimizer_bool_false(self):
        cfg = OptimizerConfig.from_run_optimizer_bool(False)
        assert cfg == OptimizerConfig.all_off()
