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
    # Inputs and outputs are captured for failure-case replay. Split
    # operations pull recent failures via ``MetricsStore.get_recent_failures``
    # and feed them to the coverage verifier as concrete test inputs. Without
    # these fields, split variants cannot be evaluated against the input
    # subspaces where the parent failed. Serialization is JSON-as-string by
    # convention; the caller is responsible for producing stable JSON. Legacy
    # rows may have both fields NULL, and the DuckDB migration in
    # ``src.registry.metrics`` keeps those databases readable.
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
