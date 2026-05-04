"""Per-operation optimizer configuration.

Replaces the single ``run_optimizer: bool`` switch with granular per-operation
controls. Each optimizer operation (prune / split / merge) is independently
configurable as ``auto`` (threshold-gated), ``on`` (always), or ``off``
(never).

The evaluation harness and older callers may still pass ``run_optimizer:
bool``. ``OptimizerConfig.from_run_optimizer_bool`` translates that legacy
surface to all-on or all-off so call sites can migrate gradually.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

OperationSetting = Literal["auto", "on", "off"]


class OptimizerConfig(BaseModel):
    """Per-operation enable/disable settings for the optimizer.

    Each operation has three possible states:

    - ``"auto"`` (default): operation runs when
      ``metrics_store.invocation_count_total() >= AUTO_OPTIMIZER_THRESHOLD``.
      This is the evaluation-profile default for optimizer-enabled
      conditions.
    - ``"on"``: operation runs on every invocation regardless of
      invocation count. Useful for debugging or for users who want
      aggressive optimization from day one.
    - ``"off"``: operation never runs. Useful when a user finds a
      specific operation too expensive or wants to isolate behavior.

    The three operations are gated independently, so a short ad-hoc run
    might set ``prune=auto, split=off,
    merge=auto`` (cheap cleanup without the expensive split rewrites).
    """

    prune: OperationSetting = Field(
        default="auto",
        description=(
            "Prune operation control. 'auto' = threshold-gated, "
            "'on' = always run, 'off' = never run."
        ),
    )
    split: OperationSetting = Field(
        default="auto",
        description=(
            "Split operation control. Same semantics as prune. Note that "
            "split is the most expensive operation (~60-120s + ~$0.10-0.15 "
            "per firing) so 'off' is common for short runs."
        ),
    )
    merge: OperationSetting = Field(
        default="auto",
        description="Merge operation control. Same semantics as prune.",
    )

    model_config = {"extra": "forbid"}

    # -- Gating logic ----------------------------------------------------

    def is_enabled(
        self,
        operation: str,
        *,
        invocation_count: int,
        threshold: int,
    ) -> bool:
        """Return whether a given operation should fire this cycle.

        Args:
            operation: One of ``"prune"``, ``"split"``, ``"merge"``.
            invocation_count: Total recorded invocations so far.
            threshold: The ``AUTO_OPTIMIZER_THRESHOLD`` (typically 20 per
                ``src/config.py``). Only consulted when setting is ``"auto"``.

        Raises:
            ValueError: If ``operation`` is not one of the three valid names.
        """
        if operation not in ("prune", "split", "merge"):
            raise ValueError(
                f"Unknown operation '{operation}'; expected one of "
                f"prune / split / merge"
            )
        setting = getattr(self, operation)
        if setting == "on":
            return True
        if setting == "off":
            return False
        # "auto"
        return invocation_count >= threshold

    def any_enabled(self, *, invocation_count: int, threshold: int) -> bool:
        """Return True if at least one operation would fire this cycle.

        This is a convenience helper for callers that already know the current
        invocation count. ``create_pipeline`` cannot use it directly at graph
        build time because the final invocation count is only known when the
        optimizer loop runs.
        """
        return any(
            self.is_enabled(op, invocation_count=invocation_count, threshold=threshold)
            for op in ("prune", "split", "merge")
        )

    # -- Backward compat factories --------------------------------------

    @classmethod
    def all_on(cls) -> OptimizerConfig:
        """Build a config with all three operations forced on.

        Used by the backward-compatibility path when a caller passes
        ``run_optimizer=True``. The
        old ``bool`` semantics of "optimizer on" means all operations run.
        """
        return cls(prune="on", split="on", merge="on")

    @classmethod
    def all_off(cls) -> OptimizerConfig:
        """Build a config with all three operations forced off.

        Used by the backward-compatibility path when a caller passes
        ``run_optimizer=False``. The old ``bool`` semantics of "optimizer
        off" means no operations run.
        """
        return cls(prune="off", split="off", merge="off")

    @classmethod
    def from_run_optimizer_bool(cls, run_optimizer: bool) -> OptimizerConfig:
        """Translate the legacy ``run_optimizer: bool`` parameter.

        Eval harness and older integration tests pass ``run_optimizer=True``
        or ``False`` via :func:`src.orchestrator.pipeline.create_pipeline`.
        This keeps them working unchanged while newer callers can use the
        per-operation surface.
        """
        return cls.all_on() if run_optimizer else cls.all_off()
