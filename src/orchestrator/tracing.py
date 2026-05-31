"""Shared LangFuse trace context and event emission helpers.

Pipeline nodes and runtime helpers call ``emit_langfuse_event`` instead
of duplicating LangFuse SDK logic. ``trace_context`` carries task,
session, user, and trace metadata across runner, pipeline, and worker
thread boundaries; ``submit_with_context`` propagates ContextVar state
into ThreadPool workers.
"""

from __future__ import annotations

import contextvars
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_trace_context_var: ContextVar[dict | None] = ContextVar(
    "mint_trace_context", default=None
)
_trace_context_global: dict = {}
_trace_context_lock = threading.Lock()


@contextmanager
def trace_context(
    *,
    tags: list[str] | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    trace_id: str | None = None,
) -> Iterator[None]:
    """Set LangFuse metadata used by ``emit_langfuse_event`` fallbacks.

    Wrap task/cycle ``pipeline.invoke`` calls in this context manager so
    events emitted outside a LangGraph node context still inherit
    tags/session/user metadata. Inner scopes may also pass ``trace_id``
    once the pipeline trace is known, which lets worker-thread events
    attach directly to the parent trace via LangFuse ``TraceContext``.

    The context is stored both in a ContextVar for same-thread sync/async
    inheritance and in a locked module-level fallback for callers that
    cannot use ``submit_with_context``. Nested contexts merge; inner
    values override outer values for the same key.
    """
    global _trace_context_global

    prev_var = _trace_context_var.get()
    merged = dict(prev_var or {})
    if tags is not None:
        merged["tags"] = list(tags)
    if session_id is not None:
        merged["session_id"] = session_id
    if user_id is not None:
        merged["user_id"] = user_id
    if trace_id is not None:
        merged["trace_id"] = trace_id

    token = _trace_context_var.set(merged)
    with _trace_context_lock:
        prev_global = dict(_trace_context_global)
        _trace_context_global.clear()
        _trace_context_global.update(merged)

    try:
        yield
    finally:
        _trace_context_var.reset(token)
        with _trace_context_lock:
            _trace_context_global.clear()
            _trace_context_global.update(prev_global)


def _read_trace_context() -> dict:
    """Read current trace context (contextvar first, module-global fallback)."""
    ctx = _trace_context_var.get()
    if ctx:
        return dict(ctx)
    with _trace_context_lock:
        return dict(_trace_context_global)


def submit_with_context(pool: Any, fn: Callable, /, *args: Any, **kwargs: Any) -> Future:
    """Submit ``fn`` to ``pool`` while propagating the current ContextVar state.

    Plain ``pool.submit(fn, *args)`` does not carry the parent thread's
    ``ContextVar`` values into the worker. ``copy_context().run(...)``
    snapshots the submitting thread's context and restores it before the
    callable runs, so tracing events read the correct task context
    instead of falling back to the shared module-level safety net.

    Use this helper for ``pool.submit(...)`` calls inside a
    ``trace_context(...)`` scope. The module-level global fallback stays
    in place for callers that cannot use this helper.
    """
    ctx = contextvars.copy_context()
    return pool.submit(ctx.run, fn, *args, **kwargs)


