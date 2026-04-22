"""Unit tests for src/schemas/evaluation.py."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.schemas.evaluation import (
    BaselineCondition,
    EvaluationRecord,
    FailureReason,
    HumanLabel,
    TaskTier,
)

_BASE = dict(
    run_id="run_001",
    condition="full",
    task_id="T01",
    task_tier="trivial",
    attempt_count=1,
    capability_type="mcp_tool",
    verification_tier_reached=2,
    gate_passed=True,
    output_in_range=True,
    token_cost=500,
    attempt_latency_ms=3200,
    failure_reason="none",
    registry_snapshot_id="snap_abc12345",
    seed=42,
)


class TestEvaluationRecord:
    def test_valid_full_record(self):
        r = EvaluationRecord(**_BASE)
        assert r.run_id == "run_001"
        assert r.condition == "full"
        assert r.success is True

    def test_success_composite(self):
        r = EvaluationRecord(**{**_BASE, "gate_passed": True, "output_in_range": True})
        assert r.success is True

    def test_success_requires_both(self):
        r1 = EvaluationRecord(**{**_BASE, "gate_passed": False, "output_in_range": True})
        assert r1.success is False
        r2 = EvaluationRecord(**{**_BASE, "gate_passed": True, "output_in_range": False})
        assert r2.success is False

    def test_optional_fields_default_to_none(self):
        r = EvaluationRecord(**{**_BASE, "capability_type": None, "human_label": None,
                                "invocation_record_id": None, "verification_tier_reached": None})
        assert r.capability_type is None
        assert r.human_label is None
        assert r.invocation_record_id is None

    def test_is_pilot_defaults_false(self):
        r = EvaluationRecord(**_BASE)
        assert r.is_pilot is False

    def test_timestamp_defaults_to_utc_now(self):
        before = datetime.now(UTC)
        r = EvaluationRecord(**_BASE)
        assert r.timestamp >= before

    def test_extra_fields_forbidden(self):
        with pytest.raises(Exception):
            EvaluationRecord(**_BASE, unexpected_field="oops")

    def test_attempt_count_non_negative(self):
        with pytest.raises(Exception):
            EvaluationRecord(**{**_BASE, "attempt_count": -1})

    def test_token_cost_non_negative(self):
        with pytest.raises(Exception):
            EvaluationRecord(**{**_BASE, "token_cost": -1})

    def test_invalid_condition_rejected(self):
        with pytest.raises(Exception):
            EvaluationRecord(**{**_BASE, "condition": "unknown"})

    def test_invalid_failure_reason_rejected(self):
        with pytest.raises(Exception):
            EvaluationRecord(**{**_BASE, "failure_reason": "not_a_reason"})

    def test_invalid_human_label_rejected(self):
        with pytest.raises(Exception):
            EvaluationRecord(**{**_BASE, "human_label": "wrong"})

    def test_verification_tier_reached_bounds(self):
        EvaluationRecord(**{**_BASE, "verification_tier_reached": 0})
        EvaluationRecord(**{**_BASE, "verification_tier_reached": 3})
        with pytest.raises(Exception):
            EvaluationRecord(**{**_BASE, "verification_tier_reached": 4})

    def test_all_conditions_valid(self):
        for cond in ["full", "no_verification", "no_optimization", "static"]:
            r = EvaluationRecord(**{**_BASE, "condition": cond})
            assert r.condition == cond

    def test_all_tiers_valid(self):
        for tier in ["trivial", "moderate", "complex"]:
            r = EvaluationRecord(**{**_BASE, "task_tier": tier})
            assert r.task_tier == tier

    def test_all_failure_reasons_valid(self):
        for reason in ["none", "generation_failed", "tier1", "tier2", "tier3", "output_rejected", "no_source_code"]:
            r = EvaluationRecord(**{**_BASE, "failure_reason": reason})
            assert r.failure_reason == reason

    def test_all_capability_types_valid(self):
        for ct in ["mcp_tool", "skill", "agent", None]:
            r = EvaluationRecord(**{**_BASE, "capability_type": ct})
            assert r.capability_type == ct


class TestStrEnums:
    def test_baseline_condition_values(self):
        assert BaselineCondition.FULL == "full"
        assert BaselineCondition.NO_VERIFICATION == "no_verification"
        assert BaselineCondition.NO_OPTIMIZATION == "no_optimization"
        assert BaselineCondition.STATIC == "static"
        # Step 18.7 (2026-04-17): LONGITUDINAL added for run_longitudinal.py.
        # Not one of the four proposal ablation conditions — it's a separate
        # sequencing protocol. Analyzers filter by condition explicitly.
        assert BaselineCondition.LONGITUDINAL == "longitudinal"

    def test_longitudinal_accepted_on_evaluation_record(self):
        """run_longitudinal.py writes condition='longitudinal'; this pins
        that the schema accepts the value without raising. Pre-Step-18.7
        this would raise ValidationError and run_longitudinal.py would
        crash on first record_run — a latent bug surfaced by Step 18.7's
        sanity check."""
        rec = EvaluationRecord(
            run_id="longitudinal_test",
            condition="longitudinal",
            task_id="T01",
            task_tier="trivial",
            attempt_count=1,
            gate_passed=True,
            output_in_range=True,
            token_cost=0,
            attempt_latency_ms=100,
            failure_reason="none",
            registry_snapshot_id="snap",
            seed=42,
        )
        assert rec.condition == "longitudinal"

    def test_task_tier_values(self):
        assert TaskTier.TRIVIAL == "trivial"
        assert TaskTier.MODERATE == "moderate"
        assert TaskTier.COMPLEX == "complex"

    def test_failure_reason_values(self):
        assert FailureReason.NONE == "none"
        assert FailureReason.GENERATION_FAILED == "generation_failed"
        assert FailureReason.TIER1 == "tier1"
        assert FailureReason.TIER2 == "tier2"
        assert FailureReason.TIER3 == "tier3"
        assert FailureReason.OUTPUT_REJECTED == "output_rejected"
        assert FailureReason.NO_SOURCE_CODE == "no_source_code"
        # Post-Step-13 additions (2026-04-15): chain-step / agent-execution failure reasons
        # surfaced by the compositional executor's sticky failure meta.
        assert FailureReason.CHAIN_STEP_FAILED == "chain_step_failed"
        assert FailureReason.AGENT_EXECUTION_TIMEOUT == "agent_execution_timeout"
        assert FailureReason.AGENT_EXECUTION_ERROR == "agent_execution_error"

    def test_human_label_values(self):
        assert HumanLabel.CORRECT == "correct"
        assert HumanLabel.CONTAMINATED == "contaminated"
        assert HumanLabel.PARTIAL == "partial"
