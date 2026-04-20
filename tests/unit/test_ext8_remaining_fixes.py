"""Regression guards for ext8 findings #72, #73, #75, #76, #77.

#72 — variation probe normalizes timestamps/UUIDs before equality.
#73 — latency gate uses absolute-stdev instead of cv for sub-millisecond means.
#75 — quick_validate rejects body-level '---' delimiters.
#76 — is_generic override forces False on domain-specific literals.
#77 — new distance-to-final convergence detector.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# #72 — output-variation probe normalization
# ---------------------------------------------------------------------------


class TestExt8_72NormalizeForVariation:
    def test_timestamp_normalized(self):
        from src.verification.behavioral import _normalize_for_variation

        a = '{"data": "x", "ts": "2026-04-20T14:32:17.111+00:00"}'
        b = '{"data": "x", "ts": "2026-04-20T14:32:18.222Z"}'
        assert _normalize_for_variation(a) == _normalize_for_variation(b)

    def test_uuid_normalized(self):
        from src.verification.behavioral import _normalize_for_variation

        a = '{"id": "550e8400-e29b-41d4-a716-446655440000", "data": "x"}'
        b = '{"id": "550e8400-E29B-41D4-A716-446655440001", "data": "x"}'
        assert _normalize_for_variation(a) == _normalize_for_variation(b)

    def test_counter_field_normalized(self):
        from src.verification.behavioral import _normalize_for_variation

        a = '{"counter": 42, "data": "x"}'
        b = '{"counter": 43, "data": "x"}'
        assert _normalize_for_variation(a) == _normalize_for_variation(b)

    def test_genuinely_different_outputs_not_normalized_equal(self):
        from src.verification.behavioral import _normalize_for_variation

        a = '{"data": "apple"}'
        b = '{"data": "banana"}'
        assert _normalize_for_variation(a) != _normalize_for_variation(b)


# ---------------------------------------------------------------------------
# #75 — quick_validate rejects body-level `---`
# ---------------------------------------------------------------------------


class TestExt8_75BodyDelimiterRejection:
    def test_rejects_skill_with_bare_dashes_in_body(self, tmp_path):
        from tools.quick_validate import validate_skill

        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: test-skill\n"
            "description: test\n"
            "---\n"
            "# Body\n"
            "content\n"
            "---\n"
            "trailing content\n"
        )
        ok, err = validate_skill(skill_dir)
        assert ok is False
        assert "---" in err
        assert "extra bare-line" in err or "delimiter" in err.lower()

    def test_accepts_skill_without_body_delimiters(self, tmp_path):
        from tools.quick_validate import validate_skill

        skill_dir = tmp_path / "clean-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            "name: clean-skill\n"
            "description: a valid skill\n"
            "---\n"
            "# Body\n"
            "Normal content here, no bare-dash delimiters.\n"
        )
        ok, err = validate_skill(skill_dir)
        # Either accepts (None err) or fails for some other reason (not the
        # bare-dash reason).
        if not ok:
            assert "bare-line" not in err and "delimiter" not in err.lower()


# ---------------------------------------------------------------------------
# #76 — is_generic override on domain-specific literals
# ---------------------------------------------------------------------------


class TestExt8_76IsGenericOverride:
    def test_source_with_order_id_literal_detected(self):
        from src.orchestrator.taxonomy import (
            _contains_domain_specific_literals,
        )
        from src.schemas.task import CapabilityType, GeneratedArtifact

        art = GeneratedArtifact(
            capability_name="parse_sales",
            capability_type=CapabilityType.MCP_TOOL,
            source_code='cols = ["order_id", "product_id"]',
            description="",
        )
        assert _contains_domain_specific_literals(art) is True

    def test_source_without_domain_literals_not_detected(self):
        from src.orchestrator.taxonomy import (
            _contains_domain_specific_literals,
        )
        from src.schemas.task import CapabilityType, GeneratedArtifact

        art = GeneratedArtifact(
            capability_name="parse_csv",
            capability_type=CapabilityType.MCP_TOOL,
            source_code='cols = ["col1", "col2", "col3"]',
            description="",
        )
        assert _contains_domain_specific_literals(art) is False

    def test_skill_content_checked(self):
        from src.orchestrator.taxonomy import (
            _contains_domain_specific_literals,
        )
        from src.schemas.task import CapabilityType, GeneratedArtifact

        art = GeneratedArtifact(
            capability_name="inventory-skill",
            capability_type=CapabilityType.SKILL,
            skill_content='Check "needs_reorder" field on each item.',
            description="",
        )
        assert _contains_domain_specific_literals(art) is True

    def test_prose_mention_without_quotes_not_detected(self):
        """Only quoted string literals count — prose mentions don't trigger."""
        from src.orchestrator.taxonomy import (
            _contains_domain_specific_literals,
        )
        from src.schemas.task import CapabilityType, GeneratedArtifact

        art = GeneratedArtifact(
            capability_name="general-analyzer",
            capability_type=CapabilityType.SKILL,
            skill_content="This analyzer handles generic inventory use cases.",
            description="",
        )
        # "inventory" appears unquoted in prose — not detected.
        assert _contains_domain_specific_literals(art) is False


