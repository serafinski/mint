"""Shared LangFuse event emission helper for orchestrator nodes.

All nodes that need to log structured events to LangFuse import
``emit_langfuse_event`` from this module instead of duplicating the
implementation.

Step 13.7 tag-propagation fix (2026-04-19). Previously events emitted from
contexts where ``langgraph.config.get_config()`` returned no metadata —
ThreadPoolExecutor workers (verification_node's gate pool, adversarial
pool), LangChain ``@tool`` runtime (``sandbox_execute`` called by
``bind_tools`` LLMs / DeepAgents), and post-pipeline callers
(``flush_task_cycle_state``, longitudinal ``distribution_shift`` event) —
landed in LangFuse as untagged top-level traces, unfilterable by
task/condition/tier. ``trace_context`` is the callers' fix: it sets both
a ContextVar (for same-thread sync/async inheritance) and a module-level
dict guarded by a lock (for cross-thread visibility, since ContextVars
don't propagate to ``ThreadPoolExecutor`` workers by default). Callers
wrap their pipeline.invoke site in it; ``emit_langfuse_event`` reads
from both layers as a fallback when ``get_config()`` returns nothing.
"""

from __future__ import annotations

import contextvars
import threading
from concurrent.futures import Future
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator


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
    """Set LangFuse trace tags/session/user/trace_id for emit_langfuse_event fallback.

    Wrap each task/cycle ``pipeline.invoke`` site (harness, longitudinal
    runners, CLI runner) in this context manager so events emitted from
    contexts where ``langgraph.config.get_config()`` returns nothing —
    ThreadPool workers, LangChain ``@tool`` runtime, post-pipeline
    callers — still inherit tags/session/user.

    Fix #4 (2026-04-19): ``trace_id`` is a later addition used by inner
    wraps (e.g. ``verification_node`` sets it to ``state.trace_id``
    before spawning the gate ThreadPool). When an inner wrap passes a
    trace_id, ThreadPool workers' ``emit_langfuse_event`` calls attach
    events directly to the parent pipeline trace (via ``TraceContext``)
    instead of creating standalone wrapper traces in the LangFuse UI.
    The pipeline trace thus shows all its Pillar-2/3 events nested as
    children, not scattered as siblings — ~80% fewer standalone-trace
    rows. The outer wrap (harness/longitudinal/CLI) typically does NOT
    pass trace_id because the handler hasn't minted one yet; inner
    wraps (verification_node, optimizer's merge/split paths) DO pass it
    because ``handler.last_trace_id`` is already populated by then.

    Sets BOTH a ContextVar (same-thread sync/async inheritance) AND a
    module-level dict (cross-thread, because ContextVars don't propagate
    to ``ThreadPoolExecutor`` workers by default). Nested contexts merge:
    inner values override outer values on the same key.
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

    Security audit finding #104 (2026-04-19, ext10): plain
    ``pool.submit(fn, *args)`` does NOT carry the parent thread's
    ``ContextVar`` values into the worker — workers see whatever
    default the ContextVar declared, then fall through to the
    module-level ``_trace_context_global`` dict. Under concurrent
    ``trace_context(...)`` invocations on different parent threads,
    the worker can observe a global that another thread has wiped-and-
    replaced (the brief window inside ``trace_context``'s critical
    section where ``_trace_context_global`` reflects the OTHER thread's
    context, not the one the worker was spawned under). This causes
    silent cross-task event attribution.

    ``contextvars.copy_context().run(fn, *args, **kwargs)`` snapshots the
    submitting thread's ContextVar state and restores it inside the
    worker before executing. The worker's ``_read_trace_context()``
    then reads from the contextvar (set), not from the global
    fallback (race-prone), eliminating the cross-thread attribution
    drift.

    Use this helper for EVERY ``pool.submit(...)`` inside a
    ``trace_context(...)`` scope. Critical sites:
    - ``src/verification/gate.py``'s tier-2 ThreadPool
    - ``src/verification/adversarial.py``'s probe ThreadPool
    - ``src/orchestrator/decomposition.py``'s decomposer-call ThreadPool
    - ``src/optimizer/operations.py``'s phase-B verify-all ThreadPool

    The module-level global fallback in ``_trace_context_global``
    remains as a safety net for any submit site we haven't migrated;
    once every submit site uses this helper, the global can be
    deprecated (Chapter 7 cleanup).
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

        # Fix #4 (2026-04-19): fallback to trace_context's trace_id.
        # Populated by verification_node + optimizer_node before spawning
        # ThreadPool workers / nested LLM invocations, so inner emit
        # sites attach events to the parent pipeline trace as children
        # (via TraceContext below) instead of creating standalone
        # wrapper traces in the LangFuse UI. See
        # ``docs/langfuse_redundancy_audit_2026_04_19.md`` Fix #4.
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
        # Previous implementation used `lf.trace(...)` which was removed in
        # SDK v3 — standalone events silently dropped. Fixed 2026-04-18
        # during Step 13.7 Batch A probe.
        if trace_id:
            lf.create_event(
                trace_context=TraceContext(trace_id=trace_id),
                name=name,
                level=level,  # type: ignore[arg-type]
                metadata=metadata,
            )
        else:
            # Try attaching to the current (contextvar) trace first.
            #
            # Fix #2 (2026-04-19, docs/langfuse_redundancy_audit_2026_04_19.md):
            # Previously checked ONLY ``lf.current_trace_id`` (LangFuse SDK's
            # own contextvar, set by ``lf.start_as_current_span``). That check
            # missed OTel-set contexts — when LangChain's callback handler
            # auto-traces a pipeline node or tool-call, it sets an active
            # OTel span but does NOT set LangFuse's ``current_trace_id``. So
            # every emit_langfuse_event call inside an active pipeline created
            # a redundant empty wrapper SPAN (input/output=null, latency=0,
            # same name as the event) that OTel then re-parented to the
            # LangChain span. Confirmed empirically across 6 event types
            # (classifier.gap_classified, adversarial.completed, cycle_flush.
            # completed, pillar3.pruned, registry.register_ok,
            # verification.tier_3.pass) — all had such wrappers. Fix:
            # also check OTel context; when EITHER LangFuse SDK OR OTel has
            # an active parent, skip the wrapper and let ``lf.create_event``
            # attach via OTel propagation.
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
                # LangFuse SDK v3 bug (observed 2026-04-19): passing
                # ``tags=[]`` (explicit empty list) to ``update_current_trace``
                # causes the SDK to serialize the empty-list protobuf marker
                # as the literal string ``{"arrayValue":{}}`` and attach it
                # as a tag. Workaround: build the kwargs dict dynamically so
                # empty tags/session_id/user_id are omitted rather than
                # passed empty.
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
    """Check if there's an active trace parent via EITHER mechanism.

    LangFuse SDK v3 uses OTel for context propagation. Two contextvars
    matter:

    1. ``lf.current_trace_id`` — only set when LangFuse SDK's own
       ``start_as_current_span`` is active. Used by direct LangFuse SDK
       users.
    2. OTel's current span — set by any OTel-instrumented runtime,
       including LangChain's auto-tracing callback handler. LangFuse SDK
       attaches to OTel-current when ``create_event`` is called without
       explicit trace_context.

    The narrow check (#1 only) misses LangChain-wrapped contexts — a
    pipeline node's span is tracked by LangChain via OTel, but LangFuse's
    SDK contextvar remains unset. Returning False there forces the
    caller to wrap in a new SPAN, which OTel then re-parents to the
    LangChain span → redundant empty middle layer.

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
