"""Shared LangFuse event emission helper for orchestrator nodes.

All nodes that need to log structured events to LangFuse import
``emit_langfuse_event`` from this module instead of duplicating the
implementation.
"""

from __future__ import annotations


def emit_langfuse_event(
    langfuse_handler: object | None,
    name: str,
    level: str,
    metadata: dict,
) -> None:
    """Emit a named event to LangFuse, linked to the active trace if available.

    Uses ``langfuse_handler.last_trace_id`` to associate the event with the
    preceding LLM invocation span.  Silently no-ops if LangFuse is unavailable
    or not configured.

    Args:
        langfuse_handler: A ``CallbackHandler`` instance (or ``None``).
        name: Event name (e.g. ``"decomposition.timeout"``).
        level: LangFuse level string (``"DEFAULT"``, ``"WARNING"``, ``"ERROR"``).
        metadata: Arbitrary JSON-serialisable metadata dict.
    """
    try:
        from langfuse import Langfuse
        from langfuse.types import TraceContext

        trace_id: str | None = getattr(langfuse_handler, "last_trace_id", None)
        lf = Langfuse()
        lf.create_event(
            trace_context=TraceContext(trace_id=trace_id) if trace_id else None,
            name=name,
            level=level,  # type: ignore[arg-type]
            metadata=metadata,
        )
        lf.flush()
    except Exception:  # noqa: BLE001
        pass
