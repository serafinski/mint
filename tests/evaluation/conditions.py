"""Evaluation condition semantics.

Centralizes the mapping from user-facing condition names (``full``,
``no_verification``, ``no_optimization``, ``static``) to the operational
behavior the harness should wire into the pipeline.

``no_verification`` disables only the task-time verification gate. It still
runs the optimizer so the ablation isolates Pillar 2 without turning off the
Pillar 3 optimizer path.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.schemas.optimizer_config import OptimizerConfig


@dataclass(frozen=True)
class EvalConditionProfile:
    """Resolved semantics for one evaluation condition."""

    jit_enabled: bool
    verification_enabled: bool
    optimizer_config: OptimizerConfig


_AUTO_OPT = OptimizerConfig(prune="auto", split="auto", merge="auto")
_OFF_OPT = OptimizerConfig.all_off()

_PROFILES: dict[str, EvalConditionProfile] = {
    "full": EvalConditionProfile(
        jit_enabled=True,
        verification_enabled=True,
        optimizer_config=_AUTO_OPT,
    ),
    "no_verification": EvalConditionProfile(
        jit_enabled=True,
        verification_enabled=False,
        optimizer_config=_AUTO_OPT,
    ),
    "no_optimization": EvalConditionProfile(
        jit_enabled=True,
        verification_enabled=True,
        optimizer_config=_OFF_OPT,
    ),
    "static": EvalConditionProfile(
        jit_enabled=False,
        verification_enabled=False,
        optimizer_config=_OFF_OPT,
    ),
}


def get_condition_profile(condition: str) -> EvalConditionProfile:
    """Return the canonical profile for ``condition`` or raise ``KeyError``."""
    return _PROFILES[condition]