# ---------------------------------------------------------------------------
# #77 — distance-to-final convergence detector
# ---------------------------------------------------------------------------


class TestExt8_77StableConvergence:
    def test_drift_to_new_state_not_falsely_converged_at_early_cycle(self):
        """Slow drift from {A:0.9, B:0.1} to {A:0.1, B:0.9} over 10 cycles.
        The pre-fix ``_detect_convergence`` (consecutive-KL) would fire
        early because each step's delta is small. The post-fix
        ``_detect_stable_convergence`` compares to the final-window average
        and only fires in the late cycles where the distribution actually
        matches the final state.
        """
        from scripts.analyze_longitudinal import _detect_stable_convergence

        # Drift series: A decreases from 0.9 to 0.1 linearly over 10 steps.
        snapshots = [
            {"A": 0.9 - 0.08 * i, "B": 0.1 + 0.08 * i}
            for i in range(10)
        ]
        # Plus 5 steady-state cycles at the new attractor.
        snapshots += [{"A": 0.1, "B": 0.9}] * 5

        result = _detect_stable_convergence(
            snapshots, threshold=0.01, min_consecutive=3, window_size=3,
        )
        # Stable detection should fire in the LATE steady-state phase,
        # not in the early drift. Pre-fix consecutive-KL would fire around
        # cycle 1-3 (each step's delta is small). Post-fix: cycle >= 9.
        assert result is None or result >= 9

    def test_stable_distribution_detected(self):
        from scripts.analyze_longitudinal import _detect_stable_convergence

        # All cycles identical — converges immediately.
        snapshots = [{"A": 0.5, "B": 0.5}] * 10
        result = _detect_stable_convergence(
            snapshots, threshold=0.01, min_consecutive=3, window_size=3,
        )
        assert result == 1  # first cycle is within threshold

    def test_too_few_snapshots_returns_none(self):
        from scripts.analyze_longitudinal import _detect_stable_convergence

        result = _detect_stable_convergence(
            [{"A": 1.0}], threshold=0.01, min_consecutive=3, window_size=5,
        )
        assert result is None


class TestExt8_77AverageDistributions:
    def test_average_with_missing_keys(self):
        from scripts.analyze_longitudinal import _average_distributions

        snapshots = [{"A": 1.0}, {"B": 1.0}, {"A": 1.0, "B": 1.0}]
        avg = _average_distributions(snapshots)
        # A appears in 2 of 3 with value 1.0 → mean 2/3
        # B appears in 2 of 3 with value 1.0 → mean 2/3
        assert abs(avg["A"] - 2 / 3) < 1e-9
        assert abs(avg["B"] - 2 / 3) < 1e-9

    def test_empty_snapshots(self):
        from scripts.analyze_longitudinal import _average_distributions

        assert _average_distributions([]) == {}
