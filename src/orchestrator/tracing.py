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
    preceding LLM invocation span.  Automatically inherits ``langfuse_tags``
    from the LangGraph pipeline config (if running inside a pipeline node).
    Silently no-ops if LangFuse is unavailable or not configured.

    Args:
        langfuse_handler: A ``CallbackHandler`` instance (or ``None``).
        name: Event name (e.g. ``"decomposition.timeout"``).
        level: LangFuse level string (``"DEFAULT"``, ``"WARNING"``, ``"ERROR"``).
        metadata: Arbitrary JSON-serialisable metadata dict.
    """
    try:
        from langfuse import Langfuse
        from langfuse.types import TraceContext

        # Accept either a CallbackHandler object (exposes .last_trace_id) or a
        # raw trace ID string (e.g. state.trace_id passed directly by nodes
        # that don't hold a handler reference).
        if isinstance(langfuse_handler, str):
            trace_id: str | None = langfuse_handler
        else:
            trace_id = getattr(langfuse_handler, "last_trace_id", None)

        # Inherit tags from the pipeline config if available.
        tags: list[str] | None = None
        session_id: str | None = None
        try:
            from langgraph.config import get_config as _get_langgraph_config

            parent_config = _get_langgraph_config()
            parent_meta = (parent_config or {}).get("metadata", {})
            tags = parent_meta.get("langfuse_tags")
            session_id = parent_meta.get("langfuse_session_id")
        except Exception:  # noqa: BLE001
            pass

        lf = Langfuse()

        # If we have a parent trace_id, attach event to it.
        # Otherwise create a standalone trace with tags so the event
        # is discoverable in LangFuse.
        if trace_id:
            lf.create_event(
                trace_context=TraceContext(trace_id=trace_id),
                name=name,
                level=level,  # type: ignore[arg-type]
                metadata=metadata,
            )
        else:
            trace = lf.trace(
                name=name,
                tags=tags or [],
                session_id=session_id,
                metadata=metadata,
            )
            trace.event(
                name=name,
                level=level,  # type: ignore[arg-type]
                metadata=metadata,
            )
        lf.flush()
    except Exception:  # noqa: BLE001
        pass
