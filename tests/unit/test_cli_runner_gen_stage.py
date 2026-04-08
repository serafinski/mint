"""Unit tests for ``src/cli/runner.py:_compute_gen_stage_diff``.

Regression for the ``-1 artifact(s)`` display bug observed in the MINT CLI
T11 smoke test (2026-04-08), where a verification retry caused
``generated_artifacts`` to drop from 1 → 0 between generator stages,
producing a negative delta in the progress display.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.cli.runner import _compute_gen_stage_diff


def _art(name: str, cap_type: str) -> SimpleNamespace:
    """Build a minimal artifact stand-in with a ``capability_type`` attr."""
    return SimpleNamespace(capability_name=name, capability_type=cap_type)


# ---------------------------------------------------------------------------
# Normal (forward-progress) cases
# ---------------------------------------------------------------------------


class TestComputeGenStageDiffForward:
    def test_no_artifacts_yet(self):
        """prev=0, cur=0 → new=0, stage is skipped."""
        new, prev, detail, label = _compute_gen_stage_diff([], 0)
        assert new == 0
        assert prev == 0
        assert detail is None
        assert label is None

    def test_first_artifact_added(self):
        """prev=0, cur=1 → new=1, stage prints with correct type label."""
        arts = [_art("csv-parser", "mcp_tool")]
        new, prev, detail, label = _compute_gen_stage_diff(arts, 0)
        assert new == 1
        assert prev == 1
        assert detail == "1 artifact(s)"
        assert label == "Generating MCP tool"

    def test_skill_artifact_label(self):
        arts = [_art("my-skill", "skill")]
        _, _, detail, label = _compute_gen_stage_diff(arts, 0)
        assert detail == "1 artifact(s)"
        assert label == "Generating skill"

    def test_agent_artifact_label(self):
        arts = [_art("my-agent", "agent")]
        _, _, detail, label = _compute_gen_stage_diff(arts, 0)
        assert label == "Generating agent"

    def test_second_stage_no_new_artifacts(self):
        """prev=1, cur=1 → new=0, subsequent generator is skipped."""
        arts = [_art("csv-parser", "mcp_tool")]
        new, prev, detail, label = _compute_gen_stage_diff(arts, 1)
        assert new == 0
        assert prev == 1
        assert detail is None
        assert label is None

    def test_second_stage_adds_artifact(self):
        """prev=1, cur=2 → new=1, only the new artifact drives the label."""
        arts = [
            _art("csv-parser", "mcp_tool"),
            _art("my-skill", "skill"),
        ]
        new, prev, detail, label = _compute_gen_stage_diff(arts, 1)
        assert new == 1
        assert prev == 2
        assert detail == "1 artifact(s)"
        # Label picks up the newly-added skill, not the prior mcp_tool
        assert label == "Generating skill"

    def test_multiple_new_same_type(self):
        arts = [_art("s1", "skill"), _art("s2", "skill")]
        new, _, detail, label = _compute_gen_stage_diff(arts, 0)
        assert new == 2
        assert detail == "2 artifact(s)"
        assert label == "Generating skill"

    def test_mixed_types_no_label_override(self):
        """If the newly-added artifacts have mixed types, no override."""
        arts = [_art("s1", "skill"), _art("a1", "agent")]
        new, _, detail, label = _compute_gen_stage_diff(arts, 0)
        assert new == 2
        assert detail == "2 artifact(s)"
        assert label is None  # mixed types → fall back to node-name label


# ---------------------------------------------------------------------------
# Retry (backward/reset) cases — the T11 CLI bug
# ---------------------------------------------------------------------------


class TestComputeGenStageDiffRetryReset:
    def test_verification_cleared_all_artifacts_next_generator_skipped(self):
        """Regression: T11 CLI "-1 artifact(s)" bug.

        Scenario: first attempt generated 1 skill (prev becomes 1), then
        verification failed and reset ``generated_artifacts`` to []. The
        next generator stage sees cur=0, prev=1 → new=-1. The old logic
        printed ``-1 artifact(s)``. The fix: refresh the baseline and skip.
        """
        # After verification reset, generator stage enters with empty arts
        new, prev, detail, label = _compute_gen_stage_diff([], 1)
        assert new == -1  # baseline dropped
        assert prev == 0  # baseline is now refreshed to cur
        assert detail is None  # stage is skipped — no negative display
        assert label is None

    def test_retry_after_reset_first_new_artifact_printed_once(self):
        """After a reset, the next generator that actually adds an artifact
        must correctly show "1 artifact(s)", not "2 artifact(s)" (which it
        would if the baseline hadn't been refreshed to 0).
        """
        # Step 1: verification reset — baseline was 1, now sees []
        new, prev, detail, _ = _compute_gen_stage_diff([], 1)
        assert detail is None  # skipped
        assert prev == 0  # baseline refreshed

        # Step 2: retry's skill generator adds a fresh skill
        retry_arts = [_art("my-skill-v2", "skill")]
        new, prev, detail, label = _compute_gen_stage_diff(retry_arts, prev)
        assert new == 1
        assert prev == 1
        assert detail == "1 artifact(s)"
        assert label == "Generating skill"

    def test_partial_reset_keeps_passing_artifact(self):
        """Verification can partially reset — keep passing, drop failing.
        If 2 artifacts were generated, 1 passed, 1 failed, the retry sees
        cur=1 (the passing one). Next generator that adds a fresh retry
        artifact should show new=1 (not new=2).
        """
        # First pass: 2 artifacts (skill + agent)
        first_pass_arts = [_art("s1", "skill"), _art("a1", "agent")]
        new, prev, _, _ = _compute_gen_stage_diff(first_pass_arts, 0)
        assert new == 2
        assert prev == 2

        # Verification fails only the agent; skill remains
        after_reset = [_art("s1", "skill")]
        new, prev, detail, _ = _compute_gen_stage_diff(after_reset, prev)
        assert new == -1
        assert prev == 1  # baseline now matches the kept artifact
        assert detail is None

        # Retry regenerates the agent
        retry_arts = [_art("s1", "skill"), _art("a1-v2", "agent")]
        new, prev, detail, label = _compute_gen_stage_diff(retry_arts, prev)
        assert new == 1
        assert prev == 2
        assert detail == "1 artifact(s)"
        assert label == "Generating agent"

    def test_reset_to_empty_then_no_regeneration_skipped(self):
        """Edge case: verification resets to [], but subsequent generator
        adds nothing (e.g., all generators are filtered out). Should not
        display anything weird — both stages are skipped.
        """
        new, prev, detail, _ = _compute_gen_stage_diff([], 1)
        assert detail is None
        assert prev == 0

        # Subsequent stage also sees empty
        new, prev, detail, _ = _compute_gen_stage_diff([], prev)
        assert new == 0
        assert detail is None
