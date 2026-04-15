from datetime import UTC, datetime

from pydantic import BaseModel, Field


class InvocationRecord(BaseModel):
    """Record of a single capability invocation for metrics tracking."""

    capability_id: str = Field(..., description="ID of the invoked capability")
    task_id: str = Field(..., description="ID of the task that triggered the invocation")
    success: bool = Field(..., description="Whether the invocation succeeded")
    token_cost: int = Field(..., ge=0, description="Token cost of the invocation")
    latency_ms: float = Field(..., ge=0.0, description="Latency in milliseconds")
    error_message: str | None = Field(
        default=None, description="Error message if the invocation failed"
    )
    # Step 4 (B1 Pillar 3): inputs + outputs for failure-case replay.
    # Step 8's split operation pulls recent failures via
    # ``MetricsStore.get_recent_failures`` and feeds them to the Tier 4
    # coverage verifier as real test inputs. Without these two fields
    # the split variants cannot be evaluated against the input
    # subspaces where the parent was failing. Serialization is
    # JSON-as-string by convention — the caller is responsible for
    # producing stable JSON. Old rows from before 2026-04-15 have
    # both fields NULL; the DuckDB migration in metrics.py adds
    # the columns with defaults so old databases keep working.
    input_args: str | None = Field(
        default=None,
        description=(
            "Serialized input arguments passed to the capability "
            "(JSON string by convention). None when not captured."
        ),
    )
    actual_output: str | None = Field(
        default=None,
        description=(
            "Serialized actual output produced by the capability "
            "(JSON string by convention). None when not captured."
        ),
    )
    invoked_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When the invocation occurred",
    )

    model_config = {"extra": "forbid"}


class GranularityMetrics(BaseModel):
    """Computed granularity metrics for the optimizer feedback loop."""

    capability_id: str = Field(..., description="ID of the capability being measured")
    reuse_rate: float = Field(
        ..., ge=0.0, le=1.0, description="Fraction of tasks that reuse this capability"
    )
    success_rate: float = Field(
        ..., ge=0.0, le=1.0, description="Fraction of invocations that succeed"
    )
    token_cost: float = Field(
        ..., ge=0.0, description="Average token cost per invocation"
    )
    maintenance_burden: float = Field(
        ..., ge=0.0, description="Estimated maintenance cost metric"
    )
    computed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When these metrics were computed",
    )
    window_size: int = Field(
        ..., ge=1, description="Number of invocations in the computation window"
    )

    model_config = {"extra": "forbid"}