def emit_langfuse_event(
    langfuse_handler: object | None,
    name: str,
    level: str,
    metadata: dict,
) -> None:
    """Emit a named event to LangFuse, linked to the active trace if available.

    Uses ``langfuse_handler.last_trace_id`` to associate the event with the
    preceding LLM invocation span.  Inherits tags/session/user from (in
    order): the LangGraph pipeline config (inside a pipeline node), the
    active ``trace_context`` (set by the outer runner before
    ``pipeline.invoke``). Silently no-ops if LangFuse is unavailable or
    not configured.

    Args:
        langfuse_handler: A ``CallbackHandler`` instance (or ``None``), or
            a raw trace ID string.
        name: Event name (e.g. ``"decomposition.timeout"``).
        level: LangFuse level string (``"DEFAULT"``, ``"WARNING"``, ``"ERROR"``).
        metadata: Arbitrary JSON-serialisable metadata dict.
    """
    try:
        from langfuse import get_client
        from langfuse.types import TraceContext

        # Accept either a CallbackHandler object (exposes .last_trace_id) or a
        # raw trace ID string (e.g. state.trace_id passed directly by nodes
        # that don't hold a handler reference).
        if isinstance(langfuse_handler, str):
            trace_id: str | None = langfuse_handler
        else:
            trace_id = getattr(langfuse_handler, "last_trace_id", None)

        # Fallback to a trace_id supplied by an active ``trace_context``.
        # Inner pipeline scopes set this before spawning workers or nested
        # LLM calls, so those event sites attach to the parent pipeline
        # trace instead of opening standalone wrapper traces.
        if not trace_id:
            fallback_trace_id = _read_trace_context().get("trace_id")
            if fallback_trace_id:
                trace_id = fallback_trace_id

        # Primary: inherit from LangGraph config metadata (active inside
        # pipeline nodes). get_config() returns None in:
        #   - ThreadPoolExecutor worker threads (verification, adversarial)
        #   - Nested LangChain @tool callbacks (sandbox_execute inside
        #     bind_tools LLMs, DeepAgent sub-contexts)
        #   - Post-pipeline callers (harness flush hooks, runners)
        tags: list[str] | None = None
        session_id: str | None = None
        user_id: str | None = None
        try:
            from langgraph.config import get_config as _get_langgraph_config

            parent_config = _get_langgraph_config()
            parent_meta = (parent_config or {}).get("metadata", {})
            tags = parent_meta.get("langfuse_tags")
            session_id = parent_meta.get("langfuse_session_id")
            user_id = parent_meta.get("langfuse_user_id")
        except Exception:  # noqa: BLE001
            pass

        # Fallback: read from the trace_context set by the outer runner.
        # Covers every get_config()-is-empty case above.
        fallback = _read_trace_context()
        if not tags and fallback.get("tags"):
            tags = fallback["tags"]
        if not session_id and fallback.get("session_id"):
            session_id = fallback["session_id"]
        if not user_id and fallback.get("user_id"):
            user_id = fallback["user_id"]

        # Sanitize tags — LangGraph's contextvar metadata serialization can
        # turn ``list[str]`` into a protobuf-style dict like
        # ``{"arrayValue": {}}`` (empty list) or
        # ``{"arrayValue": {"values": [{"stringValue": "T04"}, ...]}}``
        # (populated list). Passing these through directly produces the
        # literal string ``{"arrayValue":{}}`` as a tag in the LangFuse UI.
        # Composition.py applies the same clean_tags pattern — this
        # keeps all standalone-trace emissions equally clean.
        def _clean_tags(raw) -> list[str]:
            if isinstance(raw, list):
                return [str(t) for t in raw if isinstance(t, str)]
            if isinstance(raw, dict):
                # Try to unpack protobuf "arrayValue" form
                arr = raw.get("arrayValue")
                if isinstance(arr, dict):
                    values = arr.get("values")
                    if isinstance(values, list):
                        out: list[str] = []
                        for v in values:
                            if isinstance(v, dict) and "stringValue" in v:
                                out.append(str(v["stringValue"]))
                            elif isinstance(v, str):
                                out.append(v)
                        return out
                return []
            return []

        tags = _clean_tags(tags)
        if isinstance(session_id, dict):
            # Same protobuf-flattening hazard on scalar strings —
            # {"stringValue": "sess-123"} → "sess-123".
            session_id = session_id.get("stringValue") or None
        if isinstance(user_id, dict):
            user_id = user_id.get("stringValue") or None

        lf = get_client()

        # Three emission paths (LangFuse SDK v3):
        # 1. Trace ID known → attach event to that trace via TraceContext.
        # 2. No trace ID but we're inside an active span (contextvar set
        #    by pipeline callback) → `create_event` attaches to current
        #    trace automatically.
        # 3. No context at all → wrap in a fresh span so the event is
        #    discoverable via tags/session in the LangFuse UI.
        #
        # LangFuse SDK v3 removed the old ``lf.trace(...)`` path, so
        # standalone emissions need either ``create_event`` with an
        # explicit trace context or a short wrapper span.
        if trace_id:
            lf.create_event(
                trace_context=TraceContext(trace_id=trace_id),
                name=name,
                level=level,  # type: ignore[arg-type]
                metadata=metadata,
            )
        else:
            # Try attaching to the current trace first. LangChain callback
            # tracing usually provides an active OTel span without setting
            # LangFuse's own ``current_trace_id`` contextvar, so parent-span
            # detection checks both mechanisms before deciding that a wrapper
            # span is required.
            in_active_trace = _has_active_parent_span(lf)

            if in_active_trace:
                lf.create_event(
                    name=name,
                    level=level,  # type: ignore[arg-type]
                    metadata=metadata,
                )
            else:
                # Truly standalone emit — no parent trace in either SDK or
                # OTel context. Wrap in a fresh span so session/tags/user
                # can be applied via ``update_current_trace``. This path is
                # load-bearing for emissions from:
                #   - post-pipeline callers (harness cycle_flush, longitudinal
                #     runners' distribution_shift)
                #   - ThreadPool workers that haven't started any LangFuse /
                #     LangChain runnable yet
                # The tracing ``trace_context`` contextmanager populates
                # tags/session_id/user_id above, which would otherwise be
                # lost here (no active trace to inherit from).
                #
                # LangFuse SDK v3 can serialize an explicit empty tags
                # list as a protobuf marker tag. Build kwargs dynamically
                # so empty tags/session/user fields are omitted.
                with lf.start_as_current_span(name=name) as _span:
                    try:
                        _update_kwargs: dict = {"name": name}
                        if session_id:
                            _update_kwargs["session_id"] = session_id
                        if user_id:
                            _update_kwargs["user_id"] = user_id
                        if tags:
                            _update_kwargs["tags"] = list(tags)
                        lf.update_current_trace(**_update_kwargs)
                    except Exception:  # noqa: BLE001
                        pass
                    lf.create_event(
                        name=name,
                        level=level,  # type: ignore[arg-type]
                        metadata=metadata,
                    )
        lf.flush()
    except Exception:  # noqa: BLE001
        pass


