"""Evaluation condition semantics.

Centralizes the mapping from user-facing condition names (``full``,
``no_verification``, ``no_optimization``, ``static``) to the operational
behavior the harness should wire into the pipeline.

The key proposal-alignment fix is that ``no_verification`` disables only the
verification gate. It still runs the optimizer, matching the "JIT without
verification" baseline in ``docs/thesis_proposal.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.schemas.optimizer_config import OptimizerConfig


@dataclass(frozen=True)
class EvalConditionProfile:
    """Resolved semantics for one evaluation condition."""

    name: str
    label: str
    jit_enabled: bool
    verification_enabled: bool
    optimizer_config: OptimizerConfig

    @property
    def optimizer_enabled(self) -> bool:
        return any(
            getattr(self.optimizer_config, op) != "off"
            for op in ("prune", "split", "merge")
        )


_AUTO_OPT = OptimizerConfig(prune="auto", split="auto", merge="auto")
_OFF_OPT = OptimizerConfig.all_off()

_PROFILES: dict[str, EvalConditionProfile] = {
    "full": EvalConditionProfile(
        name="full",
        label="JIT + Verif + Opt",
        jit_enabled=True,
        verification_enabled=True,
        optimizer_config=_AUTO_OPT,
    ),
    "no_verification": EvalConditionProfile(
        name="no_verification",
        label="JIT + Opt",
        jit_enabled=True,
        verification_enabled=False,
        optimizer_config=_AUTO_OPT,
    ),
    "no_optimization": EvalConditionProfile(
        name="no_optimization",
        label="JIT + Verif",
        jit_enabled=True,
        verification_enabled=True,
        optimizer_config=_OFF_OPT,
    ),
    "static": EvalConditionProfile(
        name="static",
        label="Static",
        jit_enabled=False,
        verification_enabled=False,
        optimizer_config=_OFF_OPT,
    ),
}


def get_condition_profile(condition: str) -> EvalConditionProfile:
    """Return the canonical profile for ``condition`` or raise ``KeyError``."""
    return _PROFILES[condition]


def all_condition_profiles() -> tuple[EvalConditionProfile, ...]:
    """Return profiles in the canonical thesis table order."""
    return tuple(_PROFILES[name] for name in ("full", "no_verification", "no_optimization", "static"))