def _has_active_parent_span(lf: object) -> bool:
    """Check whether LangFuse can attach an event to an active parent trace.

    LangFuse SDK v3 uses OTel for context propagation. Two contextvars
    matter:

    1. ``lf.current_trace_id`` — only set when LangFuse SDK's own
       ``start_as_current_span`` is active. Used by direct LangFuse SDK
       users.
    2. OTel's current span — set by any OTel-instrumented runtime,
       including LangChain's auto-tracing callback handler. LangFuse SDK
       attaches to OTel-current when ``create_event`` is called without
       explicit trace_context.

    Checking only LangFuse's own contextvar misses LangChain-wrapped
    contexts: a pipeline node's span is tracked by LangChain via OTel,
    while LangFuse's SDK contextvar remains unset. Returning False there
    forces the caller to wrap in a new span, which OTel then re-parents
    to the LangChain span as a redundant middle layer.

    This function returns True if EITHER mechanism has an active parent,
    so ``lf.create_event`` can attach without wrapping.
    """
    try:
        if getattr(lf, "current_trace_id", None) is not None:
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from opentelemetry import trace as otel_trace

        span = otel_trace.get_current_span()
        ctx = span.get_span_context()
        # ``is_valid`` is True only when the span is an actual tracked
        # span, not the OTel no-op INVALID_SPAN sentinel. ``trace_id != 0``
        # is a defensive redundancy — INVALID_SPAN has trace_id == 0.
        return bool(ctx.is_valid) and ctx.trace_id != 0
    except Exception:  # noqa: BLE001
        return False
